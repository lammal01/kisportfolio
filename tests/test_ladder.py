"""market.ladder (분할매수/손절/익절) + its TradeMemory persistence and
main.PaperTradingLoop wiring."""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from config.settings import Settings
from market import ladder
from market.indicators import Indicators
from memory.trade_memory import TradeMemory
from models.decision import Decision
from models.order import OrderResult

KST = ZoneInfo("Asia/Seoul")
MON_1030 = datetime(2026, 9, 28, 10, 30, tzinfo=KST)
TODAY = "20260928"


@pytest.fixture
def memory(tmp_path):
    m = TradeMemory(tmp_path / "trades.sqlite3")
    yield m
    m.close()


def _rows(n=30, close=100_000.0, high=None, low=None, change="-2.00", date0=TODAY):
    """Daily rows newest first with a constant OHLC."""
    high = close * 1.01 if high is None else high
    low = close * 0.99 if low is None else low
    return [
        {
            "stck_bsop_date": date0 if i == 0 else f"2026080{i % 9}",
            "stck_clpr": str(close),
            "stck_hgpr": str(high),
            "stck_lwpr": str(low),
            "acml_vol": "1000000",
            "prdy_ctrt": change if i == 0 else "0.00",
        }
        for i in range(n)
    ]


def _ind(**overrides) -> Indicators:
    defaults = dict(
        date=TODAY, is_trading_day=True, price=100_000.0, change_pct=-2.0, ma5=100_000.0, ma20=100_000.0,
        prev_ma5=100_000.0, prev_ma20=100_000.0, rsi14=40.0, volume=1000, volume_ratio=1.0,
    )
    defaults.update(overrides)
    return Indicators(**defaults)


def _plan(drop=5.0, budget=1_000_000.0) -> ladder.LadderPlan:
    est = ladder.DropEstimate(drop, drop, None, "test")
    return ladder.build_plan("005930", TODAY, 100_000.0, est, budget)


# --- estimate ---------------------------------------------------------------------


def test_atr_pct_uses_completed_sessions():
    # constant 2% daily range on a 100,000 close -> ATR 2%
    assert ladder.atr_pct(_rows()) == pytest.approx(2.0)
    assert ladder.atr_pct(_rows(n=10)) is None


def test_estimate_uses_closer_support_and_clamps():
    rows = _rows(high=106_000, low=94_000)  # ATR 12% -> but 20-day low support is 6% below
    est = ladder.estimate_drop(rows, 100_000.0)
    assert est.drop_pct == pytest.approx(6.0) and "지지선" in est.basis

    tiny = ladder.estimate_drop(_rows(high=100_500, low=99_500), 100_000.0)  # ATR 1% -> MIN 2%
    assert tiny.drop_pct == ladder.MIN_DROP_PCT and "보정" in tiny.basis


def test_estimate_none_without_history():
    assert ladder.estimate_drop(_rows(n=5), 100_000.0) is None


# --- plan -------------------------------------------------------------------------


def test_build_plan_matches_the_worked_example():
    plan = _plan(drop=5.0)
    assert [l.price for l in plan.levels] == [97_000, 96_000, 95_000]
    assert [l.weight for l in plan.levels] == [0.2, 0.3, 0.5]
    assert plan.stop_price == 93_000


def test_plan_json_roundtrip():
    plan = _plan()
    plan.levels[0].filled = True
    again = ladder.LadderPlan.from_json(plan.to_json())
    assert again == plan and again.any_filled


@pytest.mark.parametrize(
    "ind, at, expected",
    [
        (_ind(change_pct=-1.5), dtime(10, 0), True),
        (_ind(change_pct=0.0, price=95.0, ma5=97.0, ma20=99.0), dtime(10, 0), True),  # downtrend
        (_ind(change_pct=-0.5), dtime(10, 0), False),
        (_ind(change_pct=-3.0), dtime(9, 10), False),  # too early
        (_ind(change_pct=-3.0), dtime(14, 50), False),  # too late for a new plan
    ],
)
def test_should_plan(ind, at, expected):
    assert ladder.should_plan(ind, at) is expected


