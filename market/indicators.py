"""Deterministic technical indicators computed in Python (not via LLM).

e.g. moving averages, RSI, MACD, volume change, volatility, price change
rate, market state. These feed the signal filter so the LLM is only
called when a Python-computed signal actually warrants it.
"""

from __future__ import annotations

# TODO: implement indicator functions as they're needed by signal_filter.
