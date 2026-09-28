"""Entry point.

    python main.py                    # show which KIS environment is configured
    python main.py --once 005930      # one paper-trading cycle for one symbol

One cycle: account/price snapshot -> AI decision -> OrderExecutor (which
runs the Risk Manager before anything reaches KIS) -> Telegram notice.
A continuous loop comes with Phase 11; real-money orders stay disabled
until then (broker.order_executor refuses a real-trading client).
"""

from __future__ import annotations

import argparse
import logging
import uuid

from agents.trading_agent import TradingAgent, TradingAgentError
from broker.kis_client import KisClient
from broker.order_executor import OrderExecutor
from config.settings import KisCredentialError, get_settings
from market.data_collector import build_account_state
from models.decision import DecisionParseError
from models.order import OrderResult
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


def run_once(symbol: str) -> OrderResult | None:
    """Returns the OrderResult, or None when no valid AI decision was made."""
    client = KisClient()
    executor = OrderExecutor(client, RiskManager())

    account = build_account_state(symbol, client=client)
    snapshot = {
        "price": client.get_current_price(symbol),
        "held_quantity": account.held_quantity,
        "total_equity": account.total_equity,
    }

    try:
        decision = TradingAgent().decide(symbol, snapshot)
    except (TradingAgentError, DecisionParseError) as exc:
        logger.error("no valid AI decision for %s: %s", symbol, exc)
        _notify(f"[NO DECISION] {symbol}: {exc}")
        return None

    decision_id = uuid.uuid4().hex
    result = executor.submit(decision, decision_id, account)
    message = format_order_result(result) + "\nAI: " + " / ".join(decision.reason)
    print(message)
    _notify(message)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="KIS AI Trading Agent")
    parser.add_argument("--once", metavar="SYMBOL", help="run one paper-trading cycle for SYMBOL")
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

    if args.once:
        run_once(args.once)


if __name__ == "__main__":
    main()
