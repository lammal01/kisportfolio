"""broker.order_executor + KisClient.place_cash_order tests.

Covers: risk rejection never reaching KIS, KIS timeout / lost response
(UNKNOWN -> emergency stop), explicit KIS rejection, duplicate
decision_id, and the real-trading refusal. All HTTP is mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from broker.kis_client import KisApiError, KisClient, KisOrderUncertainError
from broker.order_executor import OrderExecutor, RealTradingDisabledError
from config.settings import Settings
from market.data_collector import build_account_state
from models.decision import Decision
from risk.risk_manager import AccountState, RiskLimits, RiskManager

LIMITS = RiskLimits(max_position_pct=0.10, daily_loss_limit_pct=0.03)
ORDER_OUTPUT = {"KRX_FWDG_ORD_ORGNO": "00950", "ODNO": "0000117057", "ORD_TMD": "104512"}


def _paper_settings() -> Settings:
    return Settings(
        kis_paper_app_key="k",
        kis_paper_app_secret="s",
        kis_paper_account_no="12345678-01",
        kis_real_app_key=None,
        kis_real_app_secret=None,
        kis_real_account_no=None,
    )


def _decision(**overrides) -> Decision:
    defaults = dict(action="BUY", symbol="005930", quantity=1, confidence=0.7, reason=("test",))
    defaults.update(overrides)
    return Decision(**defaults)


def _account(**overrides) -> AccountState:
    defaults = dict(
        symbol="005930",
        total_equity=10_000_000.0,
        day_start_equity=10_000_000.0,
        current_price=70_000.0,
        held_quantity=5,
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def _executor(client: MagicMock | None = None) -> tuple[OrderExecutor, MagicMock, RiskManager]:
    client = client or MagicMock(spec=KisClient)
    client.is_real_trading = False
    rm = RiskManager(limits=LIMITS)
    return OrderExecutor(client, rm), client, rm


# --- OrderExecutor ---------------------------------------------------------


def test_approved_order_is_submitted():
    executor, client, _ = _executor()
    client.place_cash_order.return_value = ORDER_OUTPUT

    result = executor.submit(_decision(quantity=2), "d1", _account())

    assert result.status == "SUBMITTED"
    assert result.order_no == "0000117057"
    client.place_cash_order.assert_called_once_with(side="BUY", symbol="005930", quantity=2)


def test_risk_rejection_never_reaches_kis():
    executor, client, _ = _executor()
    # 20 * 70_000 exceeds the 10% position limit.
    result = executor.submit(_decision(quantity=20), "d1", _account())

    assert result.status == "REJECTED_BY_RISK"
    client.place_cash_order.assert_not_called()


def test_hold_never_reaches_kis():
    executor, client, _ = _executor()
    result = executor.submit(_decision(action="HOLD", quantity=0), "d1", _account())
    assert result.status == "REJECTED_BY_RISK"
    client.place_cash_order.assert_not_called()


def test_duplicate_decision_id_is_submitted_once():
    executor, client, _ = _executor()
    client.place_cash_order.return_value = ORDER_OUTPUT

    first = executor.submit(_decision(), "d1", _account())
    second = executor.submit(_decision(), "d1", _account())

    assert first.status == "SUBMITTED"
    assert second.status == "REJECTED_BY_RISK"
    assert "already executed" in second.reason
    assert client.place_cash_order.call_count == 1


def test_uncertain_outcome_locks_decision_and_engages_emergency_stop():
    executor, client, rm = _executor()
    client.place_cash_order.side_effect = KisOrderUncertainError("timeout")

    result = executor.submit(_decision(), "d1", _account())

    assert result.status == "UNKNOWN"
    assert rm.emergency_stop_engaged
    # Every later order -- even a fresh decision_id -- is blocked.
    client.place_cash_order.side_effect = None
    client.place_cash_order.return_value = ORDER_OUTPUT
    later = executor.submit(_decision(), "d2", _account())
    assert later.status == "REJECTED_BY_RISK"
    assert "emergency stop" in later.reason
    assert client.place_cash_order.call_count == 1


def test_broker_rejection_does_not_lock_decision_id():
    executor, client, rm = _executor()
    client.place_cash_order.side_effect = KisApiError("주문가능금액을 초과", msg_cd="APBK0952")

    result = executor.submit(_decision(), "d1", _account())
    assert result.status == "REJECTED_BY_BROKER"
    assert not rm.emergency_stop_engaged

    # Definitely not placed, so the same decision may be tried again.
    client.place_cash_order.side_effect = None
    client.place_cash_order.return_value = ORDER_OUTPUT
    assert executor.submit(_decision(), "d1", _account()).status == "SUBMITTED"


def test_real_trading_client_is_refused():
    client = MagicMock(spec=KisClient)
    client.is_real_trading = True
    with pytest.raises(RealTradingDisabledError):
        OrderExecutor(client, RiskManager(limits=LIMITS))


# --- KisClient.place_cash_order --------------------------------------------


@pytest.fixture
def paper_client():
    client = KisClient(settings=_paper_settings())
    with patch.object(KisClient, "get_access_token", return_value="tok"):
        yield client


def _response(status: int, body: dict | None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    if body is None:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = body
    return resp


@patch("broker.kis_client.requests.post")
def test_place_order_success_uses_paper_buy_tr_id(mock_post, paper_client):
    mock_post.return_value = _response(200, {"rt_cd": "0", "msg1": "주문 전송 완료", "output": ORDER_OUTPUT})

    output = paper_client.place_cash_order("BUY", "005930", 3)

    assert output["ODNO"] == "0000117057"
    kwargs = mock_post.call_args.kwargs
    assert kwargs["headers"]["tr_id"] == "VTTC0012U"
    assert kwargs["json"]["ORD_QTY"] == "3"
    assert kwargs["json"]["ORD_DVSN"] == "01"
    assert kwargs["json"]["CANO"] == "12345678"


@patch("broker.kis_client.requests.post")
def test_place_order_sell_uses_paper_sell_tr_id(mock_post, paper_client):
    mock_post.return_value = _response(200, {"rt_cd": "0", "output": ORDER_OUTPUT})
    paper_client.place_cash_order("SELL", "005930", 1)
    assert mock_post.call_args.kwargs["headers"]["tr_id"] == "VTTC0011U"


@patch("broker.kis_client.requests.post")
def test_place_order_timeout_is_uncertain(mock_post, paper_client):
    mock_post.side_effect = requests.Timeout("read timed out")
    with pytest.raises(KisOrderUncertainError):
        paper_client.place_cash_order("BUY", "005930", 1)
    assert mock_post.call_count == 1  # never retried


@patch("broker.kis_client.requests.post")
def test_place_order_unparseable_response_is_uncertain(mock_post, paper_client):
    mock_post.return_value = _response(502, None)
    with pytest.raises(KisOrderUncertainError):
        paper_client.place_cash_order("BUY", "005930", 1)


@patch("broker.kis_client.requests.post")
def test_place_order_http_200_without_order_no_is_uncertain(mock_post, paper_client):
    mock_post.return_value = _response(200, {"rt_cd": "0", "output": {}})
    with pytest.raises(KisOrderUncertainError):
        paper_client.place_cash_order("BUY", "005930", 1)


@patch("broker.kis_client.requests.post")
def test_place_order_explicit_rejection_is_definite(mock_post, paper_client):
    mock_post.return_value = _response(
        200, {"rt_cd": "1", "msg_cd": "APBK0952", "msg1": "주문가능금액을 초과 했습니다"}
    )
    with pytest.raises(KisApiError) as excinfo:
        paper_client.place_cash_order("BUY", "005930", 1)
    assert not isinstance(excinfo.value, KisOrderUncertainError)
    assert excinfo.value.msg_cd == "APBK0952"


def test_place_order_rejects_invalid_side_and_quantity(paper_client):
    with pytest.raises(ValueError):
        paper_client.place_cash_order("HOLD", "005930", 1)
    with pytest.raises(ValueError):
        paper_client.place_cash_order("BUY", "005930", 0)


# --- market.data_collector.build_account_state -----------------------------


def test_build_account_state_from_kis_responses():
    client = MagicMock(spec=KisClient)
    client.get_balance.return_value = {
        "holdings": [{"pdno": "005930", "hldg_qty": "4"}, {"pdno": "000660", "hldg_qty": "2"}],
        "summary": {"tot_evlu_amt": "10000000"},
    }
    client.get_current_price.return_value = {"stck_prpr": "70000"}

    account = build_account_state("005930", client=client)

    assert account == AccountState(
        symbol="005930",
        total_equity=10_000_000.0,
        day_start_equity=10_000_000.0,
        current_price=70_000.0,
        held_quantity=4,
    )


def test_build_account_state_missing_field_raises():
    client = MagicMock(spec=KisClient)
    client.get_balance.return_value = {"holdings": [], "summary": {}}
    client.get_current_price.return_value = {"stck_prpr": "70000"}
    with pytest.raises(ValueError):
        build_account_state("005930", client=client)
