"""Stores AI decisions and their eventual outcomes (decision_id,
timestamp, symbol, market snapshot, AI action/reason/confidence, risk
manager result, order/fill info, entry/exit price, realized PnL).

Provides retrieval of relevant past trades for context -- never dumps
the entire history into a prompt (recent_trades() is capped and
summarized).

Also persists the risk.risk_manager state that must survive a restart
(executed decision_ids, emergency stop) by implementing its
RiskStateStore protocol, plus each day's starting equity for the daily
loss limit.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import get_settings
from memory.database import connect
from models.decision import Decision
from models.order import OrderFill, OrderResult

_KST = ZoneInfo("Asia/Seoul")

# Cap on how many past trades are handed to the AI per call.
RECENT_TRADES_LIMIT = 5


def _now() -> str:
    return datetime.now(_KST).isoformat(timespec="seconds")


class TradeMemory:
    def __init__(self, path: str | Path | None = None):
        self._conn = connect(path or get_settings().trade_db_path)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    def _write(self, sql: str, params: tuple) -> None:
        with self._lock, self._conn:
            self._conn.execute(sql, params)

    def _read(self, sql: str, params: tuple = ()) -> list:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # --- trade history ------------------------------------------------------

    def record_decision(self, decision_id: str, decision: Decision, snapshot: dict) -> None:
        self._write(
            """INSERT INTO decisions (decision_id, created_at, symbol, action, quantity,
                   confidence, reason, risk_score, stop_loss, take_profit,
                   invalidation_condition, snapshot)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id,
                _now(),
                decision.symbol,
                decision.action,
                decision.quantity,
                decision.confidence,
                json.dumps(list(decision.reason), ensure_ascii=False),
                decision.risk_score,
                decision.stop_loss,
                decision.take_profit,
                decision.invalidation_condition,
                json.dumps(snapshot, ensure_ascii=False, default=str),
            ),
        )

    def record_order(self, result: OrderResult) -> None:
        self._write(
            """INSERT INTO orders (decision_id, created_at, side, symbol, quantity, status,
                   reason, order_no, order_org_no, order_time, requested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result.decision_id,
                _now(),
                result.side,
                result.symbol,
                result.quantity,
                result.status,
                result.reason,
                result.order_no,
                result.order_org_no,
                result.order_time,
                result.requested_at,
            ),
        )

    def record_fill(self, decision_id: str, fill: OrderFill, cost_basis: float | None = None) -> None:
        """Upserts the latest fill state for fill.order_no. For a SELL with a
        known cost_basis (매입평균가), stores realized PnL before fees/tax."""
        realized_pnl = None
        if fill.side == "SELL" and cost_basis is not None and fill.filled_qty > 0:
            realized_pnl = (fill.avg_price - cost_basis) * fill.filled_qty
        self._write(
            """INSERT INTO fills (order_no, decision_id, updated_at, side, symbol, status,
                   ordered_qty, filled_qty, remaining_qty, avg_price, order_time,
                   cost_basis, realized_pnl)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(order_no) DO UPDATE SET
                   updated_at = excluded.updated_at,
                   status = excluded.status,
                   filled_qty = excluded.filled_qty,
                   remaining_qty = excluded.remaining_qty,
                   avg_price = excluded.avg_price,
                   cost_basis = excluded.cost_basis,
                   realized_pnl = excluded.realized_pnl""",
            (
                fill.order_no,
                decision_id,
                _now(),
                fill.side,
                fill.symbol,
                fill.status,
                fill.ordered_qty,
                fill.filled_qty,
                fill.remaining_qty,
                fill.avg_price,
                fill.order_time,
                cost_basis if fill.side == "SELL" else None,
                realized_pnl,
            ),
        )

    def recent_trades(self, symbol: str, limit: int = RECENT_TRADES_LIMIT) -> list[dict]:
        """The last `limit` decisions for `symbol`, newest first, as compact
        dicts (no raw snapshots) suitable for an AI prompt."""
        limit = min(limit, RECENT_TRADES_LIMIT)
        rows = self._read(
            """SELECT d.created_at, d.action, d.quantity, d.confidence, d.reason,
                      o.status AS order_status, o.reason AS order_reason,
                      f.status AS fill_status, f.filled_qty, f.avg_price, f.realized_pnl
               FROM decisions d
               LEFT JOIN orders o ON o.decision_id = d.decision_id
               LEFT JOIN fills f ON f.decision_id = d.decision_id
               WHERE d.symbol = ?
               ORDER BY d.created_at DESC, d.rowid DESC
               LIMIT ?""",
            (symbol, limit),
        )
        trades = []
        for row in rows:
            trade = {
                "at": row["created_at"],
                "action": row["action"],
                "quantity": row["quantity"],
                "confidence": row["confidence"],
                "reason": json.loads(row["reason"]),
                "order_status": row["order_status"],
            }
            if row["order_status"] == "REJECTED_BY_RISK":
                trade["rejected_because"] = row["order_reason"]
            if row["fill_status"] is not None:
                trade["fill"] = {
                    "status": row["fill_status"],
                    "filled_qty": row["filled_qty"],
                    "avg_price": row["avg_price"],
                }
            if row["realized_pnl"] is not None:
                trade["realized_pnl"] = row["realized_pnl"]
            trades.append(trade)
        return trades

    # --- daily equity -------------------------------------------------------

    def day_start_equity(self, current_equity: float, trade_date: str | None = None) -> float:
        """Starting equity for `trade_date` (YYYYMMDD KST, default today):
        the first value recorded that day wins, so a restart mid-day keeps
        measuring the daily loss from the same baseline."""
        trade_date = trade_date or datetime.now(_KST).strftime("%Y%m%d")
        self._write(
            "INSERT OR IGNORE INTO daily_equity (trade_date, start_equity, recorded_at) VALUES (?, ?, ?)",
            (trade_date, current_equity, _now()),
        )
        return self._read("SELECT start_equity FROM daily_equity WHERE trade_date = ?", (trade_date,))[0][0]

    # --- risk.risk_manager.RiskStateStore -----------------------------------

    def load_executed_decision_ids(self) -> set[str]:
        return {row[0] for row in self._read("SELECT decision_id FROM executed_decisions")}

    def save_executed_decision_id(self, decision_id: str) -> None:
        self._write(
            "INSERT OR IGNORE INTO executed_decisions (decision_id, executed_at) VALUES (?, ?)",
            (decision_id, _now()),
        )

    def load_emergency_stop(self) -> str | None:
        rows = self._read("SELECT reason FROM emergency_stop WHERE id = 1")
        return rows[0][0] if rows else None

    def save_emergency_stop(self, reason: str | None) -> None:
        if reason is None:
            self._write("DELETE FROM emergency_stop WHERE id = 1", ())
        else:
            self._write(
                "INSERT OR REPLACE INTO emergency_stop (id, reason, engaged_at) VALUES (1, ?, ?)",
                (reason, _now()),
            )
