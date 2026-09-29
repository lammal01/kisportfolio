"""Daily trading journal (매매일지) as an Excel workbook.

    python daily_report.py              # today (KST)
    python daily_report.py 20260928     # an earlier day
    python daily_report.py --no-kis     # local DB only, no KIS lookups

Read-only: the day's AI decisions, orders and fills come from
memory.trade_memory (SQLite); KIS's own order list (일별주문체결조회) is
added as a cross-check, and the balance (잔고조회) for holdings/equity.
Nothing here places or changes an order. If a KIS lookup fails the
workbook is still written from the local DB, with a note on 요약.
The balance is always the account as of *now*, even for a past date.

Output: reports/매매일지_<YYYYMMDD>.xlsx
"""

from __future__ import annotations

import argparse
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from broker.kis_client import KisClient
from config.settings import get_settings
from memory.trade_memory import TradeMemory
from models.order import OrderFill, OrderFillParseError, is_original_order
from telegram.labels import ACTION, FILL_STATUS, ORDER_STATUS, label

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_REPORTS_DIR = Path(__file__).resolve().parent / "reports"
_DATE_RE = re.compile(r"^\d{8}$")

# Fallback names for WATCHLIST symbols; the name on a KIS balance/order
# row (prdt_name) wins when there is one.
_NAMES = {
    "005930": "삼성전자", "000660": "SK하이닉스", "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스", "005380": "현대차", "000270": "기아", "068270": "셀트리온",
    "006400": "삼성SDI", "005490": "POSCO홀딩스", "035420": "NAVER", "035720": "카카오",
    "012330": "현대모비스", "051910": "LG화학", "247540": "에코프로비엠", "032830": "삼성생명",
}

_NAVY = "1F3864"
_HEADER_FILL = PatternFill("solid", fgColor=_NAVY)
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TOTAL_FILL = PatternFill("solid", fgColor="F2F2F2")
_TITLE_FONT = Font(bold=True, size=15)
_SECTION_FONT = Font(bold=True, size=12, color=_NAVY)
_NOTE_FONT = Font(color="7F7F7F", size=9)
_BOLD = Font(bold=True)
# 국내 관례: 매수/상승 빨강, 매도/하락 파랑
_ACTION_FONTS = {
    "BUY": Font(bold=True, color="C00000"),
    "SELL": Font(bold=True, color="0070C0"),
    "HOLD": Font(color="7F7F7F"),
}
_ROW_BORDER = Border(bottom=Side(style="thin", color="D9D9D9"))
_WRAP = Alignment(wrap_text=True, vertical="top")
_TOP = Alignment(vertical="top")

WON = "#,##0"
SIGNED_WON = "[Red]+#,##0;[Blue]-#,##0;0"
PCT = "0.00%"
SIGNED_PCT = "[Red]+0.00%;[Blue]-0.00%;0.00%"
CONF = "0%"


@dataclass
class KisData:
    environment: str | None = None
    balance: dict | None = None
    orders: list[dict] | None = None
    errors: list[str] = field(default_factory=list)


def fetch_kis(trade_date: str) -> KisData:
    """Read-only KIS lookups; each failure is recorded, never raised."""
    data = KisData()
    try:
        client = KisClient()
        data.environment = client.environment
    except Exception as exc:
        data.errors.append(f"KIS 설정 오류: {exc}")
        return data
    try:
        data.balance = client.get_balance()
    except Exception as exc:
        data.errors.append(f"잔고 조회 실패: {exc}")
    try:
        data.orders = client.get_daily_orders(trade_date)
    except Exception as exc:
        data.errors.append(f"주문체결 조회 실패: {exc}")
    return data


def _num(value) -> float | None:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else None
    except ValueError:
        return None


def _hms(hhmmss: str | None) -> str:
    return f"{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}" if hhmmss and len(hhmmss) >= 6 else "-"


def _names(kis: KisData) -> dict[str, str]:
    names = dict(_NAMES)
    rows = (kis.balance or {}).get("holdings", []) + (kis.orders or [])
    for row in rows:
        if row.get("pdno") and row.get("prdt_name"):
            names[row["pdno"]] = row["prdt_name"].strip()
    return names


# --- sheet helpers -----------------------------------------------------------


