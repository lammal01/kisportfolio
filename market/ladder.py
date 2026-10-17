"""Deterministic scale-in ladder (분할매수) and exits (손절/익절).

Python-computed, not LLM: when a watched symbol is falling, estimate how
much further it may drop (expected_drop_pct), then buy in tranches
between 60% and 100% of that drop, heavier toward the bottom. Below
STOP_MULTIPLE x the drop the estimate was wrong and the position is
sold. Held positions also get a hard stop and a take-profit.

e.g. price 100,000, expected drop 5% -> buy 20% of the budget at 97,000,
30% at 96,000, 50% at 95,000 (avg ~95,700), stop at 93,000.

This module only proposes Decisions -- they go through run_once ->
OrderExecutor -> RiskManager like every AI decision, so every limit,
the emergency stop and the pause still apply.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import time as dtime

from market.indicators import Indicators
from models.decision import Decision

LEVEL_FRACTIONS = (0.6, 0.8, 1.0)  # of the expected drop
LEVEL_WEIGHTS = (0.2, 0.3, 0.5)  # of the plan budget
STOP_MULTIPLE = 1.4  # stop = ref * (1 - 1.4 x expected drop)
MIN_DROP_PCT = 2.0
MAX_DROP_PCT = 8.0
ATR_DAYS = 14
SUPPORT_DAYS = 20
BOLLINGER_K = 2.0

# A plan is only drawn up for a symbol that is actually falling, during
# this window (orders on an existing plan continue until the loop's
# 15:20 cutoff).
PLAN_START = dtime(9, 30)
PLAN_END = dtime(14, 30)
PLAN_TRIGGER_CHANGE_PCT = -1.0  # down at least this much vs. yesterday's close

# Marks in Decision.reason[0] -- also how "already took profit today" is found.
LADDER_TAG = "[분할매수]"
STOP_TAG = "[손절]"
TAKE_PROFIT_TAG = "[익절]"


def _num(row: dict, key: str) -> float:
    return float(row[key])


def atr_pct(rows: list[dict]) -> float | None:
    """Average true range of the last ATR_DAYS completed sessions, as a %
    of the last close. rows: KisClient.get_daily_prices(), newest first
    (rows[0] is today's live row and is skipped)."""
    done = rows[1:]
    if len(done) < ATR_DAYS + 1:
        return None
    trs = []
    for i in range(ATR_DAYS):
        high, low = _num(done[i], "stck_hgpr"), _num(done[i], "stck_lwpr")
        prev_close = _num(done[i + 1], "stck_clpr")
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    last_close = _num(done[0], "stck_clpr")
    if last_close <= 0:
        return None
    return sum(trs) / ATR_DAYS / last_close * 100


def nearest_support(rows: list[dict], price: float) -> float | None:
    """Closest support below `price`: the 20-session low (completed
    sessions) or the lower Bollinger band (20, 2). None if price is
    already below both."""
    candidates = []
    done = rows[1 : 1 + SUPPORT_DAYS]
    if len(done) == SUPPORT_DAYS:
        candidates.append(min(_num(r, "stck_lwpr") for r in done))
    closes = [_num(r, "stck_clpr") for r in rows[:SUPPORT_DAYS]]
    if len(closes) == SUPPORT_DAYS:
        mean = sum(closes) / SUPPORT_DAYS
        std = math.sqrt(sum((c - mean) ** 2 for c in closes) / SUPPORT_DAYS)
        candidates.append(mean - BOLLINGER_K * std)
    below = [c for c in candidates if c < price * 0.995]
    return max(below) if below else None


@dataclass(frozen=True)
class DropEstimate:
    drop_pct: float  # expected further drop from now, clamped to MIN..MAX
    atr_pct: float
    support: float | None
    basis: str  # Korean, why this number


def estimate_drop(rows: list[dict], price: float) -> DropEstimate | None:
    """Expected further drop = the smaller of one ATR and the distance to
    the nearest support. None if there isn't enough history."""
    atr = atr_pct(rows)
    if atr is None or price <= 0:
        return None
    drop, basis = atr, f"ATR{ATR_DAYS} {atr:.1f}%"
    support = nearest_support(rows, price)
    if support is not None:
        distance = (price - support) / price * 100
        if distance < drop:
            drop, basis = distance, f"지지선 {support:,.0f}원까지 {distance:.1f}%"
    clamped = min(max(drop, MIN_DROP_PCT), MAX_DROP_PCT)
    if clamped != drop:
        basis += f" -> {clamped:.1f}%로 보정"
    return DropEstimate(round(clamped, 2), round(atr, 2), support, basis)


@dataclass
class Level:
    price: float
    weight: float
    filled: bool = False


@dataclass
class LadderPlan:
    symbol: str
    trade_date: str  # YYYYMMDD -- a plan only lives for one session
    ref_price: float
    drop_pct: float
    stop_price: float
    budget: float  # won, for all levels together
    basis: str
    levels: list[Level] = field(default_factory=list)
    status: str = "active"  # active / stopped

    @property
    def any_filled(self) -> bool:
        return any(l.filled for l in self.levels)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def from_json(text: str) -> "LadderPlan":
        data = json.loads(text)
        data["levels"] = [Level(**l) for l in data["levels"]]
        return LadderPlan(**data)

    def describe(self) -> str:
        levels = ", ".join(f"{l.price:,.0f}원 {l.weight:.0%}" for l in self.levels)
        return (
            f"기준 {self.ref_price:,.0f}원, 예상 하락 {self.drop_pct:.1f}% ({self.basis})\n"
            f"매수: {levels} (예산 {self.budget:,.0f}원)\n손절: {self.stop_price:,.0f}원 이하"
        )


def build_plan(symbol: str, trade_date: str, ref_price: float, est: DropEstimate, budget: float) -> LadderPlan:
    d = est.drop_pct / 100
    return LadderPlan(
        symbol=symbol,
        trade_date=trade_date,
        ref_price=ref_price,
        drop_pct=est.drop_pct,
        stop_price=round(ref_price * (1 - STOP_MULTIPLE * d)),
        budget=budget,
        basis=est.basis,
        levels=[Level(round(ref_price * (1 - f * d)), w) for f, w in zip(LEVEL_FRACTIONS, LEVEL_WEIGHTS)],
    )


def should_plan(ind: Indicators, now_time: dtime) -> bool:
    """A symbol is a ladder candidate when it is falling today or trading
    below a falling short-term trend."""
    if not (PLAN_START <= now_time <= PLAN_END):
        return False
    falling_today = ind.change_pct <= PLAN_TRIGGER_CHANGE_PCT
    downtrend = ind.ma5 is not None and ind.ma20 is not None and ind.price < ind.ma5 < ind.ma20
    return falling_today or downtrend


def due_levels(plan: LadderPlan, price: float) -> list[int]:
    if plan.status != "active" or price <= plan.stop_price:
        return []
    return [i for i, l in enumerate(plan.levels) if not l.filled and price <= l.price]


def ladder_decision(plan: LadderPlan, levels: list[int], price: float) -> Decision | None:
    weight = sum(plan.levels[i].weight for i in levels)
    quantity = int(plan.budget * weight // price)
    if quantity < 1:
        return None
    names = "+".join(f"{i + 1}차" for i in levels)
    return Decision(
        action="BUY",
        symbol=plan.symbol,
        quantity=quantity,
        confidence=1.0,
        reason=(
            f"{LADDER_TAG} {names} 매수 -- 현재가 {price:,.0f}원이 매수가 "
            f"{min(plan.levels[i].price for i in levels):,.0f}원 이하",
            f"예상 하락 {plan.drop_pct:.1f}% ({plan.basis}), 기준가 {plan.ref_price:,.0f}원",
            f"손절가 {plan.stop_price:,.0f}원",
        ),
        stop_loss=plan.stop_price,
    )


def exit_decision(
    symbol: str,
    price: float,
    held: int,
    avg_cost: float | None,
    plan: LadderPlan | None,
    hard_stop_pct: float,
    take_profit_pct: float,
    take_profits_today: int,
) -> Decision | None:
    """Stop: price at/below the plan's stop (once a ladder level filled) or
    hard_stop_pct below the average cost -> sell everything. Take-profit:
    each take_profit_pct step above the average cost (+4%, +8%, +12%, ...)
    -> sell half of what is left, once per step per day."""
    if held <= 0 or not avg_cost or avg_cost <= 0:
        return None
    pnl_pct = (price - avg_cost) / avg_cost * 100

    def sell(quantity: int, *reason: str) -> Decision:
        return Decision(action="SELL", symbol=symbol, quantity=quantity, confidence=1.0, reason=reason)

    if plan is not None and plan.status == "active" and plan.any_filled and price <= plan.stop_price:
        return sell(
            held,
            f"{STOP_TAG} 분할매수 손절가 {plan.stop_price:,.0f}원 이탈 (현재 {price:,.0f}원) -- 전량 매도",
            f"예상 하락 {plan.drop_pct:.1f}%를 넘어 예측이 틀린 것으로 판단, 평균단가 대비 {pnl_pct:+.1f}%",
        )
    if pnl_pct <= -hard_stop_pct * 100:
        return sell(
            held,
            f"{STOP_TAG} 평균단가 {avg_cost:,.0f}원 대비 {pnl_pct:+.1f}% -- 손절 기준 -{hard_stop_pct:.0%} 도달, 전량 매도",
        )
    step = int(pnl_pct // (take_profit_pct * 100)) if take_profit_pct > 0 else 0
    if step > take_profits_today:
        return sell(
            math.ceil(held / 2),
            f"{TAKE_PROFIT_TAG} 평균단가 {avg_cost:,.0f}원 대비 {pnl_pct:+.1f}% -- 익절 {step}단계 "
            f"(+{take_profit_pct * step:.0%}) 도달, 오늘 {take_profits_today}회 익절, 남은 물량 절반 매도",
        )
    return None
