"""Market data collection.

Kept separate from AI decision logic per the architecture in the project
spec: Market Data -> Data Collector -> Signal/Event Filter -> AI Agent.

Phase 2: current price. Phase 3: balance/holdings/buying power. Later
phases add WebSocket for real-time feeds.
"""

from __future__ import annotations

from broker.kis_client import KisClient


def get_current_price(symbol: str, client: KisClient | None = None) -> dict:
    """Returns the raw KIS inquire-price 'output' dict for a KRX symbol.

    e.g. get_current_price("005930") for 삼성전자.
    """
    client = client or KisClient()
    return client.get_current_price(symbol)


def get_balance(client: KisClient | None = None) -> dict:
    """Returns {"holdings": [...], "summary": {...}} -- see
    KisClient.get_balance for field details."""
    client = client or KisClient()
    return client.get_balance()


def get_buying_power(symbol: str, client: KisClient | None = None) -> dict:
    """Returns the raw KIS inquire-psbl-order 'output' dict (매수가능금액/수량)
    for a KRX symbol, queried at 시장가 (market order) so the margin rate is
    reflected in the quantity."""
    client = client or KisClient()
    return client.get_buying_power(symbol)
