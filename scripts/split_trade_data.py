"""Splits the live trade DB into two archive files by date (e.g. the old
paper account vs. the contest account). Read-only on the live DB: it makes
a consistent copy with SQLite's backup API, then trims each copy.

    python scripts/split_trade_data.py --cutoff 2026-10-13 \
        --before-name old_account --after-name contest_account

Writes data/trade_memory_<name>_<first>-<last>.sqlite3 (never overwrites).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# table -> (date column, "iso" created_at style or "ymd" YYYYMMDD style)
BY_DATE = {
    "decisions": ("created_at", "iso"),
    "ai_calls": ("called_at", "iso"),
    "executed_decisions": ("executed_at", "iso"),
    "daily_equity": ("trade_date", "ymd"),
    "ladder_plans": ("trade_date", "ymd"),
}


def _copy(src: Path, dst: Path) -> sqlite3.Connection:
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    source.backup(target)
    source.close()
    return target


def _trim(conn: sqlite3.Connection, cutoff_iso: str, keep: str) -> None:
    """keep: 'before' (< cutoff) or 'after' (>= cutoff)."""
    cutoff_ymd = cutoff_iso.replace("-", "")
    drop = ">=" if keep == "before" else "<"
    for table, (col, style) in BY_DATE.items():
        value = cutoff_iso if style == "iso" else cutoff_ymd
        conn.execute(f"DELETE FROM {table} WHERE {col} {drop} ?", (value,))
    # children follow their decision
    conn.execute("DELETE FROM orders WHERE decision_id NOT IN (SELECT decision_id FROM decisions)")
    conn.execute("DELETE FROM fills WHERE decision_id NOT IN (SELECT decision_id FROM decisions)")
    # live state (current emergency stop / pause) belongs to the live DB only
    conn.execute("DELETE FROM emergency_stop")
    conn.execute("DELETE FROM pause")
    conn.commit()


def _span(conn: sqlite3.Connection) -> tuple[str, str]:
    lo, hi = conn.execute("SELECT MIN(substr(created_at,1,10)), MAX(substr(created_at,1,10)) FROM decisions").fetchone()
    return (lo or "none").replace("-", ""), (hi or "none").replace("-", "")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(ROOT / "trade_memory.sqlite3"))
    p.add_argument("--cutoff", required=True, help="YYYY-MM-DD: before this date vs. this date and after")
    p.add_argument("--before-name", default="before")
    p.add_argument("--after-name", default="after")
    p.add_argument("--out-dir", default=str(ROOT / "data"))
    args = p.parse_args()

    out = Path(args.out_dir)
    out.mkdir(exist_ok=True)
    tmp = {k: out / f".tmp_{k}.sqlite3" for k in ("before", "after")}
    for path in tmp.values():
        path.unlink(missing_ok=True)

    final: list[Path] = []
    for keep, name in (("before", args.before_name), ("after", args.after_name)):
        conn = _copy(Path(args.db), tmp[keep])
        _trim(conn, args.cutoff, keep)
        lo, hi = _span(conn)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("decisions", "orders", "fills", "ai_calls", "daily_equity", "ladder_plans")}
        conn.execute("VACUUM")
        conn.close()
        dest = out / f"trade_memory_{name}_{lo}-{hi}.sqlite3"
        if dest.exists():
            tmp[keep].unlink()
            sys.exit(f"{dest} already exists -- not overwriting (move it away first)")
        tmp[keep].rename(dest)
        final.append(dest)
        print(f"{dest.name}: {counts}")


if __name__ == "__main__":
    main()
