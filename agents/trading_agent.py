"""AI trading agent: analyzes filtered market data and returns a
structured BUY / SELL / HOLD decision (models.decision.Decision).

This module must never call broker.order_executor directly. Its output
always passes through risk.risk_manager first.
"""

from __future__ import annotations

# TODO(Phase 5): OpenAI structured-output call + response validation.
