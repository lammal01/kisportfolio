"""AI trading agent: analyzes a market data snapshot and returns a
structured BUY / SELL / HOLD decision (models.decision.Decision).

Uses the Claude API (Anthropic) with a JSON-schema structured output
(output_config.format) so the raw response is guaranteed well-formed
JSON -- models.decision.Decision.from_dict then re-validates the actual
values (ranges, BUY/SELL quantity > 0, etc), since a schema-conformant
response can still be semantically wrong. Never trust the shape alone.

This module must never call broker.order_executor directly. Its output
always passes through risk.risk_manager first.

Cost control (spec section 6): runs at output_config.effort="low" since
this is a bounded classification-style task, not open-ended reasoning.
Whether to call this at all is market.signal_filter's decision, not
this module's -- it only wraps a single call and its parsing.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import anthropic

from config.settings import Settings, get_settings
from models.decision import Decision, DecisionParseError

logger = logging.getLogger(__name__)

_DECISION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "symbol": {"type": "string"},
        "quantity": {"type": "integer"},
        "confidence": {"type": "number"},
        "reason": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action", "symbol", "quantity", "confidence", "reason"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a disciplined trading analyst for the Korean stock market (KRX). "
    "Given a market data snapshot for one symbol, decide BUY, SELL, or HOLD. "
    "Quantity must be 0 for HOLD and a positive integer for BUY/SELL. "
    "confidence is a number between 0 and 1. reason is a short list of the "
    "concrete factors that drove the decision. Do not invent data that "
    "isn't in the snapshot you were given. The snapshot may include "
    "recent_trades: your own last few decisions on this symbol and how they "
    "turned out (order status, fill, realized_pnl). Treat them as context "
    "only -- a past decision is not a reason to repeat it. "
    "max_buy_value is the most a single BUY may cost (quantity * price); "
    "larger orders are rejected by an independent risk manager. It already "
    "accounts for available cash and the total-investment limit, so when it "
    "is below one share's price, a BUY is impossible -- do not propose one. "
    "Write every reason item in Korean (한국어), concise, keeping numbers "
    "and tickers as-is."
)


class TradingAgentError(RuntimeError):
    """Raised when the Claude API call itself fails (network/auth/etc, not
    a validation error -- for those see models.decision.DecisionParseError)."""

AGGRESSIVE_ADDENDUM = (
    " [공격 모드: 모의투자 한정, 기간 한정] 이번 기간에는 수익 극대화가 목표다. "
    "신호가 확인되면 HOLD보다 BUY/SELL을 우선 고려하고, 확신이 높으면 "
    "max_buy_value에 가깝게 크게 매수해도 된다. 상승 추세·거래량 급증 종목을 "
    "적극 추종하고, 현금을 놀리지 말며, 약세 종목은 빠르게 정리해 강한 종목으로 "
    "옮겨라. 단 스냅샷에 없는 데이터를 지어내지 말고, max_buy_value 한도와 "
    "리스크 매니저의 거절은 그대로 적용된다."
)


def _system_prompt(settings: Settings, today: date | None = None) -> str:
    until = getattr(settings, "aggressive_until", None)
    if until:
        try:
            if (today or date.today()) <= date.fromisoformat(until):
                return SYSTEM_PROMPT + AGGRESSIVE_ADDENDUM
        except ValueError:
            pass  # malformed date -> stay on the default prompt
    return SYSTEM_PROMPT


class TradingAgent:
    def __init__(
        self,
        settings: Settings | None = None,
        client: anthropic.Anthropic | None = None,
        model: str | None = None,
    ):
        settings = settings or get_settings()
        if not settings.anthropic_api_key:
            raise TradingAgentError(
                "ANTHROPIC_API_KEY is missing -- refusing to call the AI agent."
            )
        self._client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = model or settings.anthropic_model
        self._settings = settings

    def decide(self, symbol: str, market_snapshot: dict) -> Decision:
        """market_snapshot: whatever market.data_collector produced for
        this symbol (current price, indicators, etc) -- passed through
        as-is to the model as JSON."""
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=_system_prompt(self._settings),
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"symbol": symbol, "market_snapshot": market_snapshot},
                            ensure_ascii=False,
                        ),
                    }
                ],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": _DECISION_JSON_SCHEMA},
                },
            )
        except Exception as exc:  # anthropic SDK raises its own exception hierarchy
            raise TradingAgentError(f"Claude API call failed: {exc}") from exc

        usage = response.usage
        logger.info(
            "AI decision call: model=%s input_tokens=%s output_tokens=%s",
            self._model,
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
        )

        text_blocks = [block.text for block in response.content if block.type == "text"]
        if not text_blocks:
            raise DecisionParseError(
                f"AI response had no text block (stop_reason={response.stop_reason!r})"
            )
        raw = text_blocks[0]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DecisionParseError(f"AI response was not valid JSON: {raw!r}") from exc

        return Decision.from_dict(data)
