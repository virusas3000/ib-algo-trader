#!/usr/bin/env python3
"""
Queries trades.db for today's win/loss stats and prints a Telegram-friendly report.
"""
import os
import sqlite3
from datetime import datetime

DB_FILE = os.path.expanduser("~/Desktop/ib_algo_trader/trades.db")


def report():
    if not os.path.exists(DB_FILE):
        print("📊 Trade DB not initialized yet. Run trade_logger.py first.")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_FILE)

    rows = conn.execute(
        "SELECT symbol, side, pnl, reason, strategy FROM trades "
        "WHERE exit_time LIKE ? ORDER BY exit_time",
        (f"{today}%",),
    ).fetchall()

    if not rows:
        # Fallback: show most recent trading day
        last = conn.execute(
            "SELECT DATE(exit_time) FROM trades ORDER BY exit_time DESC LIMIT 1"
        ).fetchone()
        if last:
            last_day = last[0]
            rows = conn.execute(
                "SELECT symbol, side, pnl, reason, strategy FROM trades "
                "WHERE exit_time LIKE ? ORDER BY exit_time",
                (f"{last_day}%",),
            ).fetchall()
            header = f"📊 No trades today ({today}). Showing **{last_day}**:"
        else:
            print("📊 No trades in database.")
            conn.close()
            return
    else:
        header = f"📊 **Trading Report — {today}**"

    total = len(rows)
    wins = [r for r in rows if r[2] > 0]
    losses = [r for r in rows if r[2] <= 0]
    win_rate = (len(wins) / total) * 100 if total else 0
    total_pnl = sum(r[2] for r in rows)
    best = max(rows, key=lambda r: r[2])
    worst = min(rows, key=lambda r: r[2])

    lines = [
        header,
        "─" * 28,
        f"Total Trades: **{total}**",
        f"Wins: ✅ **{len(wins)}**   Losses: ❌ **{len(losses)}**",
        f"**Win Rate: {win_rate:.1f}%**",
        f"**Net P&L: ${total_pnl:+,.2f}**",
        "",
        f"🏆 Best:  {best[0]} {best[1]} ${best[2]:+.2f} ({best[3]})",
        f"💀 Worst: {worst[0]} {worst[1]} ${worst[2]:+.2f} ({worst[3]})",
    ]
    print("\n".join(lines))
    conn.close()


if __name__ == "__main__":
    report()