def _header(ws: Worksheet, row: int, headers: list[str]) -> None:
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col, value=text)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _rows(ws: Worksheet, top: int, rows: list[list], formats: dict[int, str], wrap: set[int] = frozenset()) -> int:
    """Writes `rows` from row `top`; formats/wrap are keyed by 1-based
    column. Returns the next free row."""
    for r, values in enumerate(rows, start=top):
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.border = _ROW_BORDER
            cell.alignment = _WRAP if c in wrap else _TOP
            if c in formats:
                cell.number_format = formats[c]
    return top + len(rows)


def _widths(ws: Worksheet, widths: list[float]) -> None:
    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width


def _title(ws: Worksheet, text: str, sub: str | None = None) -> int:
    ws.cell(row=1, column=1, value=text).font = _TITLE_FONT
    if sub:
        ws.cell(row=2, column=1, value=sub).font = _NOTE_FONT
    return 4


def _no_data(ws: Worksheet, row: int, text: str) -> None:
    ws.cell(row=row, column=1, value=text).font = _NOTE_FONT


# --- sheets ------------------------------------------------------------------


def _summary_sheet(
    ws: Worksheet,
    trade_date: str,
    is_today: bool,
    journal: list[dict],
    summary: dict,
    start_equity: float | None,
    ai_limit: int,
    kis: KisData,
    names: dict[str, str],
    generated_at: datetime,
) -> None:
    env = {"paper": "모의투자", "real": "실전투자"}.get(kis.environment or "", "환경 미확인")
    row = _title(
        ws,
        f"자동매매 매매일지 — {trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} ({env})",
        f"생성 {generated_at:%Y-%m-%d %H:%M} KST · 로컬 매매기록(SQLite) + KIS 조회",
    )

    bal = (kis.balance or {}).get("summary") or {}
    equity = _num(bal.get("tot_evlu_amt"))
    ws.cell(row=row, column=1, value="계좌").font = _SECTION_FONT
    row += 1
    account = [("현재 총평가금액 (생성 시점)", equity, WON)]
    if is_today:
        # KIS's own 전일 대비 figures only describe today, so past dates skip them.
        prev = _num(bal.get("bfdy_tot_asst_evlu_amt"))
        change = _num(bal.get("asst_icdc_amt"))
        account += [
            ("전일 총평가금액 (KIS)", prev, WON),
            ("전일 대비 손익 (KIS)", change, SIGNED_WON),
            ("전일 대비 수익률", change / prev if change is not None and prev else None, SIGNED_PCT),
            ("루프 시작 평가금액 (당일 첫 점검 시)", start_equity, WON),
            ("루프 시작 대비 손익", equity - start_equity if start_equity and equity is not None else None,
             SIGNED_WON),
            ("오늘 수수료·세금 (KIS)", _num(bal.get("thdt_tlex_amt")), WON),
        ]
    else:
        account.append(("루프 시작 평가금액 (그날 첫 점검 시)", start_equity, WON))
    account += [
        # dnca_tot_amt is pre-settlement and ignores today's buys; D+2 is what's actually left.
        ("예수금 (D+2 정산 기준)", _num(bal.get("prvs_rcdl_excc_amt")), WON),
        ("주식 평가금액", _num(bal.get("scts_evlu_amt")), WON),
        ("보유 평가손익 합계", _num(bal.get("evlu_pfls_smtl_amt")), SIGNED_WON),
        ("보유종목 수", len(kis.balance["holdings"]) if kis.balance else None, "0"),
    ]
    for text, value, fmt in account:
        ws.cell(row=row, column=1, value=text)
        cell = ws.cell(row=row, column=2, value=value if value is not None else "-")
        cell.number_format = fmt
        cell.alignment = Alignment(horizontal="right")
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="매매 활동").font = _SECTION_FONT
    row += 1
    actions = summary["decisions"]
    orders = summary["orders"]
    fills = [e for e in journal if e["filled_qty"]]
    buy_amt = sum(e["filled_qty"] * e["avg_price"] for e in fills if e["action"] == "BUY")
    sell_amt = sum(e["filled_qty"] * e["avg_price"] for e in fills if e["action"] == "SELL")
    activity = [
        ("AI 호출", f"{summary['ai_calls']}회 / 일일 한도 {ai_limit}회", None),
        ("AI 판단", " / ".join(f"{label(ACTION, a)} {actions.get(a, 0)}" for a in ("BUY", "SELL", "HOLD")), None),
        ("주문 (관망 제외)", ", ".join(f"{label(ORDER_STATUS, s)} {n}" for s, n in orders.items()) or "없음", None),
        ("체결 건수", summary["filled_orders"], "0"),
        ("매수 체결금액", buy_amt, WON),
        ("매도 체결금액", sell_amt, WON),
        ("실현손익 (수수료·세금 제외)", summary["realized_pnl"], SIGNED_WON),
    ]
    for text, value, fmt in activity:
        ws.cell(row=row, column=1, value=text)
        cell = ws.cell(row=row, column=2, value=value)
        cell.alignment = Alignment(horizontal="right")
        if fmt:
            cell.number_format = fmt
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="종목별 요약").font = _SECTION_FONT
    row += 1
    per: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    last: dict[str, dict] = {}
    for e in journal:
        p = per[e["symbol"]]
        p["n"] += 1
        p[e["action"]] += 1
        if e["filled_qty"]:
            p[f"{e['action']}_qty"] += e["filled_qty"]
            p[f"{e['action']}_amt"] += e["filled_qty"] * e["avg_price"]
        if e["realized_pnl"] is not None:
            p["pnl"] += e["realized_pnl"]
        last[e["symbol"]] = e
    headers = ["종목코드", "종목명", "AI 판단", "매수", "매도", "관망", "매수 체결수량", "매수금액",
               "매도 체결수량", "매도금액", "실현손익", "마지막 판단", "마지막 판단 시각"]
    _header(ws, row, headers)
    # 체결이 있었던 종목 먼저, 그다음 AI 판단이 많았던 순
    symbols = sorted(per, key=lambda s: (-per[s]["BUY_qty"] - per[s]["SELL_qty"], -per[s]["n"], s))
    table = []
    for symbol in symbols:
        p = per[symbol]
        table.append([
            symbol, names.get(symbol, ""), int(p["n"]), int(p["BUY"]), int(p["SELL"]), int(p["HOLD"]),
            int(p["BUY_qty"]) or None, p["BUY_amt"] or None, int(p["SELL_qty"]) or None, p["SELL_amt"] or None,
            p["pnl"] if "pnl" in p else None, label(ACTION, last[symbol]["action"]),
            last[symbol]["created_at"][11:19],
        ])
    end = _rows(ws, row + 1, table, {8: WON, 10: WON, 11: SIGNED_WON})
    for r, symbol in enumerate(symbols, start=row + 1):
        ws.cell(row=r, column=12).font = _ACTION_FONTS[last[symbol]["action"]]
    if not table:
        _no_data(ws, row + 1, "이 날짜의 AI 판단 기록이 없습니다.")
        end = row + 2

    notes = [
        "· 현재 총평가·보유현황은 보고서 생성 시점의 계좌 기준입니다"
        + (" (과거 날짜를 조회해도 현재 계좌)." if not is_today else "."),
        "· 루프 시작 평가금액은 매매 루프가 그날 처음 점검한 시각의 총평가로, 일일 손실한도의 기준입니다.",
        "· 종목별 요약에는 그날 AI가 호출된 종목만 나옵니다 (신호가 없으면 AI를 부르지 않음).",
        "· 실현손익·체결금액에는 수수료와 세금이 빠져 있습니다.",
    ] + [f"· {err}" for err in kis.errors]
    row = end + 1
    for note in notes:
        ws.cell(row=row, column=1, value=note).font = _NOTE_FONT
        row += 1

    # A/B double as the label/value columns of the 계좌·매매 활동 blocks
    _widths(ws, [28, 26, 9, 7, 7, 7, 13, 13, 13, 13, 12, 11, 15])
    ws.freeze_panes = "A4"


