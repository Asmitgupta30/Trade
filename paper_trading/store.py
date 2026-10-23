"""SQLite persistence for the paper-trading engine.

Three tracks kept strictly separate:
  * suggestions        — every suggestion the advisor emitted (immutable log)
  * paper_positions    — HYPOTHETICAL fills "as if you followed every suggestion"
  * user_trades        — what the human ACTUALLY did (self-reported)

The two P&L streams are never merged: the UI shows both honestly.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestions (
    suggestion_id TEXT PRIMARY KEY,
    created_ts TEXT NOT NULL,
    phase TEXT,
    horizon TEXT,
    symbol TEXT NOT NULL,
    sector TEXT,
    action TEXT NOT NULL,
    amount_inr REAL,
    quantity REAL,
    reference_price REAL,
    confidence REAL,
    rationale TEXT,
    est_cost_inr REAL,
    baseline_comparison TEXT,
    blocked_by TEXT,
    warnings TEXT,
    budget_inr REAL
);
CREATE TABLE IF NOT EXISTS paper_positions (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL DEFAULT 'BUY',
    quantity REAL NOT NULL,
    entry_ts TEXT NOT NULL,
    entry_price REAL NOT NULL,
    entry_cost_inr REAL DEFAULT 0,
    trade_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',   -- open | closed
    exit_ts TEXT,
    exit_price REAL,
    realized_return_pct REAL
);
CREATE TABLE IF NOT EXISTS user_trades (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL,
    price REAL,
    ts TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS system_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,       -- banner | pause_reset | gate_flip | ...
    payload TEXT
);
"""


class PaperStore:
    def __init__(self, db_path: Path | None = None):
        # PAPER_DB_PATH lets tests point at a scratch database so they can
        # never pollute the production accrual record (a real bug that
        # shipped: 32 TESTSYM skips + 2613 preview rows leaked in via pytest).
        if db_path is not None:
            base = Path(db_path)
        elif os.environ.get("PAPER_DB_PATH"):
            base = Path(os.environ["PAPER_DB_PATH"])
        else:
            base = Path(__file__).resolve().parent.parent / "var" / "paper.sqlite3"
        base.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(base), check_same_thread=False, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            try:
                self.conn.execute("PRAGMA journal_mode=WAL;")
                self.conn.execute("PRAGMA busy_timeout=5000;")
            except Exception:
                pass
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def close(self):
        with self._lock:
            self.conn.close()

    # ---------------- suggestions ----------------
    def save_suggestion(self, s: dict, phase: str, horizon: str, budget_inr: float) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO suggestions
                   (suggestion_id, created_ts, phase, horizon, symbol, sector, action,
                    amount_inr, quantity, reference_price, confidence, rationale,
                    est_cost_inr, baseline_comparison, blocked_by, warnings, budget_inr)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s["suggestion_id"], s.get("created_ts") or datetime.now(timezone.utc).isoformat(),
                 phase, horizon, s["symbol"], s.get("sector"), s["action"],
                 s.get("amount_inr", 0), s.get("est_quantity", 0),
                 s.get("reference_price"), s.get("confidence"),
                 s.get("rationale"), s.get("est_cost_inr", 0),
                 s.get("baseline_comparison"),
                 json.dumps(s.get("blocked_by", [])), json.dumps(s.get("warnings", [])),
                 budget_inr))
            self.conn.commit()

    def suggestions_between(self, start_iso: str, end_iso: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM suggestions WHERE created_ts BETWEEN ? AND ?",
                (start_iso, end_iso)).fetchall()
            return [dict(r) for r in rows]

    # ---------------- hypothetical positions ----------------
    def open_paper_position(self, suggestion_id: str, symbol: str, quantity: float,
                            entry_price: float, entry_cost_inr: float,
                            entry_dt: datetime, trade_date: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO paper_positions
                   (suggestion_id, symbol, side, quantity, entry_ts, entry_price,
                    entry_cost_inr, trade_date, status)
                   VALUES (?,?,?,?,?,?,?,?,'open')""",
                (suggestion_id, symbol, "BUY", quantity,
                 entry_dt.isoformat(), entry_price, entry_cost_inr, trade_date))
            self.conn.commit()
            return int(cur.lastrowid)

    def close_paper_position(self, position_id: int, exit_price: float,
                             exit_dt: datetime, round_trip_cost_pct: float) -> float | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT entry_price, quantity FROM paper_positions WHERE position_id=?",
                (position_id,)).fetchone()
            if not row:
                return None
            ret_pct = (exit_price - row["entry_price"]) / row["entry_price"] * 100.0 \
                - round_trip_cost_pct
            self.conn.execute(
                """UPDATE paper_positions SET status='closed', exit_ts=?, exit_price=?,
                   realized_return_pct=? WHERE position_id=?""",
                (exit_dt.isoformat(), exit_price, ret_pct, position_id))
            self.conn.commit()
            return ret_pct

    def open_positions(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM paper_positions WHERE status='open'").fetchall()
            return [dict(r) for r in rows]

    def all_positions(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM paper_positions").fetchall()
            return [dict(r) for r in rows]

    def positions_for_date(self, trade_date: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM paper_positions WHERE trade_date=?", (trade_date,)).fetchall()
            return [dict(r) for r in rows]

    # ---------------- user-reported trades ----------------
    def save_user_trade(self, symbol: str, side: str, quantity: float, price: float,
                        ts: datetime, suggestion_id: str | None, note: str = "") -> int:
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO user_trades (suggestion_id, symbol, side, quantity, price,
                   ts, trade_date, note) VALUES (?,?,?,?,?,?,?,?)""",
                (suggestion_id, symbol, side, quantity, price, ts.isoformat(),
                 ts.strftime("%Y-%m-%d"), note))
            self.conn.commit()
            return int(cur.lastrowid)

    def user_trades(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM user_trades ORDER BY ts").fetchall()
            return [dict(r) for r in rows]

    # ---------------- events ----------------
    def log_event(self, kind: str, payload: dict) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO system_events (ts, kind, payload) VALUES (?,?,?)",
                (datetime.now(timezone.utc).isoformat(), kind, json.dumps(payload)))
            self.conn.commit()

    def events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM system_events ORDER BY event_id DESC LIMIT ?",
                (limit,)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception:
                    pass
                out.append(d)
            return out
