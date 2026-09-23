"""Market data collection (price, OHLCV, volume, order book, index,
account balance, holdings, buying power, open orders) from the KIS API.

Kept separate from AI decision logic per the architecture in the project
spec: Market Data -> Data Collector -> Signal/Event Filter -> AI Agent.
"""

from __future__ import annotations

# TODO(Phase 2/3): REST polling first; WebSocket for real-time feeds later.