def test_due_levels_and_gap_down_buys_several_at_once():
    plan = _plan()
    assert ladder.due_levels(plan, 98_000) == []
    assert ladder.due_levels(plan, 97_000) == [0]
    assert ladder.due_levels(plan, 95_500) == [0, 1]
    plan.levels[0].filled = True
    assert ladder.due_levels(plan, 95_500) == [1]
    assert ladder.due_levels(plan, 93_000) == []  # at/below stop: never buy
    plan.status = "stopped"
    assert ladder.due_levels(plan, 96_000) == []


def test_ladder_decision_quantity_from_weights():
    plan = _plan(budget=1_000_000)
    d = ladder.ladder_decision(plan, [0, 1], 96_000)  # 50% of 1,000,000 / 96,000 -> 5
    assert d.action == "BUY" and d.quantity == 5 and d.reason[0].startswith(ladder.LADDER_TAG)
    assert "1차+2차" in d.reason[0] and d.stop_loss == 93_000
    assert ladder.ladder_decision(_plan(budget=10_000), [0], 97_000) is None  # can't afford 1 share


# --- exits ------------------------------------------------------------------------


def _exit(price, held=10, avg=100_000.0, plan=None, took=0):
    return ladder.exit_decision("005930", price, held, avg, plan, 0.10, 0.04, took)


def test_no_exit_without_position_or_in_range():
    assert _exit(80_000, held=0) is None
    assert _exit(80_000, avg=None) is None
    assert _exit(98_000) is None


def test_plan_stop_only_after_a_level_filled():
    plan = _plan()
    assert _exit(92_000, plan=plan) is None  # nothing bought on this plan yet; hard stop is -10%
    plan.levels[0].filled = True
    d = _exit(92_000, plan=plan)
    assert d.action == "SELL" and d.quantity == 10 and d.reason[0].startswith(ladder.STOP_TAG)


def test_hard_stop_sells_everything():
    d = _exit(90_000)
    assert d.quantity == 10 and ladder.STOP_TAG in d.reason[0]


def test_take_profit_sells_half_once_per_step_per_day():
    d = _exit(104_000, held=7)
    assert d.quantity == 4 and d.reason[0].startswith(ladder.TAKE_PROFIT_TAG) and "1단계" in d.reason[0]
    assert _exit(104_000, held=7, took=1) is None  # step 1 already taken today
    assert _exit(107_000, held=7, took=1) is None  # +7% is still step 1
    d2 = _exit(108_500, held=4, took=1)  # +8.5% -> step 2
    assert d2.quantity == 2 and "2단계" in d2.reason[0]
    assert _exit(108_500, held=4, took=2) is None
    assert _exit(112_000, held=2, took=2).quantity == 1  # step 3


# --- memory -----------------------------------------------------------------------


def test_memory_plan_persistence_and_count(memory):
    plan = _plan()
    memory.save_ladder_plan(plan)
    plan.levels[0].filled = True
    memory.save_ladder_plan(plan)  # upsert
    assert memory.load_ladder_plan("005930", TODAY) == plan
    assert memory.load_ladder_plan("005930", "20260929") is None
    assert memory.ladder_plan_count(TODAY) == 1


def _record(memory, decision_id, decision, status):
    memory.record_decision(decision_id, decision, {})
    memory.record_order(OrderResult(decision_id=decision_id, side=decision.action, symbol=decision.symbol,
                                    quantity=decision.quantity, status=status, reason="x"))


def test_count_decided_today_ignores_rejected_orders(memory):
    tp = _exit(104_000)
    today = datetime.now(KST).strftime("%Y%m%d")
    _record(memory, "d1", tp, "REJECTED_BY_RISK")
    assert memory.count_decided_today("005930", today, "SELL", ladder.TAKE_PROFIT_TAG) == 0
    _record(memory, "d2", tp, "SUBMITTED")
    assert memory.count_decided_today("005930", today, "SELL", ladder.TAKE_PROFIT_TAG) == 1
    _record(memory, "d3", tp, "SUBMITTED")
    assert memory.count_decided_today("005930", today, "SELL", ladder.TAKE_PROFIT_TAG) == 2
    assert memory.count_decided_today("000660", today, "SELL", ladder.TAKE_PROFIT_TAG) == 0


# --- loop -------------------------------------------------------------------------


