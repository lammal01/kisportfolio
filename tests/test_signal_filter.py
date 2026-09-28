"""market.indicators + market.signal_filter + main.PaperTradingLoop."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from config.settings import Settings
from market import indicators
from market.indicators import IndicatorError, Indicators, compute, rsi, sma
from market.signal_filter import SignalFilter
from memory.trade_memory import TradeMemory
from models.decision import Decision
from risk.risk_manager import RiskManager

KST = ZoneInfo("Asia/Seoul")
MON_1030 = datetime(2026, 9, 28, 10, 30, tzinfo=KST)  # a Monday


@pytest.fixture
def memory(tmp_path):
    m = TradeMemory(tmp_path / "trades.sqlite3")
    yield m
    m.close()


def _rows(closes: list[float], date0: str = "20260928", volumes: list[int] | None = None) -> list[dict]:
    """Daily rows newest first; closes newest first."""
    volumes = volumes or [1_000_000] * len(closes)
    rows = []
    for i, (c, v) in enumerate(zip(closes, volumes)):
        rows.append(
            {
                "stck_bsop_date": date0 if i == 0 else f"2026080{i % 9}",
                "stck_clpr": str(c),
                "acml_vol": str(v),
                "prdy_ctrt": "0.00",
            }
        )
    return rows


def _ind(**overrides) -> Indicators:
    defaults = dict(
        date="20260928", is_trading_day=True, price=100.0, change_pct=0.0, ma5=100.0, ma20=100.0,
        prev_ma5=100.0, prev_ma20=100.0, rsi14=50.0, volume=1000, volume_ratio=1.0,
    )
    defaults.update(overrides)
    return Indicators(**defaults)


# --- indicators -----------------------------------------------------------------


def test_sma_and_short_history():
    assert sma([1, 2, 3, 4], 2) == 1.5
    assert sma([1], 2) is None


def test_rsi_extremes_and_known_value():
    rising = list(range(30, 0, -1))  # newest first: steadily rising
    assert rsi([float(x) for x in rising]) == 100.0
    assert rsi([float(x) for x in reversed(rising)]) == pytest.approx(0.0)
    assert rsi([1.0] * 10) is None
    alternating = [100.0 + (1 if i % 2 else -1) for i in range(30)]
    assert 40 < rsi(alternating) < 60


def test_compute_trading_day_ma_and_volume_ratio():
    closes = [110.0] + [100.0] * 29
    volumes = [1_000_000] + [1_000_000] * 29
    ind = compute(_rows(closes, volumes=volumes), MON_1030)  # 90 of 390 minutes elapsed
    assert ind.is_trading_day
    assert ind.ma5 == pytest.approx(102.0)
    assert ind.prev_ma5 == pytest.approx(100.0)
    assert ind.volume_ratio == pytest.approx(390 / 90)


def test_compute_golden_cross():
    # yesterday MA5 below MA20, today's jump puts it above
    # today MA5 116 > MA20 107.75; yesterday MA5 97 < MA20 103
    closes = [200.0] + [95.0] * 4 + [105.0] * 25
    ind = compute(_rows(closes), MON_1030)
    assert ind.ma_cross == "golden"


def test_compute_detects_non_trading_day():
    ind = compute(_rows([100.0] * 30, date0="20260925"), MON_1030)
    assert not ind.is_trading_day
    assert ind.volume_ratio is None


def test_compute_rejects_malformed_rows():
    with pytest.raises(IndicatorError):
        compute([], MON_1030)
    with pytest.raises(IndicatorError):
        compute(_rows([0.0] * 30), MON_1030)


# --- signal filter ------------------------------------------------------------------


def _record_call(memory, symbol="005930", price=100.0, when=None, ok=True):
    memory.record_ai_call(symbol, ok=ok)
    if ok:
        memory.record_decision(
            f"d-{symbol}-{price}-{when}", Decision("HOLD", symbol, 0, 0.5, ("x",)), {"indicators": {"price": price}}
        )
    if when is not None:  # backdate the call
        with memory._lock, memory._conn:
            memory._conn.execute("UPDATE ai_calls SET called_at = ? WHERE id = (SELECT MAX(id) FROM ai_calls)",
                                 (when.isoformat(timespec="seconds"),))


def test_first_check_of_day_triggers(memory):
    check = SignalFilter(memory, 50).check("005930", _ind(), 0, MON_1030)
    assert check.call_ai and check.triggers == ("오늘 첫 점검",)


def test_quiet_symbol_does_not_trigger(memory):
    _record_call(memory, when=MON_1030 - timedelta(hours=1))
    check = SignalFilter(memory, 50).check("005930", _ind(price=101.0), 0, MON_1030)
    assert not check.call_ai and check.triggers == ()


def test_price_move_since_last_call_triggers(memory):
    _record_call(memory, price=100.0, when=MON_1030 - timedelta(hours=1))
    check = SignalFilter(memory, 50).check("005930", _ind(price=97.5), 0, MON_1030)
    assert check.call_ai and "-2.5% 변동" in check.triggers[0]


@pytest.mark.parametrize(
    "overrides, expected",
    [
        (dict(volume_ratio=2.5), "거래량 급증"),
        (dict(ma5=101.0, ma20=100.0, prev_ma5=99.0, prev_ma20=100.0), "골든크로스"),
        (dict(ma5=99.0, ma20=100.0, prev_ma5=101.0, prev_ma20=100.0), "데드크로스"),
        (dict(rsi14=25.0), "RSI 과매도"),
        (dict(rsi14=75.0), "RSI 과매수"),
    ],
)
def test_indicator_triggers(memory, overrides, expected):
    _record_call(memory, when=MON_1030 - timedelta(hours=1))
    check = SignalFilter(memory, 50).check("005930", _ind(**overrides), 0, MON_1030)
    assert check.call_ai and expected in check.triggers[0]


def test_volume_surge_ignored_right_after_open(memory):
    _record_call(memory, when=MON_1030.replace(hour=9, minute=0) - timedelta(days=1))
    early = MON_1030.replace(hour=9, minute=10)
    check = SignalFilter(memory, 50).check("005930", _ind(volume_ratio=5.0), 0, early)
    assert all("거래량" not in t for t in check.triggers)


def test_held_position_reviewed_hourly(memory):
    _record_call(memory, when=MON_1030 - timedelta(minutes=61))
    check = SignalFilter(memory, 50).check("005930", _ind(), 3, MON_1030)
    assert check.call_ai and "보유 종목 정기 점검" in check.triggers[0]


def test_cooldown_blocks_repeat_calls(memory):
    _record_call(memory, price=100.0, when=MON_1030 - timedelta(minutes=10))
    check = SignalFilter(memory, 50).check("005930", _ind(price=90.0), 0, MON_1030)
    assert not check.call_ai and "쿨다운" in check.blocked_by


def test_daily_limit_counts_failed_calls_too(memory):
    for _ in range(3):
        _record_call(memory, symbol="000660", ok=False)
    check = SignalFilter(memory, 3).check("005930", _ind(), 0, datetime.now(KST))
    assert not check.call_ai and "상한 3회" in check.blocked_by


# --- loop -----------------------------------------------------------------------


class FakeKis:
    def __init__(self, date0="20260928"):
        self.date0 = date0
        self.get_balance = MagicMock(return_value={"holdings": [], "summary": {"tot_evlu_amt": "10000000"}})

    def get_daily_prices(self, symbol):
        return _rows([100.0] * 30, date0=self.date0)


def _loop(memory, kis=None, **kw):
    notify = MagicMock()
    run_cycle = MagicMock()
    settings = Settings(watchlist=("005930", "000660"), cycle_minutes=10, ai_daily_call_limit=50)
    from main import PaperTradingLoop

    loop = PaperTradingLoop(
        memory, settings=settings, notify=notify, run_cycle=run_cycle,
        kis_client_factory=lambda: kis or FakeKis(), **kw,
    )
    return loop, notify, run_cycle


def test_loop_runs_triggered_symbols_with_context(memory):
    loop, notify, run_cycle = _loop(memory)
    loop.tick(MON_1030)
    assert [c.args[0] for c in run_cycle.call_args_list] == ["005930", "000660"]
    context = run_cycle.call_args.kwargs["context"]
    assert context["signal_triggers"][0] == "오늘 첫 점검" and "rsi14" in context["indicators"]
    notify.assert_not_called()  # quiet start while running normally


def test_loop_skips_outside_hours_and_weekends(memory):
    loop, _, run_cycle = _loop(memory)
    loop.tick(MON_1030.replace(hour=8, minute=50))
    loop.tick(MON_1030.replace(hour=15, minute=25))  # closing auction
    loop.tick(MON_1030 + timedelta(days=5))  # Saturday
    run_cycle.assert_not_called()


def test_loop_detects_holiday_once(memory):
    loop, notify, run_cycle = _loop(memory, kis=FakeKis(date0="20260925"))
    loop.tick(MON_1030)
    loop.tick(MON_1030 + timedelta(minutes=10))
    run_cycle.assert_not_called()
    assert notify.call_count == 1 and "휴장일" in notify.call_args.args[0]


def test_loop_does_not_call_holiday_right_after_open(memory):
    loop, notify, _ = _loop(memory, kis=FakeKis(date0="20260925"))
    loop.tick(MON_1030.replace(hour=9, minute=0))
    notify.assert_not_called()


def test_loop_honors_stop_and_pause_and_notifies_transitions(memory):
    loop, notify, run_cycle = _loop(memory)
    RiskManager(store=memory).engage_emergency_stop("x")
    loop.tick(MON_1030)
    loop.tick(MON_1030 + timedelta(minutes=10))
    run_cycle.assert_not_called()
    assert notify.call_count == 1 and "비상 정지" in notify.call_args.args[0]

    RiskManager(store=memory).disengage_emergency_stop()
    memory.save_pause("lunch")
    loop.tick(MON_1030 + timedelta(minutes=20))
    assert "일시정지" in notify.call_args.args[0]

    memory.save_pause(None)
    loop.tick(MON_1030 + timedelta(minutes=30))
    assert "재개" in notify.call_args.args[0]
    run_cycle.assert_called()


def test_loop_symbol_error_is_isolated_and_throttled(memory):
    kis = FakeKis()
    kis.get_daily_prices = MagicMock(side_effect=[RuntimeError("boom"), _rows([100.0] * 30)] * 2)
    loop, notify, run_cycle = _loop(memory, kis=kis, now=lambda: MON_1030)
    loop.tick(MON_1030)
    assert [c.args[0] for c in run_cycle.call_args_list] == ["000660"]  # second symbol still ran
    loop.tick(MON_1030 + timedelta(minutes=10))
    assert sum("루프 오류" in c.args[0] for c in notify.call_args_list) == 1


def test_loop_daily_summary_once_after_close(memory):
    loop, notify, _ = _loop(memory)
    memory.day_start_equity(9_900_000.0, "20260928")
    loop.tick(MON_1030.replace(hour=15, minute=40))
    loop.tick(MON_1030.replace(hour=15, minute=50))
    summaries = [c.args[0] for c in notify.call_args_list if "일일 요약" in c.args[0]]
    assert len(summaries) == 1
    assert "+100,000원" in summaries[0]


def test_seconds_until_next_tick_aligns_to_cycle(memory):
    loop, _, _ = _loop(memory)
    assert loop.seconds_until_next_tick(MON_1030.replace(minute=33, second=30)) == 390


def test_second_loop_lock_is_refused(tmp_path):
    from main import LoopAlreadyRunningError, acquire_loop_lock

    first = acquire_loop_lock(tmp_path / "x.loop.lock")
    with pytest.raises(LoopAlreadyRunningError):
        acquire_loop_lock(tmp_path / "x.loop.lock")
    first.close()
    acquire_loop_lock(tmp_path / "x.loop.lock").close()  # released -> available again
