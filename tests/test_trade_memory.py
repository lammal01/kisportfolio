"""memory.trade_memory tests, plus RiskManager state surviving a restart
through it. Uses a throwaway SQLite file per test."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from memory.trade_memory import RECENT_TRADES_LIMIT, TradeMemory
from models.decision import Decision
from models.order import OrderFill, OrderResult
from risk.risk_manager import AccountState, RiskLimits, RiskManager

LIMITS = RiskLimits(max_position_pct=0.10, daily_loss_limit_pct=0.03)


@pytest.fixture
def memory(tmp_path):
    m = TradeMemory(tmp_path / "trades.sqlite3")
    yield m
    m.close()


def _decision(**overrides) -> Decision:
    defaults = dict(action="BUY", symbol="005930", quantity=1, confidence=0.7, reason=("r1", "r2"))
    defaults.update(overrides)
    return Decision(**defaults)


def _result(decision_id: str, **overrides) -> OrderResult:
    defaults = dict(
        decision_id=decision_id,
        side="BUY",
        symbol="005930",
        quantity=1,
        status="SUBMITTED",
        reason="accepted",
        order_no="0000000001",
        requested_at="110000",
    )
    defaults.update(overrides)
    return OrderResult(**defaults)


def _fill(**overrides) -> OrderFill:
    defaults = dict(
        order_no="0000000001",
        side="BUY",
        symbol="005930",
        ordered_qty=1,
        filled_qty=1,
        remaining_qty=0,
        rejected_qty=0,
        cancelled=False,
        avg_price=70_000.0,
        order_time="110001",
    )
    defaults.update(overrides)
    return OrderFill(**defaults)


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


# --- history ----------------------------------------------------------------


def test_recent_trades_joins_decision_order_and_fill(memory):
    memory.record_decision("d1", _decision(), {"price": {"stck_prpr": "70000"}})
    memory.record_order(_result("d1"))
    memory.record_fill("d1", _fill())

    [trade] = memory.recent_trades("005930")

    assert trade["action"] == "BUY"
    assert trade["reason"] == ["r1", "r2"]
    assert trade["order_status"] == "SUBMITTED"
    assert trade["fill"] == {"status": "FILLED", "filled_qty": 1, "avg_price": 70_000.0}
    assert "snapshot" not in trade  # raw snapshots never go back into prompts


def test_recent_trades_shows_risk_rejection_reason(memory):
    memory.record_decision("d1", _decision(quantity=99), {})
    memory.record_order(_result("d1", status="REJECTED_BY_RISK", reason="position limit", order_no=None))

    [trade] = memory.recent_trades("005930")

    assert trade["rejected_because"] == "position limit"
    assert "fill" not in trade


def test_recent_trades_newest_first_capped_and_per_symbol(memory):
    for i in range(RECENT_TRADES_LIMIT + 3):
        memory.record_decision(f"d{i}", _decision(quantity=i + 1), {})
    memory.record_decision("other", _decision(symbol="000660"), {})

    trades = memory.recent_trades("005930", limit=100)

    assert len(trades) == RECENT_TRADES_LIMIT
    assert trades[0]["quantity"] == RECENT_TRADES_LIMIT + 3  # newest
    assert all(t["quantity"] != 1 for t in trades)  # oldest dropped


def test_sell_fill_records_realized_pnl(memory):
    memory.record_decision("d1", _decision(action="SELL"), {})
    memory.record_fill("d1", _fill(side="SELL", filled_qty=2, ordered_qty=2, avg_price=72_000.0), cost_basis=70_000.0)

    [trade] = memory.recent_trades("005930")

    assert trade["realized_pnl"] == 4_000.0


def test_buy_fill_has_no_realized_pnl(memory):
    memory.record_decision("d1", _decision(), {})
    memory.record_fill("d1", _fill(), cost_basis=65_000.0)
    assert "realized_pnl" not in memory.recent_trades("005930")[0]


def test_fill_is_upserted_by_order_no(memory):
    memory.record_decision("d1", _decision(quantity=2), {})
    memory.record_fill("d1", _fill(ordered_qty=2, filled_qty=0, remaining_qty=2))
    memory.record_fill("d1", _fill(ordered_qty=2, filled_qty=2, remaining_qty=0))

    [trade] = memory.recent_trades("005930")
    assert trade["fill"]["status"] == "FILLED"


def test_order_requires_recorded_decision(memory):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        memory.record_order(_result("never-recorded"))


# --- daily equity -----------------------------------------------------------


def test_day_start_equity_first_value_of_day_wins(memory):
    assert memory.day_start_equity(10_000_000.0, "20260928") == 10_000_000.0
    # e.g. a restart after losses must not reset the baseline
    assert memory.day_start_equity(9_500_000.0, "20260928") == 10_000_000.0
    assert memory.day_start_equity(9_500_000.0, "20260929") == 9_500_000.0


def test_day_start_equity_survives_reopen(tmp_path):
    path = tmp_path / "trades.sqlite3"
    TradeMemory(path).day_start_equity(10_000_000.0, "20260928")
    assert TradeMemory(path).day_start_equity(9_000_000.0, "20260928") == 10_000_000.0


# --- RiskManager state across restarts ---------------------------------------


def test_executed_decision_id_survives_restart(tmp_path):
    path = tmp_path / "trades.sqlite3"
    RiskManager(limits=LIMITS, store=TradeMemory(path)).mark_executed("d1")

    restarted = RiskManager(limits=LIMITS, store=TradeMemory(path))
    verdict = restarted.evaluate(_decision(), "d1", _account())

    assert not verdict.approved
    assert "이미 실행된" in verdict.reason


def test_emergency_stop_survives_restart_until_released(tmp_path):
    path = tmp_path / "trades.sqlite3"
    RiskManager(limits=LIMITS, store=TradeMemory(path)).engage_emergency_stop("order outcome unknown")

    restarted = RiskManager(limits=LIMITS, store=TradeMemory(path))
    assert restarted.emergency_stop_engaged
    assert restarted.emergency_stop_reason == "order outcome unknown"
    assert not restarted.evaluate(_decision(), "d1", _account()).approved

    restarted.disengage_emergency_stop()
    assert not RiskManager(limits=LIMITS, store=TradeMemory(path)).emergency_stop_engaged


def test_failed_persist_engages_emergency_stop_without_raising():
    store = MagicMock()
    store.load_executed_decision_ids.return_value = set()
    store.load_emergency_stop.return_value = None
    store.save_executed_decision_id.side_effect = OSError("disk full")
    rm = RiskManager(limits=LIMITS, store=store)

    rm.mark_executed("d1")  # must not raise: the order already went out

    assert rm.emergency_stop_engaged
    assert "저장 실패" in rm.emergency_stop_reason
    assert not rm.evaluate(_decision(), "d1", _account()).approved


def test_failed_release_keeps_emergency_stop():
    store = MagicMock()
    store.load_executed_decision_ids.return_value = set()
    store.load_emergency_stop.return_value = "earlier incident"
    store.save_emergency_stop.side_effect = OSError("disk full")
    rm = RiskManager(limits=LIMITS, store=store)

    with pytest.raises(OSError):
        rm.disengage_emergency_stop()
    assert rm.emergency_stop_engaged


def test_store_load_failure_prevents_startup():
    store = MagicMock()
    store.load_executed_decision_ids.side_effect = OSError("corrupt db")
    with pytest.raises(OSError):
        RiskManager(limits=LIMITS, store=store)


# --- main.run_once ----------------------------------------------------------


def test_run_once_skips_ai_while_emergency_stop_engaged(memory):
    import main

    RiskManager(limits=LIMITS, store=memory).engage_emergency_stop("order outcome unknown")
    with patch("main.KisClient"), patch("main.TradingAgent") as agent, patch("main._notify") as notify:
        assert main.run_once("005930", memory=memory) is None
    agent.assert_not_called()
    assert "비상 정지" in notify.call_args.args[0]


def test_release_emergency_stop_clears_persisted_state(memory):
    import main

    RiskManager(limits=LIMITS, store=memory).engage_emergency_stop("order outcome unknown")
    with patch("main._notify"):
        main.release_emergency_stop(memory=memory)
    assert memory.load_emergency_stop() is None


def test_run_once_records_decision_order_and_fill(memory):
    import main
    from broker.kis_client import KisClient

    client = MagicMock(spec=KisClient)
    client.is_real_trading = False
    client.get_current_price.return_value = {"stck_prpr": "70000"}
    client.place_cash_order.return_value = {"ODNO": "0000000007", "KRX_FWDG_ORD_ORGNO": "00950", "ORD_TMD": "110000"}
    client.get_daily_orders.return_value = [
        {
            "odno": "0000000007", "orgn_odno": "0000000000", "sll_buy_dvsn_cd": "01", "pdno": "005930",
            "ord_qty": "2", "tot_ccld_qty": "2", "rmn_qty": "0", "rjct_qty": "0", "cncl_yn": "N",
            "avg_prvs": "71000", "ord_tmd": "110000",
        }
    ]
    agent = MagicMock()
    agent.decide.return_value = _decision(action="SELL", quantity=2)

    with patch("main.KisClient", return_value=client), \
         patch("main.build_account_state", return_value=_account(held_quantity=2, avg_cost=70_000.0)), \
         patch("main.TradingAgent", return_value=agent), \
         patch("main.RiskManager", side_effect=lambda store: RiskManager(limits=LIMITS, store=store)), \
         patch("main._notify"):
        result = main.run_once("005930", memory=memory)

    assert result.status == "SUBMITTED"
    [trade] = memory.recent_trades("005930")
    assert trade["order_status"] == "SUBMITTED"
    assert trade["fill"]["status"] == "FILLED"
    assert trade["realized_pnl"] == 2_000.0
    assert result.decision_id in memory.load_executed_decision_ids()


def test_run_once_skips_while_paused(memory):
    import main

    memory.save_pause("telegram /pause: lunch")
    with patch("main.KisClient"), patch("main.TradingAgent") as agent, patch("main._notify"):
        assert main.run_once("005930", memory=memory) is None
    agent.assert_not_called()
