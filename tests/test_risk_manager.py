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
        stock_value=0.0,
        buying_power=10_000_000.0,
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def test_buy_within_limits_is_approved():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(_decision(quantity=10), "d1", _account())
    assert verdict.approved
    assert verdict.reason == "한도 이내"


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
    assert "포지션 한도" in verdict.reason


def test_sell_exceeding_held_quantity_is_rejected():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(
        _decision(action="SELL", quantity=5), "d1", _account(held_quantity=2)
    )
    assert not verdict.approved
    assert "보유 수량" in verdict.reason


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
    assert "중복" in second.reason


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
    assert "당일 손실" in verdict.reason


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
    assert "비상 정지" in verdict.reason


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
    assert "다름" in verdict.reason


def test_open_order_for_symbol_blocks_new_order():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(_decision(), "d1", _account(open_order_count=1))
    assert not verdict.approved
    assert "미체결 주문" in verdict.reason


def test_unknown_open_order_state_is_rejected():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(_decision(), "d1", _account(open_order_count=None))
    assert not verdict.approved
    assert "확인하지 못해" in verdict.reason


# --- total exposure limit and 주문가능금액 --------------------------------------
# LIMITS: position 10% per order, total exposure defaults to 90% of equity.


def test_buy_exceeding_total_exposure_is_rejected():
    rm = RiskManager(limits=LIMITS)
    # Holding 8_500_000 already; 10 * 70_000 = 700_000 more -> 9_200_000 > 9_000_000.
    verdict = rm.evaluate(_decision(quantity=10), "d1", _account(stock_value=8_500_000.0))
    assert not verdict.approved
    assert "총 투자 한도" in verdict.reason


def test_buy_exactly_at_total_exposure_limit_is_approved():
    rm = RiskManager(limits=LIMITS)
    # 8_300_000 + 700_000 = 9_000_000 == the 90% cap.
    verdict = rm.evaluate(_decision(quantity=10), "d1", _account(stock_value=8_300_000.0))
    assert verdict.approved


def test_over_invested_account_blocks_buy_but_allows_sell():
    rm = RiskManager(limits=LIMITS)
    # Holdings worth more than total equity (cash is negative): the real
    # state of the paper account on 2026-10-06.
    over = _account(stock_value=12_000_000.0, held_quantity=5, buying_power=0.0)
    assert not rm.evaluate(_decision(quantity=1), "d1", over).approved
    assert rm.evaluate(_decision(action="SELL", quantity=5), "d2", over).approved


def test_buy_exceeding_buying_power_is_rejected():
    rm = RiskManager(limits=LIMITS)
    # 10 * 70_000 = 700_000 > 500_000 available.
    verdict = rm.evaluate(_decision(quantity=10), "d1", _account(buying_power=500_000.0))
    assert not verdict.approved
    assert "주문가능금액" in verdict.reason


def test_buy_with_zero_buying_power_is_rejected():
    rm = RiskManager(limits=LIMITS)
    verdict = rm.evaluate(_decision(quantity=1), "d1", _account(buying_power=0.0))
    assert not verdict.approved
    assert "주문가능금액" in verdict.reason


def test_buy_is_rejected_when_exposure_or_cash_is_unknown():
    rm = RiskManager(limits=LIMITS)
    for field_name in ("stock_value", "buying_power"):
        verdict = rm.evaluate(_decision(quantity=1), f"d-{field_name}", _account(**{field_name: None}))
        assert not verdict.approved, field_name
        assert "확인하지 못해" in verdict.reason


def test_sell_does_not_need_exposure_or_cash_figures():
    rm = RiskManager(limits=LIMITS)
    account = _account(held_quantity=5, stock_value=None, buying_power=None)
    assert rm.evaluate(_decision(action="SELL", quantity=5), "d1", account).approved


def test_nan_or_negative_figures_reject_buy():
    rm = RiskManager(limits=LIMITS)
    nan = float("nan")
    assert not rm.evaluate(_decision(quantity=1), "d1", _account(buying_power=nan)).approved
    assert not rm.evaluate(_decision(quantity=1), "d2", _account(stock_value=nan)).approved
    assert not rm.evaluate(_decision(quantity=1), "d3", _account(stock_value=-1.0)).approved
    assert not rm.evaluate(_decision(quantity=1), "d4", _account(buying_power=-5_000_000.0)).approved


def test_max_buy_value_is_the_smallest_cap():
    rm = RiskManager(limits=LIMITS)
    # position cap 1_000_000; exposure room 9_000_000 - 8_700_000 = 300_000;
    # cash 2_000_000 -> exposure room wins.
    assert rm.max_buy_value(_account(stock_value=8_700_000.0, buying_power=2_000_000.0)) == 300_000.0
    # cash is the smallest.
    assert rm.max_buy_value(_account(stock_value=0.0, buying_power=400_000.0)) == 400_000.0
    # position cap is the smallest.
    assert rm.max_buy_value(_account()) == 1_000_000.0


def test_max_buy_value_is_zero_when_blocked_or_unknown():
    rm = RiskManager(limits=LIMITS)
    assert rm.max_buy_value(_account(stock_value=12_000_000.0)) == 0.0  # over-invested
    assert rm.max_buy_value(_account(buying_power=-100.0)) == 0.0
    assert rm.max_buy_value(_account(stock_value=None)) == 0.0
    assert rm.max_buy_value(_account(buying_power=None)) == 0.0


def test_max_buy_value_never_exceeds_what_evaluate_accepts():
    rm = RiskManager(limits=LIMITS)
    account = _account(stock_value=8_700_000.0, buying_power=2_000_000.0)
    budget = rm.max_buy_value(account)  # 300_000 -> 4 shares at 70_000
    shares = int(budget // account.current_price)
    assert rm.evaluate(_decision(quantity=shares), "ok", account).approved
    assert not rm.evaluate(_decision(quantity=shares + 1), "over", account).approved


def test_total_exposure_limit_comes_from_settings(monkeypatch):
    from config.settings import Settings

    monkeypatch.setenv("RISK_MAX_TOTAL_EXPOSURE_PCT", "0.5")
    assert RiskLimits.from_settings(Settings()).max_total_exposure_pct == 0.5
    monkeypatch.delenv("RISK_MAX_TOTAL_EXPOSURE_PCT")
    assert RiskLimits.from_settings(Settings()).max_total_exposure_pct == 0.90
