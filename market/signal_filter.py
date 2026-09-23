"""Decides whether a given tick/update warrants an LLM call.

This is the cost-control layer: most ticks should be filtered out here
using cheap Python-computed indicators, and only genuinely interesting
events should reach agents.trading_agent.
"""

from __future__ import annotations

# TODO(Phase 5): implement filtering rules and AI-call/token logging.
