"""models.decision.Decision.from_dict tests -- pure parsing/validation,
no network or AI calls involved."""

from __future__ import annotations

import pytest

from models.decision import Decision, DecisionParseError

VALID_BUY = {
    "action": "BUY",
    "symbol": "005930",
    "quantity": 2,
    "confidence": 0.72,
    "reason": ["거래량 증가", "단기 추세 개선"],
}


def test_valid_buy_decision_parses():
    decision = Decision.from_dict(VALID_BUY)
    assert decision.action == "BUY"
    assert decision.symbol == "005930"
    assert decision.quantity == 2
    assert decision.confidence == 0.72
    assert decision.reason == ("거래량 증가", "단기 추세 개선")


def test_valid_hold_decision_allows_zero_quantity():
    decision = Decision.from_dict({**VALID_BUY, "action": "HOLD", "quantity": 0})
    assert decision.action == "HOLD"
    assert decision.quantity == 0


def test_optional_fields_default_to_none():
    decision = Decision.from_dict(VALID_BUY)
    assert decision.risk_score is None
    assert decision.stop_loss is None
    assert decision.take_profit is None
    assert decision.invalidation_condition is None


def test_optional_fields_parsed_when_present():
    data = {
        **VALID_BUY,
        "risk_score": 0.3,
        "stop_loss": 78000,
        "take_profit": 90000,
        "invalidation_condition": "종가가 75000 아래로 마감",
    }
    decision = Decision.from_dict(data)
    assert decision.risk_score == 0.3
    assert decision.stop_loss == 78000.0
    assert decision.take_profit == 90000.0
    assert decision.invalidation_condition == "종가가 75000 아래로 마감"


def test_non_dict_input_raises():
    with pytest.raises(DecisionParseError):
        Decision.from_dict("not a dict")  # type: ignore[arg-type]


def test_missing_required_field_raises():
    data = {k: v for k, v in VALID_BUY.items() if k != "confidence"}
    with pytest.raises(DecisionParseError):
        Decision.from_dict(data)


def test_invalid_action_raises():
    with pytest.raises(DecisionParseError):
        Decision.from_dict({**VALID_BUY, "action": "SHORT"})


def test_negative_quantity_raises():
    with pytest.raises(DecisionParseError):
        Decision.from_dict({**VALID_BUY, "quantity": -1})


def test_zero_quantity_for_buy_raises():
    with pytest.raises(DecisionParseError):
        Decision.from_dict({**VALID_BUY, "quantity": 0})


def test_non_integer_quantity_raises():
    with pytest.raises(DecisionParseError):
        Decision.from_dict({**VALID_BUY, "quantity": 2.5})


def test_confidence_out_of_range_raises():
    with pytest.raises(DecisionParseError):
        Decision.from_dict({**VALID_BUY, "confidence": 1.5})


def test_negative_confidence_raises():
    with pytest.raises(DecisionParseError):
        Decision.from_dict({**VALID_BUY, "confidence": -0.1})


def test_empty_reason_list_raises():
    with pytest.raises(DecisionParseError):
        Decision.from_dict({**VALID_BUY, "reason": []})


def test_non_string_reason_items_raise():
    with pytest.raises(DecisionParseError):
        Decision.from_dict({**VALID_BUY, "reason": ["ok", 123]})


def test_empty_symbol_raises():
    with pytest.raises(DecisionParseError):
        Decision.from_dict({**VALID_BUY, "symbol": ""})
