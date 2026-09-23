"""Stores AI decisions and their eventual outcomes (decision_id,
timestamp, symbol, market snapshot, AI action/reason/confidence, risk
manager result, order/fill info, entry/exit price, realized PnL, return).

Provides retrieval of relevant past trades for context -- never dumps
the entire history into a prompt.
"""

from __future__ import annotations

# TODO(Phase 9): persistence + targeted retrieval/summarization.
