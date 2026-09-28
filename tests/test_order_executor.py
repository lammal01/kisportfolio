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
from broker.order_executor import OrderExecutor, RealTradingDisabledError, Reconciliation
from config.settings import Settings
from market.data_collector import build_account_state
from models.decision import Decision
from models.order import OrderFill, OrderFillParseError, OrderResult
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
        open_order_count=0,
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
def test_place_order_connect_timeout_is_definitely_not_placed(mock_post, paper_client):
    mock_post.side_effect = requests.ConnectTimeout("connect timed out")
    with pytest.raises(KisApiError) as excinfo:
        paper_client.place_cash_order("BUY", "005930", 1)
    assert not isinstance(excinfo.value, KisOrderUncertainError)


@patch("broker.kis_client.requests.post")
def test_place_order_connection_reset_is_uncertain(mock_post, paper_client):
    mock_post.side_effect = requests.ConnectionError("connection reset by peer")
    with pytest.raises(KisOrderUncertainError):
        paper_client.place_cash_order("BUY", "005930", 1)


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
        "holdings": [
            {"pdno": "005930", "hldg_qty": "4", "pchs_avg_pric": "68000.5"},
            {"pdno": "000660", "hldg_qty": "2", "pchs_avg_pric": "150000"},
        ],
        "summary": {"tot_evlu_amt": "10000000"},
    }
    client.get_current_price.return_value = {"stck_prpr": "70000"}
    client.get_daily_orders.return_value = [
        _order_row(odno="1", rmn_qty="1", tot_ccld_qty="0"),  # open, counts
        _order_row(odno="2"),  # filled
        _order_row(odno="3", pdno="000660", rmn_qty="1", tot_ccld_qty="0"),  # other symbol
        _order_row(odno="4", orgn_odno="0000000001", rmn_qty="1", tot_ccld_qty="0"),  # cancel row
    ]

    account = build_account_state("005930", client=client)

    assert account == AccountState(
        symbol="005930",
        total_equity=10_000_000.0,
        day_start_equity=10_000_000.0,
        current_price=70_000.0,
        held_quantity=4,
        open_order_count=1,
        avg_cost=68_000.5,
    )


def test_build_account_state_missing_field_raises():
    client = MagicMock(spec=KisClient)
    client.get_balance.return_value = {"holdings": [], "summary": {}}
    client.get_current_price.return_value = {"stck_prpr": "70000"}
    with pytest.raises(ValueError):
        build_account_state("005930", client=client)


# --- Phase 8: fills, reconciliation ----------------------------------------


def _order_row(**overrides) -> dict:
    row = {
        "odno": "0000020558",
        "orgn_odno": "0000000000",
        "sll_buy_dvsn_cd": "02",
        "pdno": "005930",
        "ord_qty": "1",
        "tot_ccld_qty": "1",
        "rmn_qty": "0",
        "rjct_qty": "0",
        "cncl_yn": "N",
        "avg_prvs": "274750",
        "ord_tmd": "110238",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "overrides, status, is_open",
    [
        ({}, "FILLED", False),
        ({"ord_qty": "3", "tot_ccld_qty": "1", "rmn_qty": "2"}, "PARTIALLY_FILLED", True),
        ({"tot_ccld_qty": "0", "rmn_qty": "1"}, "PENDING", True),
        ({"tot_ccld_qty": "0", "rmn_qty": "0", "cncl_yn": "Y"}, "CANCELLED", False),
        ({"tot_ccld_qty": "0", "rmn_qty": "0", "rjct_qty": "1"}, "REJECTED", False),
    ],
)
def test_order_fill_status(overrides, status, is_open):
    fill = OrderFill.from_kis(_order_row(**overrides))
    assert fill.status == status
    assert fill.is_open is is_open


def test_order_fill_parses_side_and_price():
    fill = OrderFill.from_kis(_order_row(sll_buy_dvsn_cd="01", avg_prvs="274500"))
    assert fill.side == "SELL"
    assert fill.avg_price == 274_500.0


