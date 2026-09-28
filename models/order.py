"""Order / execution data models.

Phase 7 scope: the outcome of one submit attempt (OrderResult). An
accepted order (KIS returned an order number) is NOT the same as a fill
-- fill confirmation (체결 확인) and Order/Fill models linked to it come
in Phase 8.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# SUBMITTED: KIS accepted the order and returned an order number (not
#   necessarily filled yet).
# REJECTED_BY_RISK: risk.risk_manager refused it -- never reached KIS.
# REJECTED_BY_BROKER: KIS answered with an explicit error (rt_cd != '0')
#   -- definitely not placed.
# UNKNOWN: the request may or may not have reached KIS (timeout, lost or
#   unparseable response). Must be treated as possibly placed.
OrderStatus = Literal["SUBMITTED", "REJECTED_BY_RISK", "REJECTED_BY_BROKER", "UNKNOWN"]


@dataclass(frozen=True)
class OrderResult:
    decision_id: str
    side: str
    symbol: str
    quantity: int
    status: OrderStatus
    reason: str
    order_no: str | None = None  # KIS ODNO
    order_org_no: str | None = None  # KIS KRX_FWDG_ORD_ORGNO (needed to cancel/amend)
    order_time: str | None = None  # KIS ORD_TMD, HHMMSS

    @property
    def submitted(self) -> bool:
        return self.status == "SUBMITTED"
