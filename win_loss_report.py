#!/usr/bin/env python3
"""Win/Loss report — covers BOTH closed trades (from trades.db) AND live open
positions (parsed from the most recent [SCAN] line in algo.log)."""
import os, re, glob, sqlite3, subprocess, sys
from datetime import datetime

ROOT     = os.path.expanduser("~/Desktop/ib_algo_trader")
DB_FILE  = f"{ROOT}/trades.db"

# Always sync log → DB before reading
try:
    subprocess.run([sys.executable, f"{ROOT}/trade_logger.py"],
                   cwd=ROOT, capture_output=True, timeout=10)
except Exception:
    pass

def _resolve_log_file():
    today = datetime.now().strftime("%Y%m%d")
    candidates = [
        f"{ROOT}/logs/trader_{today}.log",
        f"{ROOT}/algo.log",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    # fallback: most recent trader_*.log
    files = sorted(glob.glob(f"{ROOT}/logs/trader_*.log"), key=os.path.getmtime, reverse=True)
    return files[0] if files else f"{ROOT}/algo.log"

LOG_FILE = _resolve_log_file()

SCAN_RE = re.compile(
    r"\[SCAN\]\s+(\d{1,2}:\d{2})\s+ET\s*\|\s*Positions:\s*(\d+)\s*\|\s*Trades:\s*(\d+)\s*\|\s*P&L:\s*\$([+-]?[\d,\.]+)"
)


def parse_live_state():
    """Return dict from last [SCAN] line in today's log, or None."""
    if not os.path.exists(LOG_FILE):
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    last = None
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            tail = f.read().decode(errors="ignore").splitlines()
        for line in tail:
            if today in line and "[SCAN]" in line:
                m = SCAN_RE.search(line)
                if m:
                    last = m
        if not last:
            for line in tail:
                if "[SCAN]" in line:
                    m = SCAN_RE.search(line)
                    if m:
                        last = m
    except Exception:
        return None
    if not last:
        return None
    return {
        "et_time": last.group(1),
        "positions": int(last.group(2)),
        "trades": int(last.group(3)),
        "floating_pnl": float(last.group(4).replace(",", "")),
    }


def report():
    today = datetime.now().strftime("%Y-%m-%d")
    live = parse_live_state()
    rows = []
    last_day = today
    closed_header_date = today

    if os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        rows = conn.execute(
            "SELECT symbol, side, pnl, reason FROM trades "
            "WHERE exit_time LIKE ? ORDER BY exit_time",
            (f"{today}%",),
        ).fetchall()
        if not rows:
            r = conn.execute(
                "SELECT DATE(exit_time) FROM trades ORDER BY exit_time DESC LIMIT 1"
            ).fetchone()
            if r:
                last_day = r[0]
                rows = conn.execute(
                    "SELECT symbol, side, pnl, reason FROM trades "
                    "WHERE exit_time LIKE ? ORDER BY exit_time",
                    (f"{last_day}%",),
                ).fetchall()
                closed_header_date = last_day
        conn.close()

    out = []
    out.append(f"📊 **Trading Report — {today}**")
    out.append("─" * 28)

    # ── LIVE section ──
    if live:
        sign = "+" if live["floating_pnl"] >= 0 else ""
        out.append("**🟢 LIVE NOW**")
        out.append(f"  Time: {live['et_time']} ET")
        out.append(f"  Open positions: **{live['positions']}**")
        out.append(f"  Trades entered today: **{live['trades']}**")
        out.append(f"  Floating P&L: **${sign}{live['floating_pnl']:,.2f}**")
        out.append("")
    else:
        out.append("_(bot offline — no live data)_\n")

    # ── CLOSED section ──
    if rows:
        total = len(rows)
        wins = [r for r in rows if r[2] > 0]
        losses = [r for r in rows if r[2] <= 0]
        wr = (len(wins) / total) * 100 if total else 0
        net = sum(r[2] for r in rows)
        best = max(rows, key=lambda r: r[2])
        worst = min(rows, key=lambda r: r[2])
        label = "✅ CLOSED TODAY" if closed_header_date == today else f"📁 Last closed day ({closed_header_date})"
        out.append(f"**{label}**")
        out.append(f"  Total: **{total}**   Wins: ✅ {len(wins)}   Losses: ❌ {len(losses)}")
        out.append(f"  Win Rate: **{wr:.1f}%**   Net: **${net:+,.2f}**")
        out.append(f"  🏆 Best:  {best[0]} {best[1]} ${best[2]:+.2f} ({best[3]})")
        out.append(f"  💀 Worst: {worst[0]} {worst[1]} ${worst[2]:+.2f} ({worst[3]})")
    else:
        out.append("**No closed trades in DB yet.**")

    # ── COMBINED section ──
    if live and rows and closed_header_date == today:
        combined = live["floating_pnl"] + sum(r[2] for r in rows)
        out.append("")
        out.append(f"**💰 Day total (closed + floating): ${combined:+,.2f}**")

    print("\n".join(out))


if __name__ == "__main__":
    report()
