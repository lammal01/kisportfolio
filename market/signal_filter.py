"""Decides whether a symbol's latest data warrants an LLM call.

This is the cost-control layer: most cycles should be filtered out here
using cheap Python-computed indicators (market.indicators), and only
genuinely interesting events reach agents.trading_agent.

All state (last AI call per symbol, today's call count) is read from
memory.trade_memory, so a restart neither re-triggers everything nor
resets the daily limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from market.indicators import Indicators
from memory.trade_memory import TradeMemory

PRICE_MOVE_PCT = 2.0  # move since the last AI call that's worth a new look
VOLUME_SURGE_RATIO = 2.0  # today's volume vs. usual for this time of day
VOLUME_MIN_ELAPSED_MINUTES = 30  # too noisy right after the open
RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0
HELD_REVIEW_MINUTES = 60  # held positions get looked at at least this often
COOLDOWN_MINUTES = 20  # never ask about the same symbol more often than this


@dataclass(frozen=True)
class SignalCheck:
    call_ai: bool
    triggers: tuple[str, ...]  # why it would call (Korean, shown to the user/AI)
    blocked_by: str | None = None  # why it won't, despite triggers


class SignalFilter:
    def __init__(self, memory: TradeMemory, daily_call_limit: int):
        self._memory = memory
        self._limit = daily_call_limit

    def check(self, symbol: str, ind: Indicators, held_quantity: int, now: datetime) -> SignalCheck:
        """now: KST, timezone-aware."""
        triggers: list[str] = []
        last = self._memory.last_ai_call(symbol)
        last_at, last_price = last if last else (None, None)
        today_first = last_at is None or last_at.date() != now.date()

        if today_first:
            triggers.append("오늘 첫 점검")
        if last_price and not today_first:
            move = (ind.price - last_price) / last_price * 100
            if abs(move) >= PRICE_MOVE_PCT:
                triggers.append(f"마지막 AI 판단 이후 {move:+.1f}% 변동")
        elapsed = now.hour * 60 + now.minute - 9 * 60
        if (
            ind.volume_ratio is not None
            and elapsed >= VOLUME_MIN_ELAPSED_MINUTES
            and ind.volume_ratio >= VOLUME_SURGE_RATIO
        ):
            triggers.append(f"거래량 급증 (평소 대비 {ind.volume_ratio:.1f}배)")
        if ind.ma_cross == "golden":
            triggers.append("골든크로스 (MA5가 MA20 상향 돌파)")
        elif ind.ma_cross == "dead":
            triggers.append("데드크로스 (MA5가 MA20 하향 돌파)")
        if ind.rsi14 is not None and ind.rsi14 <= RSI_OVERSOLD:
            triggers.append(f"RSI 과매도 ({ind.rsi14:.0f})")
        elif ind.rsi14 is not None and ind.rsi14 >= RSI_OVERBOUGHT:
            triggers.append(f"RSI 과매수 ({ind.rsi14:.0f})")
        if held_quantity > 0 and (last_at is None or now - last_at >= timedelta(minutes=HELD_REVIEW_MINUTES)):
            triggers.append(f"보유 종목 정기 점검 ({held_quantity}주)")

        if not triggers:
            return SignalCheck(False, ())
        if last_at is not None and now - last_at < timedelta(minutes=COOLDOWN_MINUTES):
            return SignalCheck(False, tuple(triggers), f"같은 종목 {COOLDOWN_MINUTES}분 쿨다운")
        if self._memory.ai_call_count(now.strftime("%Y%m%d")) >= self._limit:
            return SignalCheck(False, tuple(triggers), f"오늘 AI 호출 상한 {self._limit}회 도달")
        return SignalCheck(True, tuple(triggers))
