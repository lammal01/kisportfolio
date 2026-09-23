"""Market data collection.

Kept separate from AI decision logic per the architecture in the project
spec: Market Data -> Data Collector -> Signal/Event Filter -> AI Agent.

Phase 2 scope: current price only, via broker.kis_client (REST). Phase 3
adds balance/holdings/buying power; later phases add WebSocket for
real-time feeds.
"""

from __future__ import annotations

from broker.kis_client import KisClient


def get_current_price(symbol: str, client: KisClient | None = None) -> dict:
    """Returns the raw KIS inquire-price 'output' dict for a KRX symbol.

    e.g. get_current_price("005930") for 삼성전자.
    """
    client = client or KisClient()
    return client.get_current_price(symbol)
