"""Korean display labels for Telegram messages.

Internal status values (SUBMITTED, FILLED, ...) stay English in code,
logs and the database; only what's shown to the user is translated here.
"""

from __future__ import annotations

ACTION = {"BUY": "매수", "SELL": "매도", "HOLD": "관망"}

ORDER_STATUS = {
    "SUBMITTED": "주문 접수",
    "REJECTED_BY_RISK": "리스크 거부",
    "REJECTED_BY_BROKER": "증권사 거부",
    "UNKNOWN": "결과 불명",
}

FILL_STATUS = {
    "FILLED": "체결 완료",
    "PARTIALLY_FILLED": "일부 체결",
    "PENDING": "미체결 대기",
    "CANCELLED": "취소",
    "REJECTED": "거부",
}

RECONCILIATION = {
    "PLACED": "주문 확인됨",
    "NOT_FOUND": "아직 주문 목록에 없음 (늦게 접수될 수 있음)",
    "AMBIGUOUS": "후보가 여러 건 -- 직접 확인 필요",
}


def label(table: dict[str, str], value: str | None) -> str:
    if value is None:
        return "-"
    return table.get(value, value)
