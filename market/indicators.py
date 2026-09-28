"""Deterministic technical indicators computed in Python (not via LLM).

Moving averages, RSI, volume vs. usual, and a moving-average cross, all
from KisClient.get_daily_prices() rows (newest first, today's row live
during the session). These feed market.signal_filter so the LLM is only
called when a Python-computed signal warrants it, and they're passed to
the AI as part of its snapshot.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

SESSION_OPEN_MINUTES = 9 * 60  # 09:00 KST
SESSION_LENGTH_MINUTES = 390  # 09:00-15:30


class IndicatorError(ValueError):
    """Not enough / malformed price history to compute indicators."""


def sma(values: list[float], n: int) -> float | None:
    """Simple moving average of the first n values (newest first)."""
    if len(values) < n:
        return None
    return sum(values[:n]) / n


def rsi(values: list[float], n: int = 14) -> float | None:
    """Wilder's RSI over closes given newest first. None if too short."""
    if len(values) < n + 1:
        return None
    closes = list(reversed(values))  # oldest first
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        avg_gain = (avg_gain * (n - 1) + g) / n
        avg_loss = (avg_loss * (n - 1) + l) / n
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


@dataclass(frozen=True)
class Indicators:
    date: str  # stck_bsop_date of the newest row
    is_trading_day: bool  # newest row is today -> market is open today
    price: float
    change_pct: float  # vs. previous close
    ma5: float | None
    ma20: float | None
    prev_ma5: float | None  # as of yesterday's close
    prev_ma20: float | None
    rsi14: float | None
    volume: int
    volume_ratio: float | None  # today's volume vs. usual volume by this time of day

    @property
    def ma_cross(self) -> str | None:
        """"golden" / "dead" if MA5 crossed MA20 since yesterday's close."""
        if None in (self.ma5, self.ma20, self.prev_ma5, self.prev_ma20):
            return None
        before = self.prev_ma5 - self.prev_ma20
        now = self.ma5 - self.ma20
        if before <= 0 < now:
            return "golden"
        if before >= 0 > now:
            return "dead"
        return None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["ma_cross"] = self.ma_cross
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}


def compute(daily_rows: list[dict], now: datetime) -> Indicators:
    """daily_rows: KisClient.get_daily_prices() output. now: KST."""
    try:
        closes = [float(r["stck_clpr"]) for r in daily_rows]
        volumes = [int(r["acml_vol"]) for r in daily_rows]
        latest = daily_rows[0]
        change_pct = float(latest["prdy_ctrt"])
        date = latest["stck_bsop_date"]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise IndicatorError(f"malformed daily price rows: {exc!r}") from exc
    if any(c <= 0 for c in closes):
        raise IndicatorError("non-positive close in daily price rows")

    is_trading_day = date == now.strftime("%Y%m%d")
    volume_ratio = None
    usual = sma([float(v) for v in volumes[1:]], 20)
    if is_trading_day and usual:
        elapsed = now.hour * 60 + now.minute - SESSION_OPEN_MINUTES
        fraction = min(max(elapsed, 1) / SESSION_LENGTH_MINUTES, 1.0)
        volume_ratio = volumes[0] / (usual * fraction)

    return Indicators(
        date=date,
        is_trading_day=is_trading_day,
        price=closes[0],
        change_pct=change_pct,
        ma5=sma(closes, 5),
        ma20=sma(closes, 20),
        prev_ma5=sma(closes[1:], 5),
        prev_ma20=sma(closes[1:], 20),
        rsi14=rsi(closes, 14),
        volume=volumes[0],
        volume_ratio=volume_ratio,
    )