def test_order_fill_malformed_row_raises():
    with pytest.raises(OrderFillParseError):
        OrderFill.from_kis(_order_row(tot_ccld_qty="abc"))
    with pytest.raises(OrderFillParseError):
        OrderFill.from_kis(_order_row(sll_buy_dvsn_cd="99"))


def test_confirm_fill_polls_until_filled():
    executor, client, _ = _executor()
    client.place_cash_order.return_value = ORDER_OUTPUT
    result = executor.submit(_decision(), "d1", _account())
    client.get_daily_orders.side_effect = [
        [],  # not listed yet
        [_order_row(odno="0000117057", tot_ccld_qty="0", rmn_qty="1")],
        [_order_row(odno="0000117057")],
    ]

    fill = executor.confirm_fill(result, attempts=5, interval_seconds=0)

    assert fill.status == "FILLED"
    assert client.get_daily_orders.call_count == 3


def test_confirm_fill_returns_last_open_state_when_attempts_run_out():
    executor, client, _ = _executor()
    client.place_cash_order.return_value = ORDER_OUTPUT
    result = executor.submit(_decision(), "d1", _account())
    client.get_daily_orders.return_value = [_order_row(odno="0000117057", tot_ccld_qty="0", rmn_qty="1")]

    fill = executor.confirm_fill(result, attempts=2, interval_seconds=0)

    assert fill.status == "PENDING"


def test_confirm_fill_rejects_non_submitted_result():
    executor, _, _ = _executor()
    result = executor.submit(_decision(action="HOLD", quantity=0), "d1", _account())
    with pytest.raises(ValueError):
        executor.confirm_fill(result)


def _unknown_result(executor, client) -> OrderResult:
    client.place_cash_order.side_effect = KisOrderUncertainError("timeout")
    result = executor.submit(_decision(), "d-unknown", _account())
    assert result.status == "UNKNOWN"
    return result


def _at(result: OrderResult, delta_seconds: int) -> str:
    h, m, s = int(result.requested_at[:2]), int(result.requested_at[2:4]), int(result.requested_at[4:])
    total = h * 3600 + m * 60 + s + delta_seconds
    return f"{total // 3600:02d}{total % 3600 // 60:02d}{total % 60:02d}"


def test_reconcile_unknown_finds_placed_order():
    executor, client, rm = _executor()
    result = _unknown_result(executor, client)
    client.get_daily_orders.return_value = [_order_row(odno="9", ord_tmd=_at(result, 2))]

    rec = executor.reconcile_unknown(result)

    assert rec.outcome == "PLACED"
    assert rec.fill.order_no == "9"
    assert rm.emergency_stop_engaged  # never lifted automatically


def test_reconcile_unknown_ignores_known_and_out_of_window_orders():
    executor, client, rm = _executor()
    client.place_cash_order.return_value = ORDER_OUTPUT
    executor.submit(_decision(), "d-known", _account())  # known order 0000117057
    client.place_cash_order.return_value = None
    result = _unknown_result(executor, client)
    client.get_daily_orders.return_value = [
        _order_row(odno="0000117057", ord_tmd=_at(result, 0)),  # already known
        _order_row(odno="8", ord_tmd=_at(result, -600)),  # too early
        _order_row(odno="7", ord_tmd=_at(result, 1), ord_qty="2"),  # different qty
        _order_row(odno="6", ord_tmd=_at(result, 1), sll_buy_dvsn_cd="01"),  # different side
    ]

    rec = executor.reconcile_unknown(result)

    assert rec == Reconciliation("NOT_FOUND", ())
    assert rm.emergency_stop_engaged