def _orders_sheet(ws: Worksheet, journal: list[dict], names: dict[str, str]) -> None:
    row = _title(ws, "주문·체결 내역", "AI가 매수/매도를 판단한 건 (리스크 관리에서 거부된 주문 포함, 관망 제외)")
    headers = ["판단 시각", "주문 시각", "종목코드", "종목명", "구분", "주문수량", "체결수량", "체결단가",
               "체결금액", "주문 상태", "체결 상태", "주문번호", "매입단가", "실현손익", "AI 확신도", "AI 판단 근거"]
    _header(ws, row, headers)
    table = []
    orders = [e for e in journal if e["action"] != "HOLD"]
    for e in orders:
        filled = e["filled_qty"] or 0
        status = label(ORDER_STATUS, e["order_status"])
        if e["order_status"] not in (None, "SUBMITTED"):
            status += f": {e['order_reason']}"
        table.append([
            e["created_at"][11:19], _hms(e["fill_order_time"]), e["symbol"], names.get(e["symbol"], ""),
            label(ACTION, e["action"]), e["quantity"], filled, e["avg_price"] if filled else None,
            filled * e["avg_price"] if filled else None, status, label(FILL_STATUS, e["fill_status"]),
            e["order_no"] or "-", e["cost_basis"], e["realized_pnl"], e["confidence"], "\n".join(e["reason"]),
        ])
    _rows(ws, row + 1, table, {8: WON, 9: WON, 13: WON, 14: SIGNED_WON, 15: CONF}, wrap={10, 16})
    for r, e in enumerate(orders, start=row + 1):
        ws.cell(row=r, column=5).font = _ACTION_FONTS[e["action"]]
    if not table:
        _no_data(ws, row + 1, "이 날짜에 매수/매도 판단이 없습니다.")
    else:
        ws.auto_filter.ref = f"A{row}:{get_column_letter(len(headers))}{row + len(table)}"
    _widths(ws, [10, 10, 9, 16, 6, 9, 9, 11, 12, 22, 11, 12, 11, 11, 9, 70])
    ws.freeze_panes = ws.cell(row=row + 1, column=5)


