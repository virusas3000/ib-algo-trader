#!/usr/bin/env python3
"""
TWS <-> DB Sync Checker
Compares IB TWS account data against local trades.db
Runs daily to detect P&L discrepancies and missing trades.
"""
import sqlite3
import json
import os
from datetime import datetime, date
from ib_insync import IB

DB_PATH = os.path.join(os.path.dirname(__file__), "trades.db")
REPORT_PATH = os.path.expanduser("~/Desktop/ib_algo_trader/logs/sync_report.json")

def get_tws_data():
    ib = IB()
    ib.connect("127.0.0.1", 7497, clientId=97)

    summary = {a.tag: float(a.value) for a in ib.accountSummary()
               if a.tag in ["NetLiquidation", "TotalCashValue", "RealizedPnL", "UnrealizedPnL", "GrossPositionValue"]}

    # Dedupe — IB returns multiple currency rows, take first non-zero
    deduped = {}
    for a in ib.accountSummary():
        if a.tag in ["NetLiquidation", "TotalCashValue", "RealizedPnL", "UnrealizedPnL", "GrossPositionValue"]:
            if a.tag not in deduped or (float(a.value) != 0 and deduped[a.tag] == 0):
                deduped[a.tag] = float(a.value)

    positions = [
        {"symbol": p.contract.symbol, "qty": p.position, "avg_cost": p.avgCost}
        for p in ib.positions()
    ]

    # Get today's fills
    fills = []
    for fill in ib.fills():
        fills.append({
            "symbol": fill.contract.symbol,
            "action": fill.execution.side,
            "qty": fill.execution.shares,
            "price": fill.execution.price,
            "time": str(fill.execution.time),
            "pnl": fill.commissionReport.realizedPNL if fill.commissionReport else None
        })

    ib.disconnect()
    return deduped, positions, fills


def get_db_data():
    conn = sqlite3.connect(DB_PATH)
    today = date.today().isoformat()

    total_pnl = conn.execute("SELECT COALESCE(SUM(pnl),0) FROM trades").fetchone()[0]
    today_pnl = conn.execute(
        "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE date(exit_time) = ?", (today,)
    ).fetchone()[0]
    trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    today_count = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE date(exit_time) = ?", (today,)
    ).fetchone()[0]
    open_in_db = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE exit_price IS NULL OR exit_price = 0"
    ).fetchone()[0]

    conn.close()
    return {
        "total_pnl": total_pnl,
        "today_pnl": today_pnl,
        "trade_count": trade_count,
        "today_count": today_count,
        "open_in_db": open_in_db
    }


def run_check():
    print(f"\n{'='*50}")
    print(f"TWS <-> DB SYNC CHECK — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    try:
        tws, positions, fills = get_tws_data()
    except Exception as e:
        print(f"❌ Could not connect to TWS: {e}")
        return

    db = get_db_data()

    tws_realized = tws.get("RealizedPnL", 0)
    db_total = db["total_pnl"]
    discrepancy = db_total - tws_realized
    pct_diff = abs(discrepancy / tws_realized * 100) if tws_realized != 0 else 0

    print(f"\n📊 P&L COMPARISON")
    print(f"  TWS Realized P&L : ${tws_realized:>12,.2f}")
    print(f"  DB Total P&L     : ${db_total:>12,.2f}")
    print(f"  Discrepancy      : ${discrepancy:>12,.2f}  ({pct_diff:.1f}%)")

    if pct_diff > 10:
        print(f"  ⚠️  LARGE DISCREPANCY — DB is out of sync with TWS!")
    elif pct_diff > 2:
        print(f"  ⚡ Minor discrepancy — within acceptable range")
    else:
        print(f"  ✅ DB and TWS are in sync")

    print(f"\n💼 ACCOUNT")
    print(f"  Net Liquidation  : ${tws.get('NetLiquidation',0):>12,.2f}")
    print(f"  Cash             : ${tws.get('TotalCashValue',0):>12,.2f}")
    print(f"  Unrealized P&L   : ${tws.get('UnrealizedPnL',0):>12,.2f}")
    print(f"  Gross Positions  : ${tws.get('GrossPositionValue',0):>12,.2f}")

    print(f"\n📂 TRADES DB")
    print(f"  Total trades     : {db['trade_count']}")
    print(f"  Today's trades   : {db['today_count']}")
    print(f"  Today's DB P&L   : ${db['today_pnl']:>12,.2f}")
    print(f"  Unclosed in DB   : {db['open_in_db']}")

    print(f"\n📌 OPEN POSITIONS (TWS)")
    if positions:
        for p in positions:
            print(f"  {p['symbol']:>8} | qty={p['qty']:>8} | avg={p['avg_cost']:.2f} | unPnL=${p.get('unrealized_pnl',0):.2f}")
    else:
        print("  None")

    print(f"\n🔄 TODAY'S FILLS (TWS)")
    today_fills = [f for f in fills if f["time"].startswith(date.today().isoformat())]
    if today_fills:
        for f in today_fills:
            pnl_str = f"  pnl=${f['pnl']:.2f}" if f["pnl"] is not None else ""
            print(f"  {f['symbol']:>8} {f['action']} {f['qty']} @ {f['price']}{pnl_str}  [{f['time']}]")
    else:
        print("  No fills today yet")

    # Save report
    report = {
        "timestamp": datetime.now().isoformat(),
        "tws": tws,
        "db": db,
        "discrepancy": discrepancy,
        "discrepancy_pct": pct_diff,
        "positions": positions,
        "today_fills": today_fills,
        "status": "OUT_OF_SYNC" if pct_diff > 10 else "OK"
    }
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n💾 Report saved to {REPORT_PATH}")
    print(f"{'='*50}\n")

    return report


if __name__ == "__main__":
    run_check()
