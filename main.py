"""Entry point.

    python main.py                    # show which KIS environment is configured
    python main.py --once 005930      # one paper-trading cycle for one symbol
    python main.py --release-emergency-stop   # after checking the account yourself

One cycle: account/price snapshot -> AI decision -> OrderExecutor (which
runs the Risk Manager before anything reaches KIS) -> fill confirmation
(or reconciliation of an UNKNOWN order) -> Telegram notice, with every
step recorded in memory.trade_memory (SQLite).
A continuous loop comes with Phase 11; real-money orders stay disabled
until then (broker.order_executor refuses a real-trading client).
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import uuid
from dataclasses import replace

from agents.trading_agent import TradingAgent, TradingAgentError
from broker.kis_client import KisApiError, KisClient
from broker.order_executor import OrderExecutor
from config.settings import KisCredentialError, get_settings
from market.data_collector import build_account_state
from memory.trade_memory import TradeMemory
from models.decision import DecisionParseError
from models.order import OrderFill, OrderFillParseError, OrderResult
from risk.risk_manager import RiskManager
from telegram.notifier import TelegramError, TelegramNotifier

logger = logging.getLogger(__name__)


def _notify(text: str) -> None:
    """Telegram failures are logged only -- they must never affect trading."""
    try:
        TelegramNotifier().send_message(text, parse_mode="")
    except TelegramError as exc:
        logger.warning("telegram notification failed: %s", exc)


def format_order_result(result: OrderResult) -> str:
    lines = [
        f"[{result.status}] {result.side} {result.symbol} x{result.quantity}",
        f"decision_id: {result.decision_id}",
        f"reason: {result.reason}",
    ]
    if result.order_no:
        lines.append(f"order_no: {result.order_no} ({result.order_time})")
    return "\n".join(lines)


def format_fill(fill: OrderFill | None) -> str:
    if fill is None:
        return "fill: not listed by KIS yet -- check manually"
    return (
        f"fill: {fill.status} {fill.filled_qty}/{fill.ordered_qty} "
        f"@ avg {fill.avg_price:,.0f} (remaining {fill.remaining_qty})"
    )


def run_once(symbol: str, memory: TradeMemory | None = None) -> OrderResult | None:
    """Returns the OrderResult, or None when no order was attempted
    (emergency stop engaged, or no valid AI decision)."""
    memory = memory or TradeMemory()
    client = KisClient()
    risk = RiskManager(store=memory)
    if risk.emergency_stop_engaged:
        # Checked before the AI call so a stopped system doesn't keep paying
        # for decisions it can't act on.
        message = (
            f"[EMERGENCY STOP] {symbol}: skipped -- {risk.emergency_stop_reason}\n"
            "Check the account, then: python main.py --release-emergency-stop"
        )
        print(message)
        _notify(message)
        return None
    executor = OrderExecutor(client, risk)

    account = build_account_state(symbol, client=client)
    account = replace(account, day_start_equity=memory.day_start_equity(account.total_equity))
    snapshot = {
        "price": client.get_current_price(symbol),
        "held_quantity": account.held_quantity,
        "avg_cost": account.avg_cost,
        "total_equity": account.total_equity,
        "recent_trades": memory.recent_trades(symbol),
    }

    try:
        decision = TradingAgent().decide(symbol, snapshot)
    except (TradingAgentError, DecisionParseError) as exc:
        logger.error("no valid AI decision for %s: %s", symbol, exc)
        _notify(f"[NO DECISION] {symbol}: {exc}")
        return None

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
        message += f"\nWARNING: order not saved to trade memory: {exc}"
    try:
        fill = None
        if result.submitted:
            fill = executor.confirm_fill(result)
            message += "\n" + format_fill(fill)
        elif result.status == "UNKNOWN":
            rec = executor.reconcile_unknown(result)
            fill = rec.fill
            message += f"\nreconciliation: {rec.outcome} {[c.order_no for c in rec.candidates]}"
        if fill is not None:
            memory.record_fill(decision_id, fill, cost_basis=account.avg_cost)
    except (KisApiError, OrderFillParseError) as exc:
        logger.error("fill/reconciliation lookup failed for %s: %s", decision_id, exc)
        message += f"\nfill lookup failed: {exc} -- check manually"
    except sqlite3.Error as exc:
        logger.error("failed to record fill %s: %s", decision_id, exc)
        message += f"\nWARNING: fill not saved to trade memory: {exc}"
    if result.status == "UNKNOWN":
        message += "\nEMERGENCY STOP engaged -- check the account before resuming"
    message += "\nAI: " + " / ".join(decision.reason)
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
    message = f"[EMERGENCY STOP RELEASED] by operator (was: {reason})"
    print(message)
    _notify(message)


def main() -> None:
    parser = argparse.ArgumentParser(description="KIS AI Trading Agent")
    parser.add_argument("--once", metavar="SYMBOL", help="run one paper-trading cycle for SYMBOL")
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


if __name__ == "__main__":
    main()
