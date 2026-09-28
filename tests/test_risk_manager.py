"""risk.risk_manager tests -- highest priority suite in this project.

Pure logic, no network/KIS calls: AccountState is constructed by hand for
each case.
"""

from __future__ import annotations

from models.decision import Decision
from risk.risk_manager import AccountState, RiskLimits, RiskManager

LIMITS = RiskLimits(max_position_pct=0.10, daily_loss_limit_pct=0.03)


def _decision(**overrides) -> Decision:
    defaults = dict(
        action="BUY",
        symbol="005930",
        quantity=1,
        confidence=0.7,
        reason=("test",),
    )
    defaults.update(overrides)
    return Decision(**defaults)


def _account(**overrides) -> AccountState:
    defaults = dict(
        symbol="005930",
        total_equity=10_000_000.0,
        day_start_equity=10_000_000.0,
        current_price=70_000.0,
        held_quantity=0,
        open_order_count=0,
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def test_buy_within_limits_is_approved():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(_decision(quantity=10), "d1", _account())
    assert verdict.approved
    assert verdict.reason == "within limits"


def test_hold_is_never_executable():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(_decision(action="HOLD", quantity=0), "d1", _account())
    assert not verdict.approved
    assert "HOLD" in verdict.reason


def test_buy_exceeding_position_limit_is_rejected():
    rm = RiskManager(limits=LIMITS)
    # 20 * 70_000 = 1_400_000 > 10% of 10_000_000 = 1_000_000 position cap.
    verdict = rm.evaluate(_decision(quantity=20), "d1", _account())
    assert not verdict.approved
    assert "position limit" in verdict.reason


def test_sell_exceeding_held_quantity_is_rejected():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(
        _decision(action="SELL", quantity=5), "d1", _account(held_quantity=2)
    )
    assert not verdict.approved
    assert "exceeds held quantity" in verdict.reason


def test_sell_within_held_quantity_is_approved():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(
        _decision(action="SELL", quantity=2), "d1", _account(held_quantity=5)
    )
    assert verdict.approved


def test_duplicate_decision_id_is_rejected():
    rm = RiskManager(limits=LIMITS)
    first = rm.evaluate(_decision(quantity=1), "d1", _account())
    assert first.approved
    rm.mark_executed("d1")

    second = rm.evaluate(_decision(quantity=1), "d1", _account())
    assert not second.approved
    assert "duplicate" in second.reason


def test_unmarked_decision_id_can_be_reevaluated():
    rm = RiskManager(limits=LIMITS)
    first = rm.evaluate(_decision(quantity=1), "d1", _account())
    assert first.approved
    # mark_executed() was never called (e.g. order submission failed) --
    # the same decision_id must still be usable, not permanently locked out.
    second = rm.evaluate(_decision(quantity=1), "d1", _account())
    assert second.approved


def test_daily_loss_limit_blocks_new_buy():
    rm = RiskManager(limits=LIMITS)
    # equity dropped from 10M to 9.6M -> -4% > 3% limit
    account = _account(total_equity=9_600_000.0, day_start_equity=10_000_000.0)
    verdict = rm.evaluate(_decision(action="BUY", quantity=1), "d1", account)
    assert not verdict.approved
    assert "daily loss" in verdict.reason


def test_daily_loss_limit_still_allows_sell():
    rm = RiskManager(limits=LIMITS)
    account = _account(
        total_equity=9_600_000.0, day_start_equity=10_000_000.0, held_quantity=5
    )
    verdict = rm.evaluate(_decision(action="SELL", quantity=1), "d1", account)
    assert verdict.approved


def test_daily_loss_exactly_at_limit_blocks_buy():
    rm = RiskManager(limits=LIMITS)
    # exactly -3% -- boundary is inclusive (>=), fail-closed at the edge.
    account = _account(total_equity=9_700_000.0, day_start_equity=10_000_000.0)
    verdict = rm.evaluate(_decision(action="BUY", quantity=1), "d1", account)
    assert not verdict.approved


def test_emergency_stop_blocks_everything():
    rm = RiskManager(limits=LIMITS)
    rm.engage_emergency_stop("manual halt for testing")
    verdict = rm.evaluate(_decision(quantity=1), "d1", _account())
    assert not verdict.approved
    assert "emergency stop" in verdict.reason


def test_emergency_stop_can_be_disengaged():
    rm = RiskManager(limits=LIMITS)
    rm.engage_emergency_stop("manual halt for testing")
    rm.disengage_emergency_stop()
    verdict = rm.evaluate(_decision(quantity=1), "d1", _account())
    assert verdict.approved


def test_invalid_current_price_is_rejected():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(_decision(quantity=1), "d1", _account(current_price=0.0))
    assert not verdict.approved
    assert "current_price" in verdict.reason


def test_invalid_total_equity_is_rejected():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(_decision(quantity=1), "d1", _account(total_equity=0.0))
    assert not verdict.approved
    assert "total_equity" in verdict.reason


def test_limits_default_from_settings(monkeypatch):
    monkeypatch.setenv("RISK_MAX_POSITION_PCT", "0.25")
    monkeypatch.setenv("RISK_DAILY_LOSS_LIMIT_PCT", "0.05")
    from config.settings import get_settings

    limits = RiskLimits.from_settings(get_settings())
    assert limits.max_position_pct == 0.25
    assert limits.daily_loss_limit_pct == 0.05


def test_symbol_mismatch_is_rejected():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(_decision(symbol="000660"), "d1", _account(symbol="005930"))
    assert not verdict.approved
    assert "does not match" in verdict.reason


def test_open_order_for_symbol_blocks_new_order():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(_decision(), "d1", _account(open_order_count=1))
    assert not verdict.approved
    assert "unfilled order" in verdict.reason


def test_unknown_open_order_state_is_rejected():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(_decision(), "d1", _account(open_order_count=None))
    assert not verdict.approved
    assert "unknown" in verdict.reason
