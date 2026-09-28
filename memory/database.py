"""SQLite connection/schema management for local persistence.

One file per machine (TRADE_DB_PATH, default trade_memory.sqlite3 in the
project root, gitignored) -- desktop and laptop keep separate histories.
Schema changes are additive (CREATE ... IF NOT EXISTS) until a migration
is actually needed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT NOT NULL,              -- JSON list
    risk_score REAL,
    stop_loss REAL,
    take_profit REAL,
    invalidation_condition TEXT,
    snapshot TEXT NOT NULL             -- JSON market snapshot given to the AI
);

-- One row per submit attempt outcome, including risk rejections.
CREATE TABLE IF NOT EXISTS orders (
    decision_id TEXT PRIMARY KEY REFERENCES decisions(decision_id),
    created_at TEXT NOT NULL,
    side TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    status TEXT NOT NULL,              -- models.order.OrderStatus
    reason TEXT NOT NULL,
    order_no TEXT,
    order_org_no TEXT,
    order_time TEXT,
    requested_at TEXT
);

-- Latest known fill state per KIS order number (upserted).
CREATE TABLE IF NOT EXISTS fills (
    order_no TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
    updated_at TEXT NOT NULL,
    side TEXT NOT NULL,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,              -- models.order.FillStatus
    ordered_qty INTEGER NOT NULL,
    filled_qty INTEGER NOT NULL,
    remaining_qty INTEGER NOT NULL,
    avg_price REAL NOT NULL,
    order_time TEXT NOT NULL,
    cost_basis REAL,                   -- SELL: 매입평균가 at decision time
    realized_pnl REAL                  -- SELL: (avg_price - cost_basis) * filled_qty, before fees
);

CREATE TABLE IF NOT EXISTS daily_equity (
    trade_date TEXT PRIMARY KEY,       -- YYYYMMDD, KST
    start_equity REAL NOT NULL,
    recorded_at TEXT NOT NULL
);

-- risk.risk_manager state that must survive a restart.
CREATE TABLE IF NOT EXISTS executed_decisions (
    decision_id TEXT PRIMARY KEY,
    executed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emergency_stop (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    reason TEXT NOT NULL,
    engaged_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_symbol_created ON decisions(symbol, created_at);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
