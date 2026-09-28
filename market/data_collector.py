"""Market data collection.

Kept separate from AI decision logic per the architecture in the project
spec: Market Data -> Data Collector -> Signal/Event Filter -> AI Agent.

Phase 2: current price. Phase 3: balance/holdings/buying power. Phase 7:
AccountState for the risk manager. Later phases add WebSocket for
real-time feeds.
"""

from __future__ import annotations

from broker.kis_client import KisClient
from risk.risk_manager import AccountState


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


def build_account_state(
    symbol: str, day_start_equity: float | None = None, client: KisClient | None = None
) -> AccountState:
    """Fills a risk.risk_manager.AccountState for `symbol` from live KIS
    balance + current price.

    day_start_equity: 당일 시작 총평가금액. None이면 지금 총평가금액을
    그대로 씀 -- 즉 일일 손실 한도가 이 시점부터 계산됨. 영속 저장은
    Phase 9 (Trade Memory) 몫.

    Raises ValueError if KIS returns a missing/non-numeric field rather
    than defaulting it to 0 (a 0 price/equity would be silently wrong).
    """
    client = client or KisClient()
    balance = client.get_balance()
    price = client.get_current_price(symbol)

    def number(source: dict, key: str) -> float:
        try:
            return float(source[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"KIS response missing/invalid {key!r}: {source.get(key)!r}") from exc

    total_equity = number(balance["summary"], "tot_evlu_amt")
    held_quantity = sum(
        int(number(h, "hldg_qty")) for h in balance["holdings"] if h.get("pdno") == symbol
    )
    return AccountState(
        symbol=symbol,
        total_equity=total_equity,
        day_start_equity=total_equity if day_start_equity is None else day_start_equity,
        current_price=number(price, "stck_prpr"),
        held_quantity=held_quantity,
    )