class FakeKis:
    def __init__(self, rows, holdings=None):
        self.rows = rows
        self.get_balance = MagicMock(
            return_value={"holdings": holdings or [], "summary": {"tot_evlu_amt": "100000000"}}
        )

    def get_daily_prices(self, symbol):
        return self.rows


def _loop(memory, kis, submitted=True, **settings_kw):
    notify = MagicMock()
    run_cycle = MagicMock(return_value=MagicMock(submitted=submitted))
    settings = Settings(watchlist=("005930",), cycle_minutes=10, ai_daily_call_limit=50, **settings_kw)
    from main import PaperTradingLoop

    loop = PaperTradingLoop(memory, settings=settings, notify=notify, run_cycle=run_cycle,
                            kis_client_factory=lambda: kis)
    return loop, notify, run_cycle


def _rows_at(price, change="-2.00"):
    rows = _rows(close=100_000.0, high=102_500, low=97_500, change=change)  # ATR 5%, 20d low 97,500
    rows[0] = dict(rows[0], stck_clpr=str(price))
    return rows


def test_loop_draws_up_plan_then_buys_levels(memory):
    kis = FakeKis(_rows_at(100_000))
    loop, notify, run_cycle = _loop(memory, kis)
    loop.tick(MON_1030)
    plan = memory.load_ladder_plan("005930", TODAY)
    assert plan is not None and "분할매수 계획" in notify.call_args.args[0]
    # no level due yet -> the symbol goes to the normal AI path
    assert run_cycle.call_args.kwargs.get("rule_decision") is None

    kis.rows = _rows_at(plan.levels[0].price)
    loop.tick(MON_1030 + timedelta(minutes=10))
    decision = run_cycle.call_args.kwargs["rule_decision"]
    assert decision.action == "BUY" and decision.reason[0].startswith(ladder.LADDER_TAG)
    assert memory.load_ladder_plan("005930", TODAY).levels[0].filled


def test_loop_does_not_mark_level_filled_when_order_rejected(memory):
    kis = FakeKis(_rows_at(100_000))
    loop, _, run_cycle = _loop(memory, kis, submitted=False)
    loop.tick(MON_1030)
    plan = memory.load_ladder_plan("005930", TODAY)
    kis.rows = _rows_at(plan.levels[0].price)
    loop.tick(MON_1030 + timedelta(minutes=10))
    assert run_cycle.call_args.kwargs["rule_decision"].action == "BUY"
    assert not memory.load_ladder_plan("005930", TODAY).any_filled


def test_loop_stop_sells_and_stops_plan(memory):
    plan = ladder.build_plan("005930", TODAY, 100_000.0, ladder.DropEstimate(5.0, 5.0, None, "t"), 5_000_000)
    plan.levels[0].filled = True
    memory.save_ladder_plan(plan)
    holdings = [{"pdno": "005930", "hldg_qty": "10", "pchs_avg_pric": "97000"}]
    loop, _, run_cycle = _loop(memory, FakeKis(_rows_at(92_000), holdings))
    loop.tick(MON_1030)
    decision = run_cycle.call_args.kwargs["rule_decision"]
    assert decision.action == "SELL" and decision.quantity == 10
    assert memory.load_ladder_plan("005930", TODAY).status == "stopped"


def test_loop_rules_respect_settings_and_plan_cap(memory):
    loop, notify, run_cycle = _loop(memory, FakeKis(_rows_at(100_000)), ladder_enabled=False, exits_enabled=False)
    loop.tick(MON_1030)
    assert memory.load_ladder_plan("005930", TODAY) is None

    loop, _, _ = _loop(memory, FakeKis(_rows_at(100_000)), ladder_max_plans=0)
    loop.tick(MON_1030)
    assert memory.load_ladder_plan("005930", TODAY) is None


def test_loop_rule_failure_falls_back_to_ai(memory):
    rows = _rows_at(100_000)
    for r in rows:
        r.pop("stck_hgpr")  # malformed history -> ladder raises
    loop, notify, run_cycle = _loop(memory, FakeKis(rows))
    loop.tick(MON_1030)
    assert run_cycle.call_count == 1 and run_cycle.call_args.kwargs.get("rule_decision") is None
    assert any("분할매수/손절 규칙 실패" in c.args[0] for c in notify.call_args_list)
