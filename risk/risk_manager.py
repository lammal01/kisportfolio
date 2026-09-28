"""Deterministic, AI-independent risk validation.

Every AI decision must pass through here before it can reach
broker.order_executor. This module must not be influenced by AI output
in any way that could weaken its own limits (position size caps, daily
loss limits, cooldowns, duplicate-order checks, emergency stop, etc).
Rejections must be logged with a reason.

RiskManager does not call the KIS API itself -- callers (market/broker)
gather account state and pass it in as AccountState, keeping this module
pure and independently testable without network access.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config.settings import Settings, get_settings
from models.decision import Decision

# Conservative defaults; override via RISK_MAX_POSITION_PCT /
# RISK_DAILY_LOSS_LIMIT_PCT in .env, never by editing these.
DEFAULT_MAX_POSITION_PCT = 0.10
DEFAULT_DAILY_LOSS_LIMIT_PCT = 0.03


@dataclass(frozen=True)
class AccountState:
    """Account snapshot a caller must supply for one evaluate() call.

    symbol: 이 스냅샷(current_price/held_quantity)이 가리키는 종목. Decision의
        symbol과 다르면 거부 -- 다른 종목 가격으로 한도를 계산하지 않기 위함.
    total_equity: 계좌 총평가금액 (예수금 + 보유종목 평가금액), position
        size limit의 기준.
    day_start_equity: 당일 장 시작 시점 총평가금액, 일일 손실 한도 계산의
        기준. (영속 저장은 아직 없음 -- Phase 9 Trade Memory에서 붙일 것.)
    current_price: 대상 종목 현재가. 주문금액 = quantity * current_price.
    held_quantity: 대상 종목 보유 수량 (SELL이 보유량을 넘지 않는지 체크).
    """

    symbol: str
    total_equity: float
    day_start_equity: float
    current_price: float
    held_quantity: int = 0


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    reason: str
    decision: Decision


@dataclass(frozen=True)
class RiskLimits:
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT
    daily_loss_limit_pct: float = DEFAULT_DAILY_LOSS_LIMIT_PCT

    @staticmethod
    def from_settings(settings: Settings | None = None) -> "RiskLimits":
        settings = settings or get_settings()
        return RiskLimits(
            max_position_pct=settings.risk_max_position_pct,
            daily_loss_limit_pct=settings.risk_daily_loss_limit_pct,
        )


class RiskManager:
    """The only path from an AI Decision to an executable order.

    Never trusts the AI's own risk_score/confidence -- approval depends
    only on AccountState and the deterministic limits below. There must
    be no code path that calls broker.order_executor without first going
    through evaluate() and getting approved=True back.
    """

    def __init__(self, limits: RiskLimits | None = None):
        self._limits = limits or RiskLimits.from_settings()
        self._executed_decision_ids: set[str] = set()
        self._emergency_stop = False
        self._emergency_stop_reason: str | None = None

    @property
    def emergency_stop_engaged(self) -> bool:
        return self._emergency_stop

    def engage_emergency_stop(self, reason: str) -> None:
        """Blocks every subsequent order immediately, regardless of
        limits. Stays engaged until disengage_emergency_stop() is called
        explicitly -- never clears itself, so a real incident can't be
        silently forgotten by e.g. a process restart re-reading a flag."""
        self._emergency_stop = True
        self._emergency_stop_reason = reason

    def disengage_emergency_stop(self) -> None:
        self._emergency_stop = False
        self._emergency_stop_reason = None

    def evaluate(self, decision: Decision, decision_id: str, account: AccountState) -> RiskVerdict:
        """Approves or rejects one Decision. Pure/deterministic given its
        inputs -- same decision_id/decision/account always yields the same
        verdict, except for the stateful duplicate-decision_id check."""

        def reject(reason: str) -> RiskVerdict:
            return RiskVerdict(approved=False, reason=reason, decision=decision)

        if self._emergency_stop:
            return reject(f"emergency stop engaged: {self._emergency_stop_reason}")

        if decision.action == "HOLD":
            return reject("HOLD is never an executable order")

        if decision.symbol != account.symbol:
            return reject(
                f"decision symbol {decision.symbol!r} does not match account snapshot "
                f"symbol {account.symbol!r}"
            )

        if decision_id in self._executed_decision_ids:
            return reject(f"decision_id {decision_id!r} already executed -- refusing duplicate order")

        if account.current_price <= 0:
            return reject(f"invalid current_price={account.current_price!r} for {decision.symbol!r}")

        if account.total_equity <= 0:
            return reject(f"invalid total_equity={account.total_equity!r}")

        if decision.action == "SELL" and decision.quantity > account.held_quantity:
            return reject(
                f"SELL quantity {decision.quantity} exceeds held quantity "
                f"{account.held_quantity} for {decision.symbol!r}"
            )

        order_value = decision.quantity * account.current_price
        max_position_value = account.total_equity * self._limits.max_position_pct
        if decision.action == "BUY" and order_value > max_position_value:
            return reject(
                f"BUY order value {order_value:,.0f} exceeds position limit "
                f"{max_position_value:,.0f} ({self._limits.max_position_pct:.0%} of "
                f"equity {account.total_equity:,.0f})"
            )

        if decision.action == "BUY":
            if account.day_start_equity <= 0:
                return reject(f"invalid day_start_equity={account.day_start_equity!r}")
            daily_pnl_pct = (account.total_equity - account.day_start_equity) / account.day_start_equity
            if daily_pnl_pct <= -self._limits.daily_loss_limit_pct:
                return reject(
                    f"daily loss {daily_pnl_pct:.2%} exceeds limit "
                    f"-{self._limits.daily_loss_limit_pct:.0%} -- new BUY orders blocked "
                    "for today (SELL is still allowed)"
                )

        return RiskVerdict(approved=True, reason="within limits", decision=decision)

    def mark_executed(self, decision_id: str) -> None:
        """Call only after broker.order_executor has actually submitted
        the order for this decision_id -- not at evaluate() time, so an
        approval that never got executed doesn't get permanently locked
        out as a false 'duplicate'."""
        self._executed_decision_ids.add(decision_id)
