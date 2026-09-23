"""agents.trading_agent.TradingAgent tests. The Anthropic client is a
hand-rolled fake injected via the constructor -- no real API key or
network access is needed to run this suite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from agents.trading_agent import TradingAgent, TradingAgentError
from config.settings import Settings
from models.decision import DecisionParseError


def _settings(**overrides) -> Settings:
    defaults = dict(anthropic_api_key="test-anthropic-key", anthropic_model="claude-opus-5")
    defaults.update(overrides)
    return Settings(**defaults)


@dataclass
class _FakeMessages:
    text: str
    input_tokens: int = 10
    output_tokens: int = 5
    stop_reason: str = "end_turn"
    raise_error: Exception | None = None
    captured_kwargs: dict = field(default_factory=dict)

    def create(self, **kwargs):
        self.captured_kwargs = kwargs
        if self.raise_error is not None:
            raise self.raise_error
        content = [SimpleNamespace(type="text", text=self.text)] if self.text is not None else []
        usage = SimpleNamespace(input_tokens=self.input_tokens, output_tokens=self.output_tokens)
        return SimpleNamespace(content=content, usage=usage, stop_reason=self.stop_reason)


class _FakeAnthropicClient:
    def __init__(self, messages: _FakeMessages):
        self.messages = messages


def _agent_with_response(text: str | None, **message_kwargs) -> tuple[TradingAgent, _FakeMessages]:
    messages = _FakeMessages(text=text, **message_kwargs)
    client = _FakeAnthropicClient(messages)
    agent = TradingAgent(settings=_settings(), client=client)
    return agent, messages


def test_missing_api_key_raises():
    with pytest.raises(TradingAgentError):
        TradingAgent(settings=_settings(anthropic_api_key=None), client=_FakeAnthropicClient(_FakeMessages("{}")))


def test_valid_response_parses_into_decision():
    payload = {
        "action": "BUY",
        "symbol": "005930",
        "quantity": 2,
        "confidence": 0.72,
        "reason": ["거래량 증가"],
    }
    agent, messages = _agent_with_response(json.dumps(payload))

    decision = agent.decide("005930", {"stck_prpr": "82100"})

    assert decision.action == "BUY"
    assert decision.quantity == 2
    # request actually asked for structured output tied to our schema, at low effort
    fmt = messages.captured_kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert messages.captured_kwargs["output_config"]["effort"] == "low"
    sent_user_message = json.loads(messages.captured_kwargs["messages"][0]["content"])
    assert sent_user_message["symbol"] == "005930"


def test_malformed_json_raises_decision_parse_error():
    agent, _ = _agent_with_response("not valid json")

    with pytest.raises(DecisionParseError):
        agent.decide("005930", {})


def test_invalid_decision_shape_raises_decision_parse_error():
    agent, _ = _agent_with_response(json.dumps({"action": "MAYBE", "symbol": "005930"}))

    with pytest.raises(DecisionParseError):
        agent.decide("005930", {})


def test_no_text_block_raises_decision_parse_error():
    agent, _ = _agent_with_response(None, stop_reason="max_tokens")

    with pytest.raises(DecisionParseError):
        agent.decide("005930", {})


def test_anthropic_call_failure_raises_trading_agent_error():
    agent, _ = _agent_with_response("{}", raise_error=RuntimeError("network down"))

    with pytest.raises(TradingAgentError):
        agent.decide("005930", {})
