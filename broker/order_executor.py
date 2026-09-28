"""Order execution against the KIS Open API.

OrderExecutor.submit() is the only way to place an order, and it runs
risk.risk_manager.RiskManager.evaluate() itself before touching KIS --
there is no public method that takes an already-"approved" order, so a
caller can't skip or fake the risk check.

An accepted order (KIS returned an order number) is not a fill; fill
confirmation comes in Phase 8. HTTP 200 alone is never taken as success.

Real trading is refused outright until Phase 11 (장시간 Paper Trading)
has been validated: constructing an OrderExecutor on a real-trading
KisClient raises, regardless of what's in .env.
"""

from __future__ import annotations

import logging
import threading

from broker.kis_client import KisApiError, KisClient, KisOrderUncertainError
from models.decision import Decision
from models.order import OrderResult
from risk.risk_manager import AccountState, RiskManager

logger = logging.getLogger(__name__)


class RealTradingDisabledError(RuntimeError):
    """Raised when an OrderExecutor is built on a real-trading KisClient."""


class OrderExecutor:
    def __init__(self, client: KisClient, risk_manager: RiskManager):
        if client.is_real_trading:
            raise RealTradingDisabledError(
                "Real-money order execution is disabled until Phase 11 (장시간 Paper Trading) "
                "has been validated. Use KIS_PAPER_* credentials."
            )
        self._client = client
        self._risk = risk_manager
        # evaluate -> place -> mark_executed must not interleave for the
        # same decision_id, or the duplicate check could be raced.
        self._lock = threading.Lock()

    def submit(self, decision: Decision, decision_id: str, account: AccountState) -> OrderResult:
        with self._lock:
            return self._submit(decision, decision_id, account)

    def _submit(self, decision: Decision, decision_id: str, account: AccountState) -> OrderResult:
        def result(status, reason, output: dict | None = None) -> OrderResult:
            output = output or {}
            return OrderResult(
                decision_id=decision_id,
                side=decision.action,
                symbol=decision.symbol,
                quantity=decision.quantity,
                status=status,
                reason=reason,
                order_no=output.get("ODNO"),
                order_org_no=output.get("KRX_FWDG_ORD_ORGNO"),
                order_time=output.get("ORD_TMD"),
            )

        verdict = self._risk.evaluate(decision, decision_id, account)
        if not verdict.approved:
            logger.info("order rejected by risk manager: decision_id=%s reason=%s", decision_id, verdict.reason)
            return result("REJECTED_BY_RISK", verdict.reason)

        try:
            output = self._client.place_cash_order(
                side=decision.action, symbol=decision.symbol, quantity=decision.quantity
            )
        except KisOrderUncertainError as exc:
            # Possibly placed: lock out this decision_id and stop all further
            # orders until a human checks the account (Phase 8 will add
            # automatic order-status reconciliation).
            self._risk.mark_executed(decision_id)
            self._risk.engage_emergency_stop(
                f"order outcome unknown for decision_id={decision_id!r}: {exc}"
            )
            logger.error("order outcome unknown: decision_id=%s error=%s", decision_id, exc)
            return result("UNKNOWN", str(exc))
        except KisApiError as exc:
            # Explicit rejection from KIS -- definitely not placed, so the
            # decision_id is not locked out.
            logger.warning("order rejected by KIS: decision_id=%s error=%s", decision_id, exc)
            return result("REJECTED_BY_BROKER", str(exc))

        self._risk.mark_executed(decision_id)
        logger.info("order accepted by KIS: decision_id=%s order_no=%s", decision_id, output.get("ODNO"))
        return result("SUBMITTED", "accepted by KIS (not yet confirmed filled)", output)
