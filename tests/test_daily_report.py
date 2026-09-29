"""daily_report tests: TradeMemory.day_journal plus the workbook written
from a throwaway SQLite file and canned KIS data (no network)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook

import daily_report
from daily_report import KisData, export
from memory.trade_memory import TradeMemory
from models.decision import Decision
from models.order import OrderFill, OrderResult

TODAY = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")

BALANCE = {
    "holdings": [
        {"pdno": "000270", "prdt_name": "기아", "hldg_qty": "8", "pchs_avg_pric": "115100", "prpr": "116000",
         "pchs_amt": "920800", "evlu_amt": "928000", "evlu_pfls_amt": "7200", "evlu_pfls_rt": "0.78"},
    ],
    "summary": {"tot_evlu_amt": "10012813", "bfdy_tot_asst_evlu_amt": "9994953", "asst_icdc_amt": "17860",
                "prvs_rcdl_excc_amt": "9084813", "dnca_tot_amt": "10000000", "scts_evlu_amt": "928000",
                "evlu_pfls_smtl_amt": "7200", "thdt_tlex_amt": "140"},
}

BROKER_ORDERS = [
    {"odno": "0000013511", "orgn_odno": "", "ord_tmd": "100722", "pdno": "000270", "prdt_name": "기아",
     "sll_buy_dvsn_cd": "02", "ord_qty": "8", "tot_ccld_qty": "8", "rmn_qty": "0", "rjct_qty": "0",
     "cncl_yn": "N", "avg_prvs": "115100", "tot_ccld_amt": "920800"},
    {"odno": "0000099999", "orgn_odno": "", "ord_tmd": "130000", "pdno": "005930", "prdt_name": "삼성전자",
     "sll_buy_dvsn_cd": "01", "ord_qty": "1", "tot_ccld_qty": "0", "rmn_qty": "1", "rjct_qty": "0",
     "cncl_yn": "N", "avg_prvs": "0", "tot_ccld_amt": "0"},
]


@pytest.fixture
def memory(tmp_path):
    m = TradeMemory(tmp_path / "trades.sqlite3")
    snapshot = {"held_quantity": 0, "indicators": {"price": 115000.0, "change_pct": -2.21, "rsi14": 27.64},
                "signal_triggers": ["오늘 첫 점검", "RSI 과매도 (28)"]}
    m.record_decision("d-hold", Decision(action="HOLD", symbol="005930", quantity=0, confidence=0.6,
                                         reason=("관망 사유",)), {"indicators": {}, "held_quantity": 0})
    m.record_order(OrderResult(decision_id="d-hold", side="HOLD", symbol="005930", quantity=0,
                               status="REJECTED_BY_RISK", reason="HOLD(관망)는 주문 대상이 아님"))
    m.record_decision("d-buy", Decision(action="BUY", symbol="000270", quantity=8, confidence=0.6,
                                        reason=("RSI 과매도", "PBR 0.73")), snapshot)
    m.record_order(OrderResult(decision_id="d-buy", side="BUY", symbol="000270", quantity=8, status="SUBMITTED",
                               reason="accepted", order_no="0000013511", requested_at="100720"))
    m.record_fill("d-buy", OrderFill(order_no="0000013511", side="BUY", symbol="000270", ordered_qty=8,
                                     filled_qty=8, remaining_qty=0, rejected_qty=0, cancelled=False,
                                     avg_price=115100.0, order_time="100722"))
    m.record_ai_call("005930", True)
    m.record_ai_call("000270", True)
    m.day_start_equity(9_982_553.0, TODAY)
    yield m
    m.close()


def _column(ws, header_row: int, header: str) -> list:
    headers = [c.value for c in ws[header_row]]
    col = headers.index(header) + 1
    return [ws.cell(row=r, column=col).value for r in range(header_row + 1, ws.max_row + 1)]


def _labels(ws) -> dict:
    return {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value for r in range(1, ws.max_row + 1)}


def test_day_journal_joins_order_fill_and_snapshot(memory):
    journal = memory.day_journal(TODAY)
    assert [e["action"] for e in journal] == ["HOLD", "BUY"]
    buy = journal[1]
    assert buy["order_no"] == "0000013511" and buy["filled_qty"] == 8 and buy["avg_price"] == 115100.0
    assert buy["reason"] == ["RSI 과매도", "PBR 0.73"]
    assert buy["indicators"]["rsi14"] == 27.64
    assert buy["signal_triggers"] == ["오늘 첫 점검", "RSI 과매도 (28)"]
    assert memory.day_journal("19990101") == []


def test_start_equity_on_is_read_only(memory):
    assert memory.start_equity_on(TODAY) == 9_982_553.0
    assert memory.start_equity_on("19990101") is None
    assert memory.last_day_start_equity() == (TODAY, 9_982_553.0)  # nothing inserted for 19990101


def test_export_writes_all_sheets(memory, tmp_path):
    kis = KisData(environment="paper", balance=BALANCE, orders=BROKER_ORDERS)
    path = export(TODAY, memory=memory, kis=kis, out_dir=tmp_path / "reports")
    assert path.name == f"매매일지_{TODAY}.xlsx"
    wb = load_workbook(path)
    assert wb.sheetnames == ["요약", "주문·체결", "보유현황", "AI판단내역", "증권사대조"]

    summary = wb["요약"]
    assert "모의투자" in summary["A1"].value
    values = _labels(summary)
    assert values["전일 대비 손익 (KIS)"] == 17860
    assert values["루프 시작 대비 손익"] == pytest.approx(10_012_813 - 9_982_553)
    assert values["예수금 (D+2 정산 기준)"] == 9_084_813
    assert values["매수 체결금액"] == 8 * 115100
    assert values["AI 판단"] == "매수 1 / 매도 0 / 관망 1"

    orders = wb["주문·체결"]
    assert _column(orders, 4, "종목명") == ["기아"]  # HOLD is not an order
    assert _column(orders, 4, "체결금액") == [920800]

    decisions = wb["AI판단내역"]
    assert _column(decisions, 4, "판단") == ["관망", "매수"]
    assert _column(decisions, 4, "호출 신호")[1] == "오늘 첫 점검\nRSI 과매도 (28)"

    holdings = wb["보유현황"]
    assert _column(holdings, 4, "종목코드")[:2] == ["000270", "합계"]
    assert _column(holdings, 4, "평가손익")[1] == "=SUM(H5:H5)"
    assert len(holdings._charts) == 1

    broker = wb["증권사대조"]
    assert _column(broker, 4, "로컬 기록") == ["일치", "로컬 기록 없음"]
    assert _column(broker, 4, "구분") == ["매수", "매도"]


def test_export_without_kis_still_writes_local_data(memory, tmp_path):
    path = export(TODAY, memory=memory, kis=KisData(errors=["KIS 조회 생략 (--no-kis)"]), out_dir=tmp_path)
    wb = load_workbook(path)
    summary_text = [c.value for row in wb["요약"].iter_rows() for c in row if isinstance(c.value, str)]
    assert "· KIS 조회 생략 (--no-kis)" in summary_text
    assert _column(wb["주문·체결"], 4, "주문번호") == ["0000013511"]
    assert "잔고 조회" in wb["보유현황"]["A4"].value


def test_past_date_skips_today_only_figures(memory, tmp_path):
    kis = KisData(environment="paper", balance=BALANCE, orders=[])
    path = export("19990101", memory=memory, kis=kis, out_dir=tmp_path)
    values = _labels(load_workbook(path)["요약"])
    assert "전일 대비 손익 (KIS)" not in values
    assert values["루프 시작 평가금액 (그날 첫 점검 시)"] == "-"


def test_fetch_kis_records_failures_instead_of_raising(monkeypatch):
    class Broken:
        environment = "paper"

        def get_balance(self):
            raise RuntimeError("boom")

        def get_daily_orders(self, date):
            return []

    monkeypatch.setattr(daily_report, "KisClient", Broken)
    data = daily_report.fetch_kis(TODAY)
    assert data.balance is None and data.orders == []
    assert data.errors == ["잔고 조회 실패: boom"]
