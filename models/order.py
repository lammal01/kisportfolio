"""Order / execution data models.

OrderResult (Phase 7): the outcome of one submit attempt. An accepted
order (KIS returned an order number) is NOT the same as a fill.
OrderFill (Phase 8): an order's actual fill state as reported by KIS's
일별주문체결조회, which is what "was this filled?" must be decided from.
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
    # Local KST time (HHMMSS) the order request was sent -- lets an UNKNOWN
    # result be matched against KIS's order list later.
    requested_at: str | None = None

    @property
    def submitted(self) -> bool:
        return self.status == "SUBMITTED"


# FILLED: 전량 체결. PARTIALLY_FILLED: 일부 체결, 잔량 대기 중.
# PENDING: 미체결 대기. CANCELLED: 취소됨 (일부 체결 후 취소 포함).
# REJECTED: 거부 수량 있음.
FillStatus = Literal["FILLED", "PARTIALLY_FILLED", "PENDING", "CANCELLED", "REJECTED"]

_SIDE_CODES = {"01": "SELL", "02": "BUY"}


class OrderFillParseError(ValueError):
    """A KIS order row was missing/invalid -- never guess its fill state."""


@dataclass(frozen=True)
class OrderFill:
    order_no: str
    side: str
    symbol: str
    ordered_qty: int
    filled_qty: int
    remaining_qty: int
    rejected_qty: int
    cancelled: bool
    avg_price: float
    order_time: str

    @property
    def status(self) -> FillStatus:
        if self.filled_qty >= self.ordered_qty:
            return "FILLED"
        if self.rejected_qty > 0:
            return "REJECTED"
        if self.cancelled:
            return "CANCELLED"
        return "PARTIALLY_FILLED" if self.filled_qty > 0 else "PENDING"

    @property
    def is_open(self) -> bool:
        """Still waiting at the exchange -- more could fill."""
        return self.status in ("PENDING", "PARTIALLY_FILLED")

    @staticmethod
    def from_kis(row: dict) -> "OrderFill":
        """Parses one inquire-daily-ccld output1 row."""
        try:
            side = _SIDE_CODES[row["sll_buy_dvsn_cd"]]
            return OrderFill(
                order_no=row["odno"],
                side=side,
                symbol=row["pdno"],
                ordered_qty=int(row["ord_qty"]),
                filled_qty=int(row["tot_ccld_qty"]),
                remaining_qty=int(row["rmn_qty"]),
                rejected_qty=int(row.get("rjct_qty") or 0),
                cancelled=row.get("cncl_yn") == "Y",
                avg_price=float(row.get("avg_prvs") or 0),
                order_time=row["ord_tmd"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OrderFillParseError(f"unparseable KIS order row: {exc!r} in {row!r}") from exc


def is_original_order(row: dict) -> bool:
    """False for the separate rows KIS adds when an order is amended or
    cancelled (they carry the original order's number in orgn_odno)."""
    return not (row.get("orgn_odno") or "").strip("0")
