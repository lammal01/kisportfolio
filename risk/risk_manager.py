"""Deterministic, AI-independent risk validation.

Every AI decision must pass through here before it can reach
broker.order_executor. This module must not be influenced by AI output
in any way that could weaken its own limits (position size caps, daily
loss limits, cooldowns, duplicate-order checks, emergency stop, etc).
Rejections must be logged with a reason.
"""

from __future__ import annotations

# TODO(Phase 6): implement limit checks and approve/reject decision path.
