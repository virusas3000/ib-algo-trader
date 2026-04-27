#!/usr/bin/env python3
"""
Parses algo.log for TRADE EXITED events and inserts them into trades.db.
Idempotent: uses (timestamp, symbol) as unique key so re-runs don't duplicate.
"""
import os
import re
import sqlite3
from datetime import datetime

LOG_FILE = os.path.expanduser("~/Desktop/ib_algo_trader/algo.log")
DB_FILE = os.path.expanduser("~/Desktop/ib_algo_trader/trades.db")

# Example line:
# 2026-04-25 01:38:32,891 [INFO] strategy: TRADE EXITED | GE LONG @ 283.82 | P&L=$+620.16 | Reason=TARGET1 | Strategy=ORB
PATTERN = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*? TRADE EXITED \| "
    r"(?P<sym>\S+) (?P<side>LONG|SHORT) @ (?P<px>[\d.]+) \| "
    r"P&L=\$(?P<pnl>[+-]?[\d.]+) \| Reason=(?P<reason>\S+) \| Strategy=(?P<strat>\S+)"
)


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exit_time TEXT NOT NULL,
            symbol    TEXT NOT NULL,
            side      TEXT NOT NULL,
            exit_price REAL,
            pnl       REAL NOT NULL,
            reason    TEXT,
            strategy  TEXT,
            UNIQUE(exit_time, symbol)
        )
    """)
    conn.commit()


def ingest():
    if not os.path.exists(LOG_FILE):
        print(f"Log file not found: {LOG_FILE}")
        return 0

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    inserted = 0
    with open(LOG_FILE, "r") as f:
        for line in f:
            m = PATTERN.search(line)
            if not m:
                continue
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO trades (exit_time, symbol, side, exit_price, pnl, reason, strategy) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        m.group("ts"),
                        m.group("sym"),
                        m.group("side"),
                        float(m.group("px")),
                        float(m.group("pnl")),
                        m.group("reason"),
                        m.group("strat"),
                    ),
                )
                if conn.total_changes > inserted:
                    inserted = conn.total_changes
            except Exception as e:
                print(f"Skip line: {e}")

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    conn.close()
    print(f"Ingested {inserted} new trades. DB total: {total}")
    return inserted


if __name__ == "__main__":
    ingest()