def _broker_sheet(ws: Worksheet, kis: KisData, journal: list[dict], names: dict[str, str]) -> None:
    row = _title(ws, "증권사 주문체결 조회 (대조용)", "KIS 일별주문체결조회 원자료 — 로컬 매매기록과 주문번호로 대조")
    if kis.orders is None:
        _no_data(ws, row, "KIS 주문체결 조회를 하지 않았거나 실패했습니다 (요약 시트 참고).")
        return
    local = {e["order_no"] for e in journal if e["order_no"]}
    headers = ["주문 시각", "주문번호", "구분", "종목코드", "종목명", "주문수량", "체결수량", "미체결",
               "평균체결가", "체결금액", "상태", "비고", "로컬 기록"]
    _header(ws, row, headers)
    table, sides = [], []
    for o in sorted(kis.orders, key=lambda o: o.get("ord_tmd", "")):
        try:
            fill = OrderFill.from_kis(o)
            side, status = fill.side, label(FILL_STATUS, fill.status)
        except OrderFillParseError:
            side, status = None, "?"
        filled = _num(o.get("tot_ccld_qty")) or 0
        avg = _num(o.get("avg_prvs"))
        note = "" if is_original_order(o) else f"정정/취소 (원주문 {o.get('orgn_odno')})"
        table.append([
            _hms(o.get("ord_tmd")), o.get("odno"), label(ACTION, side), o.get("pdno"),
            names.get(o.get("pdno"), o.get("prdt_name", "")), _num(o.get("ord_qty")), filled,
            _num(o.get("rmn_qty")), avg if filled else None,
            _num(o.get("tot_ccld_amt")) or (filled * avg if filled and avg else None),
            status, note, "일치" if o.get("odno") in local else "로컬 기록 없음",
        ])
        sides.append(side)
    end = _rows(ws, row + 1, table, {6: WON, 7: WON, 8: WON, 9: WON, 10: WON})
    for r, side in enumerate(sides, start=row + 1):
        if side:
            ws.cell(row=r, column=3).font = _ACTION_FONTS[side]
        if ws.cell(row=r, column=13).value != "일치":
            ws.cell(row=r, column=13).font = Font(bold=True, color="C00000")
    if not table:
        _no_data(ws, row + 1, "증권사에 이 날짜의 주문이 없습니다.")
        end = row + 2
    broker_nos = {o.get("odno") for o in kis.orders}
    missing = sorted(n for n in local if n not in broker_nos)
    if missing:
        ws.cell(row=end + 1, column=1, value=f"· 로컬에만 있고 증권사 조회에 없는 주문번호: {', '.join(missing)}").font = (
            Font(bold=True, color="C00000")
        )
    _widths(ws, [10, 12, 6, 9, 16, 9, 9, 8, 11, 13, 11, 22, 14])
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def _holdings_sheet(ws: Worksheet, kis: KisData, names: dict[str, str]) -> None:
    row = _title(ws, "보유 현황", "KIS 잔고조회 — 보고서 생성 시점 기준")
    if kis.balance is None:
        _no_data(ws, row, "KIS 잔고 조회를 하지 않았거나 실패했습니다 (요약 시트 참고).")
        return
    holdings = kis.balance["holdings"]
    if not holdings:
        _no_data(ws, row, "보유종목이 없습니다.")
        return
    headers = ["종목코드", "종목명", "보유수량", "매입평균가", "현재가", "매입금액", "평가금액", "평가손익",
               "수익률", "비중"]
    _header(ws, row, headers)
    total_eval = sum(_num(h.get("evlu_amt")) or 0 for h in holdings)
    table = []
    for h in sorted(holdings, key=lambda h: -(_num(h.get("evlu_amt")) or 0)):
        rate = _num(h.get("evlu_pfls_rt"))
        evlu = _num(h.get("evlu_amt"))
        table.append([
            h.get("pdno"), names.get(h.get("pdno"), h.get("prdt_name", "")), _num(h.get("hldg_qty")),
            _num(h.get("pchs_avg_pric")), _num(h.get("prpr")), _num(h.get("pchs_amt")), evlu,
            _num(h.get("evlu_pfls_amt")), rate / 100 if rate is not None else None,
            evlu / total_eval if evlu and total_eval else None,
        ])
    first = row + 1
    end = _rows(ws, first, table, {3: WON, 4: WON, 5: WON, 6: WON, 7: WON, 8: SIGNED_WON, 9: SIGNED_PCT, 10: PCT})
    last = end - 1
    total = ["합계", "", None, None, None, f"=SUM(F{first}:F{last})", f"=SUM(G{first}:G{last})",
             f"=SUM(H{first}:H{last})", f"=IF(F{end}=0,\"\",H{end}/F{end})", f"=SUM(J{first}:J{last})"]
    _rows(ws, end, [total], {6: WON, 7: WON, 8: SIGNED_WON, 9: SIGNED_PCT, 10: PCT})
    for c in range(1, len(headers) + 1):
        ws.cell(row=end, column=c).fill = _TOTAL_FILL
        ws.cell(row=end, column=c).font = _BOLD
    _widths(ws, [10, 18, 9, 12, 12, 13, 13, 12, 9, 8])
    ws.freeze_panes = ws.cell(row=first, column=1)

    chart = BarChart()
    chart.type = "bar"
    chart.title = "종목별 평가손익 (원)"
    chart.legend = None
    chart.height = max(6.0, 1.0 * len(table) + 3)
    chart.width = 16
    chart.add_data(Reference(ws, min_col=8, min_row=first, max_row=last), titles_from_data=False)
    chart.set_categories(Reference(ws, min_col=2, min_row=first, max_row=last))
    series = chart.series[0]
    series.graphicalProperties.solidFill = "2A78D6"
    series.graphicalProperties.line.noFill = True
    series.invertIfNegative = False
    chart.y_axis.numFmt = "#,##0"
    chart.y_axis.majorGridlines = None
    chart.x_axis.delete = False
    chart.y_axis.delete = False
    ws.add_chart(chart, f"A{end + 3}")


