"""Entry point.

    python main.py                    # show which KIS environment is configured
    python main.py --once 005930      # one paper-trading cycle for one symbol
    python main.py --loop             # paper-trading loop over WATCHLIST (Phase 11)
    python main.py --release-emergency-stop   # after checking the account yourself

One cycle: account/price snapshot -> AI decision -> OrderExecutor (which
runs the Risk Manager before anything reaches KIS) -> fill confirmation
(or reconciliation of an UNKNOWN order) -> Telegram notice, with every
step recorded in memory.trade_memory (SQLite).
--loop repeats that every CYCLE_MINUTES during the session for the
WATCHLIST symbols market.signal_filter lets through. Real-money orders
stay disabled (broker.order_executor refuses a real-trading client).
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.trading_agent import TradingAgent, TradingAgentError
from broker.kis_client import KisApiError, KisClient
from broker.order_executor import OrderExecutor
from config.settings import KisCredentialError, get_settings
from market import indicators, ladder
from market.data_collector import build_account_state
from market.signal_filter import SignalFilter
from memory.trade_memory import TradeMemory
from models.decision import Decision, DecisionParseError
from models.order import OrderFill, OrderFillParseError, OrderResult
from risk.risk_manager import RiskManager
from telegram.labels import ACTION, FILL_STATUS, ORDER_STATUS, RECONCILIATION, label
from telegram.notifier import TelegramError, TelegramNotifier

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")


def _notify(text: str) -> None:
    """Telegram failures are logged only -- they must never affect trading."""
    try:
        TelegramNotifier().send_message(text, parse_mode="")
    except TelegramError as exc:
        logger.warning("telegram notification failed: %s", exc)


def format_order_result(result: OrderResult) -> str:
    if result.side == "HOLD":
        return f"[관망] {result.symbol} -- 주문 없음\ndecision_id: {result.decision_id}"
    lines = [
        f"[{label(ORDER_STATUS, result.status)}] {result.symbol} "
        f"{label(ACTION, result.side)} {result.quantity}주",
        f"사유: {result.reason}",
    ]
    if result.order_no:
        lines.append(f"주문번호: {result.order_no} ({result.order_time})")
    lines.append(f"decision_id: {result.decision_id}")
    return "\n".join(lines)


def format_fill(fill: OrderFill | None) -> str:
    if fill is None:
        return "체결: KIS 주문 목록에 아직 없음 -- 직접 확인 필요"
    return (
        f"체결: {label(FILL_STATUS, fill.status)} {fill.filled_qty}/{fill.ordered_qty}주 "
        f"평균 {fill.avg_price:,.0f}원 (잔량 {fill.remaining_qty}주)"
    )


def run_once(
    symbol: str,
    memory: TradeMemory | None = None,
    context: dict | None = None,
    rule_decision: Decision | None = None,
) -> OrderResult | None:
    """Returns the OrderResult, or None when no order was attempted
    (emergency stop engaged, paused via Telegram, or no valid AI decision).

    context: extra snapshot fields from the loop (indicators, the
    signal_filter triggers that caused this call).
    rule_decision: a market.ladder decision (분할매수/손절/익절) to submit
    instead of asking the AI -- same Risk Manager path, no AI call counted."""
    memory = memory or TradeMemory()
    client = KisClient()
    risk = RiskManager(store=memory)
    if risk.emergency_stop_engaged:
        # Checked before the AI call so a stopped system doesn't keep paying
        # for decisions it can't act on.
        message = (
            f"[비상 정지] {symbol}: 사이클 건너뜀 -- {risk.emergency_stop_reason}\n"
            "계좌 확인 후 해제: 텔레그램 /release_stop 또는 python main.py --release-emergency-stop"
        )
        print(message)
        _notify(message)
        return None
    pause_reason = memory.load_pause()
    if pause_reason is not None:
        print(f"[일시정지] {symbol}: 사이클 건너뜀 -- {pause_reason}")
        return None
    executor = OrderExecutor(client, risk)

    account = build_account_state(symbol, client=client)
    account = replace(account, day_start_equity=memory.day_start_equity(account.total_equity))
    snapshot = {
        "price": client.get_current_price(symbol),
        "held_quantity": account.held_quantity,
        "avg_cost": account.avg_cost,
        "total_equity": account.total_equity,
        # Lets the AI size orders sensibly; risk.risk_manager still enforces it.
        "max_buy_value": risk.max_buy_value(account),
        "recent_trades": memory.recent_trades(symbol),
        **(context or {}),
    }
    triggers = list((context or {}).get("signal_triggers", []))

    if rule_decision is not None:
        decision = rule_decision
    else:
        try:
            decision = TradingAgent().decide(symbol, snapshot)
        except (TradingAgentError, DecisionParseError) as exc:
            memory.record_ai_call(symbol, ok=False, triggers=triggers)
            logger.error("no valid AI decision for %s: %s", symbol, exc)
            _notify(f"[판단 실패] {symbol}: AI 판단을 받지 못해 주문 없음 -- {exc}")
            return None
        memory.record_ai_call(symbol, ok=True, triggers=triggers)
    decision_id = uuid.uuid4().hex
    # Recorded before submitting: if this write fails, no order goes out.
    memory.record_decision(decision_id, decision, snapshot)
    result = executor.submit(decision, decision_id, account)
    message = format_order_result(result)

    # The order already happened by now -- a failed lookup or DB write must
    # not stop the notification from going out.
    try:
        memory.record_order(result)
    except sqlite3.Error as exc:
        logger.error("failed to record order %s: %s", decision_id, exc)
        message += f"\n경고: 주문 기록 저장 실패 -- {exc}"
    try:
        fill = None
        if result.submitted:
            fill = executor.confirm_fill(result)
            message += "\n" + format_fill(fill)
        elif result.status == "UNKNOWN":
            rec = executor.reconcile_unknown(result)
            fill = rec.fill
            message += f"\n주문 대사: {label(RECONCILIATION, rec.outcome)}"
            if rec.candidates:
                message += f" {[c.order_no for c in rec.candidates]}"
        if fill is not None:
            memory.record_fill(decision_id, fill, cost_basis=account.avg_cost)
    except (KisApiError, OrderFillParseError) as exc:
        logger.error("fill/reconciliation lookup failed for %s: %s", decision_id, exc)
        message += f"\n체결 조회 실패 -- 직접 확인 필요: {exc}"
    except sqlite3.Error as exc:
        logger.error("failed to record fill %s: %s", decision_id, exc)
        message += f"\n경고: 체결 기록 저장 실패 -- {exc}"
    if result.status == "UNKNOWN":
        message += "\n비상 정지 걸림 -- 계좌 확인 전까지 모든 주문 차단"
    if triggers:
        message += "\n호출 이유: " + ", ".join(triggers)
    header = "규칙 판단:" if rule_decision is not None else f"AI 판단 (확신도 {decision.confidence:.0%}):"
    message += f"\n\n{header}\n" + "\n".join(f"- {r}" for r in decision.reason)
    print(message)
    _notify(message)
    return result


def release_emergency_stop(memory: TradeMemory | None = None) -> None:
    """Human-only: the code never lifts an emergency stop by itself."""
    memory = memory or TradeMemory()
    risk = RiskManager(store=memory)
    if not risk.emergency_stop_engaged:
        print("Emergency stop is not engaged.")
        return
    reason = risk.emergency_stop_reason
    risk.disengage_emergency_stop()
    message = f"[비상 정지 해제] 운영자가 직접 해제 (이전 사유: {reason})"
    print(message)
    _notify(message)


MARKET_OPEN = dtime(9, 0)
# Last new orders at 15:20 -- the 15:20-15:30 closing auction is skipped.
ORDER_CUTOFF = dtime(15, 20)
SUMMARY_AFTER = dtime(15, 35)
# KIS may not have today's daily row for a moment after the open; only
# call it a holiday once this much of the session has passed.
HOLIDAY_CHECK_AFTER = dtime(9, 10)
ERROR_NOTICE_INTERVAL = timedelta(minutes=30)


def _optional_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _kst_now() -> datetime:
    return datetime.now(_KST)


class PaperTradingLoop:
    """main.py --loop: every CYCLE_MINUTES during the session, check each
    WATCHLIST symbol with market.signal_filter and run a full cycle
    (run_once) only for the ones that trigger. Real-money orders stay
    impossible (OrderExecutor refuses a real client); the emergency stop /
    pause are honored every cycle; Telegram gets state changes, orders,
    errors (throttled) and a daily summary -- not every quiet cycle."""

    def __init__(
        self,
        memory: TradeMemory,
        settings=None,
        now=_kst_now,
        sleep=time.sleep,
        notify=None,
        run_cycle=None,
        kis_client_factory=KisClient,
    ):
        self._memory = memory
        self._settings = settings or get_settings()
        self._now = now
        self._sleep = sleep
        self._notify = notify or _notify
        self._run_once = run_cycle or run_once
        self._kis = kis_client_factory
        self._filter = SignalFilter(memory, self._settings.ai_daily_call_limit)
        self._state: str | None = None
        self._holiday: str | None = None
        self._summary_sent_for: str | None = None
        self._limit_notice_for: str | None = None
        self._last_error_notice: datetime | None = None

    def run_forever(self) -> None:
        s = self._settings
        self._notify(
            f"[모의 매매 루프 시작] 감시 {', '.join(s.watchlist)} / {s.cycle_minutes}분 간격 / "
            f"AI 하루 최대 {s.ai_daily_call_limit}회"
        )
        while True:
            try:
                self.tick(self._now())
            except Exception as exc:  # the loop itself must keep running
                logger.exception("tick failed")
                self._error_notice(f"루프 오류: {exc!r}")
            self._sleep(self.seconds_until_next_tick(self._now()))

    def seconds_until_next_tick(self, now: datetime) -> float:
        step = self._settings.cycle_minutes
        base = now.replace(second=0, microsecond=0)
        nxt = base + timedelta(minutes=step - base.minute % step)
        return max((nxt - now).total_seconds(), 1.0)

    def tick(self, now: datetime) -> None:
        if now.weekday() >= 5:
            return
        today = now.strftime("%Y%m%d")
        if MARKET_OPEN <= now.time() < ORDER_CUTOFF:
            if self._holiday != today:
                self.cycle(now)
        elif now.time() >= SUMMARY_AFTER and self._summary_sent_for != today and self._holiday != today:
            self.send_daily_summary(now)
            self._summary_sent_for = today

    def _current_state(self) -> str:
        if RiskManager(store=self._memory).emergency_stop_engaged:
            return "stopped"
        return "paused" if self._memory.load_pause() is not None else "running"

    def cycle(self, now: datetime) -> None:
        state = self._current_state()
        if state != self._state:
            if self._state is not None or state != "running":
                self._notify(
                    {
                        "running": "[루프] 매매 사이클 재개",
                        "paused": "[루프] 일시정지 중 -- 사이클 건너뜀 (/resume 으로 재개)",
                        "stopped": "[루프] 비상 정지 중 -- 사이클 건너뜀 (계좌 확인 후 /release_stop)",
                    }[state]
                )
            self._state = state
        if state != "running":
            return

        today = now.strftime("%Y%m%d")
        client = self._kis()
        balance = client.get_balance()
        held = {h.get("pdno"): int(h.get("hldg_qty", "0")) for h in balance["holdings"]}
        avg_costs = {h.get("pdno"): _optional_float(h.get("pchs_avg_pric")) for h in balance["holdings"]}
        total_equity = _optional_float(balance.get("summary", {}).get("tot_evlu_amt"))
        for symbol in self._settings.watchlist:
            try:
                rows = client.get_daily_prices(symbol)
                ind = indicators.compute(rows, now)
                if not ind.is_trading_day:
                    if now.time() >= HOLIDAY_CHECK_AFTER:
                        self._holiday = today
                        self._notify(f"[휴장일] {today} -- 오늘은 매매하지 않습니다.")
                        return
                    continue
                if self._run_rules(symbol, rows, ind, held.get(symbol, 0), avg_costs.get(symbol), total_equity, now):
                    continue  # a rule order went out -- no AI call for this symbol this cycle
                check = self._filter.check(symbol, ind, held.get(symbol, 0), now)
                logger.info(
                    "%s price=%s triggers=%s call_ai=%s blocked=%s",
                    symbol, ind.price, list(check.triggers), check.call_ai, check.blocked_by,
                )
                if check.call_ai:
                    self._run_once(
                        symbol,
                        memory=self._memory,
                        context={"indicators": ind.as_dict(), "signal_triggers": list(check.triggers)},
                    )
                elif check.blocked_by and "상한" in check.blocked_by and self._limit_notice_for != today:
                    self._limit_notice_for = today
                    self._notify(f"[루프] {check.blocked_by} -- 오늘은 더 이상 AI를 호출하지 않습니다.")
            except Exception as exc:
                logger.exception("cycle failed for %s", symbol)
                self._error_notice(f"{symbol} 처리 실패: {exc!r}")

    def _run_rules(
        self,
        symbol: str,
        rows: list[dict],
        ind,
        held: int,
        avg_cost: float | None,
        total_equity: float | None,
        now: datetime,
    ) -> bool:
        """market.ladder exits (손절/익절) then scale-in (분할매수). Returns
        True if an order was attempted. A failure here is logged and the
        symbol falls back to the normal AI path -- it never skips it."""
        s = self._settings
        if not (s.exits_enabled or s.ladder_enabled):
            return False
        today = now.strftime("%Y%m%d")
        try:
            plan = self._memory.load_ladder_plan(symbol, today)
            if s.exits_enabled:
                decision = ladder.exit_decision(
                    symbol, ind.price, held, avg_cost, plan,
                    s.exit_hard_stop_pct, s.exit_take_profit_pct,
                    self._memory.count_decided_today(symbol, today, "SELL", ladder.TAKE_PROFIT_TAG),
                )
                if decision is not None:
                    result = self._submit_rule(symbol, ind, decision)
                    if result is not None and result.submitted and plan is not None and decision.reason[0].startswith(ladder.STOP_TAG):
                        plan.status = "stopped"  # no more buys on a plan whose estimate failed
                        self._memory.save_ladder_plan(plan)
                    return True
            if not s.ladder_enabled:
                return False
            if plan is None:
                plan = self._new_plan(symbol, rows, ind, held, total_equity, now)
                if plan is None:
                    return False
            due = ladder.due_levels(plan, ind.price)
            decision = ladder.ladder_decision(plan, due, ind.price) if due else None
            if decision is None:
                return False
            result = self._submit_rule(symbol, ind, decision)
            if result is not None and result.submitted:
                for i in due:
                    plan.levels[i].filled = True
                self._memory.save_ladder_plan(plan)
            return True
        except Exception as exc:
            logger.exception("ladder/exit rules failed for %s", symbol)
            self._error_notice(f"{symbol} 분할매수/손절 규칙 실패 (AI 판단은 계속): {exc!r}")
            return False

    def _new_plan(self, symbol, rows, ind, held, total_equity, now):
        s = self._settings
        if not total_equity or not ladder.should_plan(ind, now.time()):
            return None
        today = now.strftime("%Y%m%d")
        if self._memory.ladder_plan_count(today) >= s.ladder_max_plans:
            return None
        budget = total_equity * s.ladder_budget_pct
        if held * ind.price >= budget:
            return None  # already holding at least a full plan's worth
        est = ladder.estimate_drop(rows, ind.price)
        if est is None:
            return None
        plan = ladder.build_plan(symbol, today, ind.price, est, budget)
        self._memory.save_ladder_plan(plan)
        logger.info("ladder plan %s: %s", symbol, plan.describe().replace("\n", " / "))
        self._notify(f"[분할매수 계획] {symbol}\n{plan.describe()}")
        return plan

    def _submit_rule(self, symbol: str, ind, decision: Decision) -> OrderResult | None:
        logger.info("rule decision %s: %s %s주 -- %s", symbol, decision.action, decision.quantity, decision.reason[0])
        return self._run_once(
            symbol,
            memory=self._memory,
            context={"indicators": ind.as_dict(), "signal_triggers": [decision.reason[0]]},
            rule_decision=decision,
        )

    def _error_notice(self, text: str) -> None:
        now = self._now()
        if self._last_error_notice is None or now - self._last_error_notice >= ERROR_NOTICE_INTERVAL:
            self._last_error_notice = now
            self._notify(f"[루프 오류] {text}\n(같은 종류 알림은 30분에 한 번만)")

    def send_daily_summary(self, now: datetime) -> None:
        today = now.strftime("%Y%m%d")
        summary = self._memory.daily_summary(today)
        start = self._memory.last_day_start_equity()
        lines = [f"[일일 요약] {today}"]
        lines.append(f"AI 호출: {summary['ai_calls']}회")
        decisions = summary["decisions"]
        lines.append(
            "판단: " + (", ".join(f"{label(ACTION, a)} {n}" for a, n in decisions.items()) or "없음")
        )
        orders = summary["orders"]
        lines.append(
            "주문: " + (", ".join(f"{label(ORDER_STATUS, st)} {n}" for st, n in orders.items()) or "없음")
        )
        lines.append(f"체결된 주문: {summary['filled_orders']}건")
        lines.append(f"실현손익(수수료 제외): {summary['realized_pnl']:,.0f}원")
        try:
            equity = float(self._kis().get_balance()["summary"]["tot_evlu_amt"])
            if start and start[0] == today:
                change = equity - start[1]
                lines.append(f"총평가: {equity:,.0f}원 (시작 대비 {change:+,.0f}원, {change / start[1]:+.2%})")
            else:
                lines.append(f"총평가: {equity:,.0f}원")
        except Exception as exc:
            lines.append(f"총평가 조회 실패: {exc}")
        self._notify("\n".join(lines))


class LoopAlreadyRunningError(RuntimeError):
    pass


def acquire_loop_lock(path: Path):
    """Holds an OS-level lock on `path` for the life of the process, so a
    second --loop (which could double every order) refuses to start. The
    OS drops the lock when the process dies, so a crash never leaves a
    stale lock behind. Returns the open file; keep a reference to it."""
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise LoopAlreadyRunningError(f"another --loop is already running ({path})") from exc
    return handle


def main() -> None:
    parser = argparse.ArgumentParser(description="KIS AI Trading Agent")
    parser.add_argument("--once", metavar="SYMBOL", help="run one paper-trading cycle for SYMBOL")
    parser.add_argument(
        "--loop", action="store_true", help="run the paper-trading loop over WATCHLIST during market hours"
    )
    parser.add_argument(
        "--release-emergency-stop",
        action="store_true",
        help="lift a persisted emergency stop (only after checking the account yourself)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    try:
        _, _, _, environment = settings.resolve_kis_credentials()
    except KisCredentialError as exc:
        print(f"KIS AI Trading Agent -- no KIS environment configured yet: {exc}")
        return
    label = "실전투자 (REAL MONEY)" if environment == "real" else "모의투자 (paper)"
    print(f"KIS AI Trading Agent -- environment={environment} ({label})")

    if args.release_emergency_stop:
        release_emergency_stop()
    if args.once:
        run_once(args.once)
    if args.loop:
        if environment == "real":
            print("--loop is paper-only until Phase 11 validation is done -- refusing to start.")
            return
        try:
            lock = acquire_loop_lock(Path(settings.trade_db_path).with_suffix(".loop.lock"))
        except LoopAlreadyRunningError as exc:
            print(f"refusing to start: {exc}")
            return
        try:
            PaperTradingLoop(TradeMemory()).run_forever()
        except KeyboardInterrupt:
            _notify("[모의 매매 루프 종료] 사용자가 중지함")
        finally:
            lock.close()


if __name__ == "__main__":
    main()