def test_reconcile_unknown_finds_order_registered_late():
    # Seen live: KIS registered the order 31s after a timed-out request.
    executor, client, _ = _executor()
    result = _unknown_result(executor, client)
    client.get_daily_orders.return_value = [_order_row(odno="9", ord_tmd=_at(result, 180))]
    assert executor.reconcile_unknown(result).outcome == "PLACED"


def test_reconcile_unknown_ambiguous_with_several_candidates():
    executor, client, _ = _executor()
    result = _unknown_result(executor, client)
    client.get_daily_orders.return_value = [
        _order_row(odno="9", ord_tmd=_at(result, 1)),
        _order_row(odno="10", ord_tmd=_at(result, 3)),
    ]
    assert executor.reconcile_unknown(result).outcome == "AMBIGUOUS"


def test_reconcile_rejects_non_unknown_result():
    executor, client, _ = _executor()
    client.place_cash_order.return_value = ORDER_OUTPUT
    result = executor.submit(_decision(), "d1", _account())
    with pytest.raises(ValueError):
        executor.reconcile_unknown(result)


# --- KisClient.get_daily_orders pagination / rate limit ---------------------


def _get_response(body: dict, tr_cont: str = "E") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b"{}"
    resp.json.return_value = body
    resp.headers = {"tr_cont": tr_cont}
    return resp


@patch("broker.kis_client.requests.get")
def test_get_daily_orders_follows_pagination(mock_get, paper_client):
    mock_get.side_effect = [
        _get_response(
            {"rt_cd": "0", "output1": [_order_row(odno="1")], "ctx_area_fk100": "FK", "ctx_area_nk100": "NK"},
            tr_cont="M",
        ),
        _get_response({"rt_cd": "0", "output1": [_order_row(odno="2")]}, tr_cont="E"),
    ]

    rows = paper_client.get_daily_orders("20260928")

    assert [r["odno"] for r in rows] == ["1", "2"]
    second = mock_get.call_args_list[1].kwargs
    assert second["headers"]["tr_cont"] == "N"
    assert second["headers"]["tr_id"] == "VTTC0081R"
    assert second["params"]["CTX_AREA_NK100"] == "NK"


@patch("broker.kis_client.requests.get")
def test_get_daily_orders_refuses_partial_list(mock_get, paper_client, monkeypatch):
    monkeypatch.setattr("broker.kis_client._MAX_ORDER_PAGES", 2)
    mock_get.return_value = _get_response({"rt_cd": "0", "output1": []}, tr_cont="M")
    with pytest.raises(KisApiError):
        paper_client.get_daily_orders("20260928")


@patch("broker.kis_client.requests.get")
def test_read_request_retries_on_rate_limit(mock_get, paper_client):
    limited = _get_response({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."})
    mock_get.side_effect = [limited, _get_response({"rt_cd": "0", "output": {"stck_prpr": "1"}})]

    assert paper_client.get_current_price("005930") == {"stck_prpr": "1"}
    assert mock_get.call_count == 2


@patch("broker.kis_client.requests.post")
def test_order_is_never_retried_on_rate_limit(mock_post, paper_client):
    mock_post.return_value = _response(
        200, {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}
    )
    with pytest.raises(KisApiError):
        paper_client.place_cash_order("BUY", "005930", 1)
    assert mock_post.call_count == 1


@patch("broker.kis_client.requests.get")
def test_read_request_retries_network_error_then_raises_kis_error(mock_get, paper_client):
    mock_get.side_effect = requests.ReadTimeout("read timed out")
    with pytest.raises(KisApiError):
        paper_client.get_current_price("005930")
    assert mock_get.call_count == 3  # 1 + _RATE_LIMIT_RETRIES


@patch("broker.kis_client.requests.get")
def test_read_request_recovers_after_network_error(mock_get, paper_client):
    mock_get.side_effect = [
        requests.ReadTimeout("read timed out"),
        _get_response({"rt_cd": "0", "output": {"stck_prpr": "1"}}),
    ]
    assert paper_client.get_current_price("005930") == {"stck_prpr": "1"}