def _decisions_sheet(ws: Worksheet, journal: list[dict], names: dict[str, str]) -> None:
    row = _title(ws, "AI 판단 내역", "매매 루프가 AI를 호출한 모든 건 — 판단 당시 지표와 호출 이유(신호) 포함")
    headers = ["시각", "종목코드", "종목명", "판단", "수량", "확신도", "보유수량", "현재가", "등락률",
               "MA5", "MA20", "RSI14", "거래량비", "호출 신호", "결과", "판단 근거"]
    _header(ws, row, headers)
    table = []
    for e in journal:
        ind = e["indicators"]
        change = ind.get("change_pct")
        if e["action"] == "HOLD":
            result = "주문 없음"
        elif e["order_status"] == "SUBMITTED":
            result = f"{label(ORDER_STATUS, 'SUBMITTED')} / {label(FILL_STATUS, e['fill_status'])}"
        else:
            result = f"{label(ORDER_STATUS, e['order_status'])}: {e['order_reason'] or ''}"
        table.append([
            e["created_at"][11:19], e["symbol"], names.get(e["symbol"], ""), label(ACTION, e["action"]),
            e["quantity"] or None, e["confidence"], e["held_quantity"], ind.get("price"),
            change / 100 if change is not None else None, ind.get("ma5"), ind.get("ma20"), ind.get("rsi14"),
            ind.get("volume_ratio"), "\n".join(e["signal_triggers"]), result, "\n".join(e["reason"]),
        ])
    _rows(ws, row + 1, table, {6: CONF, 8: WON, 9: SIGNED_PCT, 10: WON, 11: WON, 12: "0.0", 13: "0.00"},
          wrap={14, 15, 16})
    for r, e in enumerate(journal, start=row + 1):
        ws.cell(row=r, column=4).font = _ACTION_FONTS.get(e["action"], Font())
    if not table:
        _no_data(ws, row + 1, "이 날짜의 AI 판단 기록이 없습니다.")
    else:
        ws.auto_filter.ref = f"A{row}:{get_column_letter(len(headers))}{row + len(table)}"
    _widths(ws, [9, 9, 16, 6, 6, 7, 8, 11, 8, 11, 11, 7, 8, 22, 18, 80])
    ws.freeze_panes = ws.cell(row=row + 1, column=5)


