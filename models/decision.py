"""Structured AI trading decision output and validation.

The AI (agents.trading_agent) must never return free-form natural
language for the actual decision -- only this structured shape, and
even then it is parsed and validated here before anything downstream
(risk.risk_manager) is allowed to trust it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ACTIONS: tuple[str, ...] = ("BUY", "SELL", "HOLD")
Action = Literal["BUY", "SELL", "HOLD"]


class DecisionParseError(ValueError):
    """Raised when raw AI output can't be parsed into a valid Decision."""


@dataclass(frozen=True)
class Decision:
    action: Action
    symbol: str
    quantity: int
    confidence: float
    reason: tuple[str, ...]
    risk_score: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    invalidation_condition: str | None = None

    @staticmethod
    def from_dict(data: dict) -> "Decision":
        """Parses+validates a raw dict (e.g. json.loads'd AI output).

        Raises DecisionParseError with a specific reason on anything
        malformed -- callers should treat that as "no valid decision",
        never fall back to guessing a default action.
        """
        if not isinstance(data, dict):
            raise DecisionParseError(f"expected a JSON object, got {type(data).__name__}")

        missing = [f for f in ("action", "symbol", "quantity", "confidence", "reason") if f not in data]
        if missing:
            raise DecisionParseError(f"missing required field(s): {', '.join(missing)}")

        action = data["action"]
        if action not in ACTIONS:
            raise DecisionParseError(f"action must be one of {ACTIONS}, got {action!r}")

        symbol = data["symbol"]
        if not isinstance(symbol, str) or not symbol.strip():
            raise DecisionParseError(f"symbol must be a non-empty string, got {symbol!r}")

        quantity = data["quantity"]
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise DecisionParseError(f"quantity must be an integer, got {quantity!r}")
        if quantity < 0:
            raise DecisionParseError(f"quantity must not be negative, got {quantity!r}")
        if action in ("BUY", "SELL") and quantity == 0:
            raise DecisionParseError(f"quantity must be > 0 for action={action!r}")

        confidence = data["confidence"]
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise DecisionParseError(f"confidence must be a number, got {confidence!r}")
        if not (0.0 <= float(confidence) <= 1.0):
            raise DecisionParseError(f"confidence must be within [0, 1], got {confidence!r}")

        reason = data["reason"]
        if not isinstance(reason, list) or not reason or not all(isinstance(r, str) for r in reason):
            raise DecisionParseError(f"reason must be a non-empty list of strings, got {reason!r}")

        def _optional_float(key: str) -> float | None:
            value = data.get(key)
            if value is None:
                return None
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise DecisionParseError(f"{key} must be a number if present, got {value!r}")
            return float(value)

        invalidation_condition = data.get("invalidation_condition")
        if invalidation_condition is not None and not isinstance(invalidation_condition, str):
            raise DecisionParseError(
                f"invalidation_condition must be a string if present, got {invalidation_condition!r}"
            )

        return Decision(
            action=action,
            symbol=symbol,
            quantity=quantity,
            confidence=float(confidence),
            reason=tuple(reason),
            risk_score=_optional_float("risk_score"),
            stop_loss=_optional_float("stop_loss"),
            take_profit=_optional_float("take_profit"),
            invalidation_condition=invalidation_condition,
        )
