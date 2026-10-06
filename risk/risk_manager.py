"""Deterministic, AI-independent risk validation.

Every AI decision must pass through here before it can reach
broker.order_executor. This module must not be influenced by AI output
in any way that could weaken its own limits (position size caps, daily
loss limits, cooldowns, duplicate-order checks, emergency stop, etc).
Rejections must be logged with a reason.

RiskManager does not call the KIS API itself -- callers (market/broker)
gather account state and pass it in as AccountState, keeping this module
pure and independently testable without network access.

State that must survive a restart (executed decision_ids, emergency
stop) goes through an optional RiskStateStore (memory.trade_memory
implements it) -- loaded at construction, written through on change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from config.settings import Settings, get_settings
from models.decision import Decision

logger = logging.getLogger(__name__)

# Conservative defaults; override via RISK_MAX_POSITION_PCT /
# RISK_DAILY_LOSS_LIMIT_PCT / RISK_MAX_TOTAL_EXPOSURE_PCT in .env, never by
# editing these.
DEFAULT_MAX_POSITION_PCT = 0.10
DEFAULT_DAILY_LOSS_LIMIT_PCT = 0.03
DEFAULT_MAX_TOTAL_EXPOSURE_PCT = 0.90


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
    open_order_count: 대상 종목의 KIS 미체결 주문 수. 0보다 크면 새 주문을
        거부 -- decision_id 기록은 메모리에만 있어 재시작하면 사라지지만,
        브로커의 미체결 목록은 남으므로 재시작 후에도 주문이 쌓이지 않음.
        None(확인 못 함)이면 거부.
    avg_cost: 대상 종목 매입평균가 (보유 없으면 None). 한도 계산에는 쓰지
        않음 -- SELL의 실현손익 기록(memory.trade_memory)용으로 전달만 함.
    stock_value: 전 종목 보유 주식 평가금액 합계. 총 투자 한도 계산의 기준.
        None(확인 못 함)이면 매수 거부.
    buying_power: 대상 종목 시장가 매수 시 KIS가 알려준 주문가능금액(원).
        None(확인 못 함)이면 매수 거부.
    """

    symbol: str
    total_equity: float
    day_start_equity: float
    current_price: float
    held_quantity: int = 0
    open_order_count: int | None = None
    avg_cost: float | None = None
    stock_value: float | None = None
    buying_power: float | None = None


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    reason: str
    decision: Decision


@dataclass(frozen=True)
class RiskLimits:
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT
    daily_loss_limit_pct: float = DEFAULT_DAILY_LOSS_LIMIT_PCT
    max_total_exposure_pct: float = DEFAULT_MAX_TOTAL_EXPOSURE_PCT

    @staticmethod
    def from_settings(settings: Settings | None = None) -> "RiskLimits":
        settings = settings or get_settings()
        return RiskLimits(
            max_position_pct=settings.risk_max_position_pct,
            daily_loss_limit_pct=settings.risk_daily_loss_limit_pct,
            max_total_exposure_pct=settings.risk_max_total_exposure_pct,
        )


class RiskStateStore(Protocol):
    def load_executed_decision_ids(self) -> set[str]: ...
    def save_executed_decision_id(self, decision_id: str) -> None: ...
    def load_emergency_stop(self) -> str | None: ...
    def save_emergency_stop(self, reason: str | None) -> None: ...