# --- entry points ------------------------------------------------------------


def build_workbook(
    trade_date: str,
    journal: list[dict],
    summary: dict,
    start_equity: float | None,
    ai_limit: int,
    kis: KisData,
    generated_at: datetime,
) -> Workbook:
    names = _names(kis)
    is_today = generated_at.strftime("%Y%m%d") == trade_date
    wb = Workbook()
    _summary_sheet(wb.active, trade_date, is_today, journal, summary, start_equity, ai_limit, kis, names,
                   generated_at)
    wb.active.title = "요약"
    _orders_sheet(wb.create_sheet("주문·체결"), journal, names)
    _holdings_sheet(wb.create_sheet("보유현황"), kis, names)
    _decisions_sheet(wb.create_sheet("AI판단내역"), journal, names)
    _broker_sheet(wb.create_sheet("증권사대조"), kis, journal, names)
    return wb


def export(
    trade_date: str,
    use_kis: bool = True,
    memory: TradeMemory | None = None,
    kis: KisData | None = None,
    out_dir: Path = _REPORTS_DIR,
) -> Path:
    own_memory = memory is None
    memory = memory or TradeMemory()
    try:
        journal = memory.day_journal(trade_date)
        summary = memory.daily_summary(trade_date)
        start_equity = memory.start_equity_on(trade_date)
    finally:
        if own_memory:
            memory.close()
    if kis is None:
        kis = fetch_kis(trade_date) if use_kis else KisData(errors=["KIS 조회 생략 (--no-kis)"])
    for err in kis.errors:
        logger.warning(err)

    now = datetime.now(_KST)
    wb = build_workbook(trade_date, journal, summary, start_equity, get_settings().ai_daily_call_limit, kis, now)
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"매매일지_{trade_date}.xlsx"
    try:
        wb.save(path)
    except PermissionError:
        # Same-day file still open in Excel -- don't fail, write a new one.
        path = out_dir / f"매매일지_{trade_date}_{now:%H%M%S}.xlsx"
        wb.save(path)
    logger.info("매매일지 저장: %s (AI 판단 %d건, 체결 %d건)", path, len(journal), summary["filled_orders"])
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="자동매매 일일 매매일지(엑셀) 생성 -- 조회 전용")
    parser.add_argument("date", nargs="?", help="YYYYMMDD (기본: 오늘, KST)")
    parser.add_argument("--no-kis", action="store_true", help="KIS 조회 없이 로컬 매매기록만으로 생성")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    trade_date = args.date or datetime.now(_KST).strftime("%Y%m%d")
    if not _DATE_RE.match(trade_date):
        parser.error(f"날짜는 YYYYMMDD 형식이어야 합니다: {trade_date!r}")
    print(export(trade_date, use_kis=not args.no_kis))


if __name__ == "__main__":
    main()
