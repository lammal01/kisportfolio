"""Order execution against the KIS Open API.

OrderExecutor.submit() is the only way to place an order, and it runs
risk.risk_manager.RiskManager.evaluate() itself before touching KIS --
there is no public method that takes an already-"approved" order, so a
caller can't skip or fake the risk check.

An accepted order (KIS returned an order number) is not a fill -- use
confirm_fill() to see what KIS's 일별주문체결조회 actually reports. HTTP
200 alone is never taken as success.

An UNKNOWN outcome engages the emergency stop; reconcile_unknown() looks
the order up in KIS's order list to tell a human whether it was placed,
but never releases the stop itself -- a person does that, explicitly.

Real trading is refused outright until Phase 11 (장시간 Paper Trading)
has been validated: constructing an OrderExecutor on a real-trading
KisClient raises, regardless of what's in .env.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from broker.kis_client import _KST, KisApiError, KisClient, KisOrderUncertainError
from models.decision import Decision
from models.order import OrderFill, OrderResult, is_original_order
from risk.risk_manager import AccountState, RiskManager

logger = logging.getLogger(__name__)


class RealTradingDisabledError(RuntimeError):
    """Raised when an OrderExecutor is built on a real-trading KisClient."""


# An UNKNOWN order is matched against KIS orders registered no earlier
# than this many seconds before the request was sent (local/KIS clock
# skew). No upper bound: a slow KIS server has been seen registering an
# order 31s after a timed-out request, and after an UNKNOWN the emergency
# stop keeps this program from placing anything later that could be
# confused with it.
_RECONCILE_CLOCK_SKEW_SECONDS = 60


@dataclass(frozen=True)
class Reconciliation:
    """PLACED: exactly one matching order found at KIS (fill = its state).
    NOT_FOUND: no matching order *yet* -- not proof it wasn't placed: KIS
        has been seen registering an order ~30s after a timed-out request.
    AMBIGUOUS: several candidates -- can't tell which, a human must check.
    """

    outcome: Literal["PLACED", "NOT_FOUND", "AMBIGUOUS"]
    candidates: tuple[OrderFill, ...]

    @property
    def fill(self) -> OrderFill | None:
        return self.candidates[0] if self.outcome == "PLACED" else None


def _seconds(hhmmss: str) -> int:
    return int(hhmmss[:2]) * 3600 + int(hhmmss[2:4]) * 60 + int(hhmmss[4:6])


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
        # Order numbers this executor already knows about, so reconciling an
        # UNKNOWN order never mistakes one of them for the lost one.
        self._known_order_nos: set[str] = set()

    def submit(self, decision: Decision, decision_id: str, account: AccountState) -> OrderResult:
        with self._lock:
            return self._submit(decision, decision_id, account)

    def _submit(self, decision: Decision, decision_id: str, account: AccountState) -> OrderResult:
        requested_at: str | None = None

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
                requested_at=requested_at,
            )

        verdict = self._risk.evaluate(decision, decision_id, account)
        if not verdict.approved:
            logger.info("order rejected by risk manager: decision_id=%s reason=%s", decision_id, verdict.reason)
            return result("REJECTED_BY_RISK", verdict.reason)

        requested_at = datetime.now(_KST).strftime("%H%M%S")
        try:
            output = self._client.place_cash_order(
                side=decision.action, symbol=decision.symbol, quantity=decision.quantity
            )
        except KisOrderUncertainError as exc:
            # Possibly placed: lock out this decision_id and stop all further
            # orders until a human checks the account (reconcile_unknown()
            # helps with that, but never lifts the stop).
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
        self._known_order_nos.add(output["ODNO"])
        logger.info("order accepted by KIS: decision_id=%s order_no=%s", decision_id, output.get("ODNO"))
        return result("SUBMITTED", "accepted by KIS (not yet confirmed filled)", output)

    def _todays_orders(self) -> list[OrderFill]:
        return [OrderFill.from_kis(row) for row in self._client.get_daily_orders() if is_original_order(row)]

    def check_fill(self, order_no: str) -> OrderFill | None:
        """Current fill state of one of today's orders, or None if KIS
        doesn't list it (yet)."""
        for fill in self._todays_orders():
            if fill.order_no == order_no:
                return fill
        return None

    def confirm_fill(
        self, result: OrderResult, attempts: int = 5, interval_seconds: float = 2.0
    ) -> OrderFill | None:
        """Polls KIS until a SUBMITTED order is no longer open (filled,
        cancelled or rejected) or `attempts` run out; returns the last
        state seen (possibly still PENDING/PARTIALLY_FILLED, or None if
        KIS never listed it). Only for SUBMITTED results."""
        if not result.submitted or not result.order_no:
            raise ValueError(f"confirm_fill needs a SUBMITTED result, got {result.status}")
        fill = None
        for attempt in range(attempts):
            if attempt:
                time.sleep(interval_seconds)
            fill = self.check_fill(result.order_no)
            if fill is not None and not fill.is_open:
                break
        return fill

    def reconcile_unknown(self, result: OrderResult) -> Reconciliation:
        """Looks for the order behind an UNKNOWN result in today's KIS
        orders: same symbol/side/quantity, registered no earlier than
        _RECONCILE_CLOCK_SKEW_SECONDS before the request, and not an order
        number this executor already knows. Reports only -- the emergency stop
        stays engaged either way."""
        if result.status != "UNKNOWN" or not result.requested_at:
            raise ValueError(f"reconcile_unknown needs an UNKNOWN result, got {result.status}")
        requested = _seconds(result.requested_at)
        candidates = tuple(
            fill
            for fill in self._todays_orders()
            if fill.symbol == result.symbol
            and fill.side == result.side
            and fill.ordered_qty == result.quantity
            and fill.order_no not in self._known_order_nos
            and _seconds(fill.order_time) >= requested - _RECONCILE_CLOCK_SKEW_SECONDS
        )
        if not candidates:
            return Reconciliation("NOT_FOUND", candidates)
        if len(candidates) > 1:
            return Reconciliation("AMBIGUOUS", candidates)
        self._known_order_nos.add(candidates[0].order_no)
        return Reconciliation("PLACED", candidates)