class RiskManager:
    """The only path from an AI Decision to an executable order.

    Never trusts the AI's own risk_score/confidence -- approval depends
    only on AccountState and the deterministic limits below. There must
    be no code path that calls broker.order_executor without first going
    through evaluate() and getting approved=True back.
    """

    def __init__(self, limits: RiskLimits | None = None, store: RiskStateStore | None = None):
        self._limits = limits or RiskLimits.from_settings()
        self._store = store
        self._executed_decision_ids: set[str] = set()
        self._emergency_stop = False
        self._emergency_stop_reason: str | None = None
        if store is not None:
            # A load failure propagates: better not to start at all than to
            # start without knowing about a previous emergency stop.
            self._executed_decision_ids = set(store.load_executed_decision_ids())
            reason = store.load_emergency_stop()
            if reason is not None:
                self._emergency_stop = True
                self._emergency_stop_reason = reason

    def _refresh_emergency_stop(self) -> None:
        """Picks up an emergency stop engaged by another process (e.g.
        Telegram /stop) right before each evaluation. Only ever engages --
        a stop is never cleared by re-reading, and a failed read counts as
        engaged."""
        if self._store is None:
            return
        try:
            reason = self._store.load_emergency_stop()
        except Exception as exc:
            logger.error("could not re-read emergency stop state: %s", exc)
            reason = f"비상 정지 상태를 읽지 못함: {exc}"
        if reason is not None and not self._emergency_stop:
            self._emergency_stop = True
            self._emergency_stop_reason = reason

    def _persist(self, action: str, fn, *args) -> None:
        """Writes state through to the store. A failed write never raises
        (it can happen right after an order was placed) -- it engages the
        emergency stop in memory instead, since what's on disk is now
        behind what actually happened."""
        if self._store is None:
            return
        try:
            fn(*args)
        except Exception as exc:
            logger.error("risk state persistence failed (%s): %s", action, exc)
            self._emergency_stop = True
            self._emergency_stop_reason = f"리스크 상태 저장 실패 ({action}): {exc}"

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
        if self._store is not None:
            self._persist("engage emergency stop", self._store.save_emergency_stop, reason)

    @property
    def emergency_stop_reason(self) -> str | None:
        return self._emergency_stop_reason

    def disengage_emergency_stop(self) -> None:
        """Clears the store first: if that fails the stop stays engaged."""
        if self._store is not None:
            self._store.save_emergency_stop(None)
        self._emergency_stop = False
        self._emergency_stop_reason = None

    def evaluate(self, decision: Decision, decision_id: str, account: AccountState) -> RiskVerdict:
        """Approves or rejects one Decision. Pure/deterministic given its
        inputs -- same decision_id/decision/account always yields the same
        verdict, except for the stateful duplicate-decision_id check."""

        def reject(reason: str) -> RiskVerdict:
            return RiskVerdict(approved=False, reason=reason, decision=decision)

        self._refresh_emergency_stop()
        if self._emergency_stop:
            return reject(f"비상 정지 중: {self._emergency_stop_reason}")

        if decision.action == "HOLD":
            return reject("HOLD(관망)는 주문 대상이 아님")

        if decision.symbol != account.symbol:
            return reject(
                f"AI 판단 종목 {decision.symbol}이(가) 계좌 스냅샷 종목 "
                f"{account.symbol}과(와) 다름"
            )

        if account.open_order_count is None:
            return reject(f"{decision.symbol} 미체결 주문 상태를 확인하지 못해 주문 거부")

        if account.open_order_count > 0:
            return reject(
                f"{decision.symbol}에 미체결 주문 {account.open_order_count}건이 이미 있어 "
                "추가 주문 거부"
            )

        if decision_id in self._executed_decision_ids:
            return reject(f"이미 실행된 decision_id {decision_id} -- 중복 주문 거부")

        if account.current_price <= 0:
            return reject(f"{decision.symbol} 현재가 값이 잘못됨 (current_price={account.current_price!r})")

        if account.total_equity <= 0:
            return reject(f"총평가금액 값이 잘못됨 (total_equity={account.total_equity!r})")

        if decision.action == "SELL" and decision.quantity > account.held_quantity:
            return reject(
                f"매도 수량 {decision.quantity}주가 {decision.symbol} 보유 수량 "
                f"{account.held_quantity}주를 초과"
            )

        order_value = decision.quantity * account.current_price
        max_position_value = account.total_equity * self._limits.max_position_pct
        if decision.action == "BUY" and order_value > max_position_value:
            return reject(
                f"매수 금액 {order_value:,.0f}원이 포지션 한도 {max_position_value:,.0f}원 "
                f"(총평가 {account.total_equity:,.0f}원의 {self._limits.max_position_pct:.0%})을 초과"
            )

        if decision.action == "BUY":
            if account.stock_value is None or account.buying_power is None:
                return reject(
                    f"{decision.symbol} 보유 평가금액/주문가능금액을 확인하지 못해 매수 거부"
                )

            # Written as `not (a <= b)` so a NaN from a bad response rejects
            # instead of slipping through every comparison.
            max_total_exposure = account.total_equity * self._limits.max_total_exposure_pct
            exposure_after = account.stock_value + order_value
            if account.stock_value < 0 or not (exposure_after <= max_total_exposure):
                return reject(
                    f"매수 후 총 투자금액 {exposure_after:,.0f}원(현재 보유 평가 {account.stock_value:,.0f}원 "
                    f"+ 이번 주문 {order_value:,.0f}원)이 총 투자 한도 {max_total_exposure:,.0f}원 "
                    f"(총평가 {account.total_equity:,.0f}원의 {self._limits.max_total_exposure_pct:.0%})을 초과"
                )

            if not (order_value <= account.buying_power):
                return reject(
                    f"매수 금액 {order_value:,.0f}원이 주문가능금액 {account.buying_power:,.0f}원을 초과"
                )

            if account.day_start_equity <= 0:
                return reject(f"당일 시작 평가금액 값이 잘못됨 (day_start_equity={account.day_start_equity!r})")
            daily_pnl_pct = (account.total_equity - account.day_start_equity) / account.day_start_equity
            if daily_pnl_pct <= -self._limits.daily_loss_limit_pct:
                return reject(
                    f"당일 손실 {daily_pnl_pct:.2%}이(가) 한도 -{self._limits.daily_loss_limit_pct:.0%}를 "
                    "넘어 오늘 신규 매수 차단 (매도는 허용)"
                )

        return RiskVerdict(approved=True, reason="한도 이내", decision=decision)

    def max_buy_value(self, account: AccountState) -> float:
        """Largest single BUY (won) that evaluate() would accept right now
        -- the smallest of the position limit, the room left under the
        total-exposure limit and the 주문가능금액. 0 when the exposure or
        cash can't be verified (evaluate() rejects those BUYs too). Used to
        tell the AI its real budget so it doesn't propose orders that are
        sure to be rejected; evaluate() stays the actual gate."""
        if account.stock_value is None or account.buying_power is None:
            return 0.0
        caps = (
            account.total_equity * self._limits.max_position_pct,
            account.total_equity * self._limits.max_total_exposure_pct - account.stock_value,
            account.buying_power,
        )
        return max(0.0, min(caps))

    def mark_executed(self, decision_id: str) -> None:
        """Call only after broker.order_executor has actually submitted
        the order for this decision_id -- not at evaluate() time, so an
        approval that never got executed doesn't get permanently locked
        out as a false 'duplicate'."""
        self._executed_decision_ids.add(decision_id)
        if self._store is not None:
            self._persist("mark executed", self._store.save_executed_decision_id, decision_id)
