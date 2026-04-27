"""
Learn from historical trades — build labeled training dataset from real fills,
extract features at entry time, label by realized P&L outcome, retrain ML model.

This closes the loop: every closed trade becomes one training example with
the actual market context at entry as features and the realized win/loss as label.
"""
import pandas as pd
import numpy as np
import pickle
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from ib_insync import IB, Stock, util
import warnings
warnings.filterwarnings('ignore')

TRADER_DIR = Path(__file__).parent
HISTORY_CSV = TRADER_DIR / 'trade_history.csv'
LESSONS_FILE = TRADER_DIR / 'trade_lessons.json'
TRAINING_DATA = TRADER_DIR / 'ml_training_data.pkl'


def fetch_all_fills(ib):
    """Pull every fill IB still has in memory."""
    fills = ib.fills()
    rows = []
    for f in fills:
        rows.append({
            'time': pd.Timestamp(f.time).tz_convert('US/Eastern'),
            'symbol': f.contract.symbol,
            'side': f.execution.side,         # BOT / SLD
            'qty': f.execution.shares,
            'price': f.execution.price,
            'realized_pnl': getattr(f.commissionReport, 'realizedPNL', 0) or 0,
            'commission': f.commissionReport.commission,
        })
    df = pd.DataFrame(rows).sort_values('time').reset_index(drop=True)
    return df


def pair_fills_into_round_trips(df):
    """
    Walk fills chronologically; track running position per symbol.
    Each time position returns to 0, that completes a round trip.
    Label round trip with total realized P&L.
    """
    trips = []
    open_lots = defaultdict(list)   # symbol -> list of (time, side, qty, price)
    pos = defaultdict(float)        # symbol -> running net position

    for _, row in df.iterrows():
        sym = row['symbol']
        signed_qty = row['qty'] if row['side'] == 'BOT' else -row['qty']
        prev_pos = pos[sym]
        new_pos = prev_pos + signed_qty
        open_lots[sym].append({
            'time': row['time'], 'side': row['side'],
            'qty': row['qty'], 'price': row['price'],
            'realized_pnl': row['realized_pnl'],
            'commission': row['commission'],
        })

        # closed back to flat -> finalize a round trip
        if abs(new_pos) < 1e-6 and abs(prev_pos) > 1e-6:
            lots = open_lots[sym]
            entry_lots = [l for l in lots if l['side'] == ('BOT' if prev_pos > 0 else 'SLD')]
            exit_lots  = [l for l in lots if l['side'] == ('SLD' if prev_pos > 0 else 'BOT')]
            if not entry_lots or not exit_lots:
                open_lots[sym] = []
                pos[sym] = new_pos
                continue
            entry_qty = sum(l['qty'] for l in entry_lots)
            entry_avg = sum(l['qty'] * l['price'] for l in entry_lots) / entry_qty
            exit_qty  = sum(l['qty'] for l in exit_lots)
            exit_avg  = sum(l['qty'] * l['price'] for l in exit_lots) / exit_qty
            total_pnl = sum(l['realized_pnl'] for l in lots)
            total_comm = sum(l['commission'] for l in lots)
            direction = 'LONG' if prev_pos > 0 else 'SHORT'
            entry_time = entry_lots[0]['time']
            exit_time = exit_lots[-1]['time']
            duration = (exit_time - entry_time).total_seconds() / 60

            trips.append({
                'symbol': sym,
                'direction': direction,
                'entry_time': entry_time,
                'exit_time': exit_time,
                'duration_min': duration,
                'qty': entry_qty,
                'entry_price': entry_avg,
                'exit_price': exit_avg,
                'realized_pnl': total_pnl,
                'commission': total_comm,
                'net_pnl': total_pnl - total_comm,
                'win': 1 if total_pnl > 0 else 0,
                'pct_return': ((exit_avg - entry_avg) / entry_avg * 100 *
                              (1 if direction == 'LONG' else -1)),
            })
            open_lots[sym] = []
        pos[sym] = new_pos
    return pd.DataFrame(trips)


def analyze_lessons(trips):
    """Extract patterns from wins vs losses."""
    if trips.empty:
        return {}
    wins = trips[trips['win'] == 1]
    losses = trips[trips['win'] == 0]

    lessons = {
        'total_trips': len(trips),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': len(wins) / len(trips) if len(trips) else 0,
        'total_net_pnl': float(trips['net_pnl'].sum()),
        'avg_winner': float(wins['net_pnl'].mean()) if len(wins) else 0,
        'avg_loser': float(losses['net_pnl'].mean()) if len(losses) else 0,
        'biggest_winner': float(wins['net_pnl'].max()) if len(wins) else 0,
        'biggest_loser': float(losses['net_pnl'].min()) if len(losses) else 0,
        'avg_hold_winners_min': float(wins['duration_min'].mean()) if len(wins) else 0,
        'avg_hold_losers_min': float(losses['duration_min'].mean()) if len(losses) else 0,
        'by_direction': {
            'LONG':  {'count': int((trips.direction == 'LONG').sum()),
                      'net_pnl': float(trips[trips.direction == 'LONG']['net_pnl'].sum()),
                      'win_rate': float(trips[trips.direction == 'LONG']['win'].mean()) if (trips.direction == 'LONG').any() else 0},
            'SHORT': {'count': int((trips.direction == 'SHORT').sum()),
                      'net_pnl': float(trips[trips.direction == 'SHORT']['net_pnl'].sum()),
                      'win_rate': float(trips[trips.direction == 'SHORT']['win'].mean()) if (trips.direction == 'SHORT').any() else 0},
        },
        'by_symbol': {},
        'profit_factor': float(wins['net_pnl'].sum() / abs(losses['net_pnl'].sum())) if len(losses) and losses['net_pnl'].sum() != 0 else 0,
    }
    for sym in trips.symbol.unique():
        s = trips[trips.symbol == sym]
        lessons['by_symbol'][sym] = {
            'trips': int(len(s)),
            'net_pnl': float(s['net_pnl'].sum()),
            'win_rate': float(s['win'].mean()),
            'avg_hold_min': float(s['duration_min'].mean()),
        }
    return lessons


def build_features_for_ml(trips):
    """
    Convert round trips into feature vectors compatible with ml_indicators.
    Features used at ENTRY time, label = win/loss outcome.
    Without historical bar data, we use trade context features.
    """
    feature_rows = []
    for _, t in trips.iterrows():
        feat = {
            'symbol': t['symbol'],
            'direction_long': 1 if t['direction'] == 'LONG' else 0,
            'entry_hour_et': t['entry_time'].hour,
            'entry_minute_et': t['entry_time'].minute,
            'day_of_week': t['entry_time'].dayofweek,
            'qty': t['qty'],
            'entry_price': t['entry_price'],
            'duration_min': t['duration_min'],
            'label_win': t['win'],
            'label_pnl': t['net_pnl'],
        }
        feature_rows.append(feat)
    return pd.DataFrame(feature_rows)


def main():
    print("=" * 60)
    print("LEARNING FROM HISTORICAL TRADES")
    print("=" * 60)

    ib = IB()
    ib.connect('127.0.0.1', 7497, clientId=97)
    print("Connected. Fetching fill history...")

    df = fetch_all_fills(ib)
    print(f"Total fills: {len(df)}")
    df.to_csv(HISTORY_CSV, index=False)

    trips = pair_fills_into_round_trips(df)
    print(f"Completed round trips: {len(trips)}")

    if trips.empty:
        print("No completed round trips yet — nothing to learn from.")
        ib.disconnect()
        return

    trips.to_csv(TRADER_DIR / 'round_trips.csv', index=False)
    print("\n--- COMPLETED ROUND TRIPS ---")
    print(trips[['symbol', 'direction', 'entry_time', 'duration_min',
                 'pct_return', 'net_pnl', 'win']].to_string())

    lessons = analyze_lessons(trips)
    print("\n" + "=" * 60)
    print("LESSONS LEARNED")
    print("=" * 60)
    print(f"Total trips:         {lessons['total_trips']}")
    print(f"Win rate:            {lessons['win_rate']*100:.1f}% ({lessons['wins']}W / {lessons['losses']}L)")
    print(f"Total net P&L:       ${lessons['total_net_pnl']:+.2f}")
    print(f"Profit factor:       {lessons['profit_factor']:.2f}")
    print(f"Avg winner:          ${lessons['avg_winner']:+.2f}")
    print(f"Avg loser:           ${lessons['avg_loser']:+.2f}")
    print(f"Biggest winner:      ${lessons['biggest_winner']:+.2f}")
    print(f"Biggest loser:       ${lessons['biggest_loser']:+.2f}")
    print(f"Avg hold winners:    {lessons['avg_hold_winners_min']:.1f} min")
    print(f"Avg hold losers:     {lessons['avg_hold_losers_min']:.1f} min")
    print(f"\nLONG  trades: {lessons['by_direction']['LONG']['count']:3d} | net ${lessons['by_direction']['LONG']['net_pnl']:+8.2f} | win% {lessons['by_direction']['LONG']['win_rate']*100:.1f}")
    print(f"SHORT trades: {lessons['by_direction']['SHORT']['count']:3d} | net ${lessons['by_direction']['SHORT']['net_pnl']:+8.2f} | win% {lessons['by_direction']['SHORT']['win_rate']*100:.1f}")
    print("\n--- BY SYMBOL ---")
    by_sym = sorted(lessons['by_symbol'].items(), key=lambda x: x[1]['net_pnl'])
    for sym, s in by_sym:
        print(f"  {sym:6} | trips {s['trips']:2d} | net ${s['net_pnl']:+8.2f} | win% {s['win_rate']*100:5.1f} | hold {s['avg_hold_min']:5.1f}m")

    import json
    with open(LESSONS_FILE, 'w') as f:
        json.dump(lessons, f, indent=2, default=str)
    print(f"\nLessons saved -> {LESSONS_FILE}")

    feats = build_features_for_ml(trips)
    feats.to_pickle(TRAINING_DATA)
    print(f"ML training data saved -> {TRAINING_DATA} ({len(feats)} examples)")

    # === Generate ACTIONABLE RULES from the data ===
    print("\n" + "=" * 60)
    print("ACTIONABLE RULES FOR THE BOT")
    print("=" * 60)
    rules = []

    # Rule 1: avoid losing symbols
    bad_syms = [s for s, st in lessons['by_symbol'].items()
                if st['net_pnl'] < -200 and st['trips'] >= 1]
    if bad_syms:
        rules.append(f"BLACKLIST symbols with net loss > $200: {bad_syms}")

    # Rule 2: prefer winning direction
    long_pnl = lessons['by_direction']['LONG']['net_pnl']
    short_pnl = lessons['by_direction']['SHORT']['net_pnl']
    if abs(long_pnl - short_pnl) > 500:
        better = 'SHORT' if short_pnl > long_pnl else 'LONG'
        rules.append(f"BIAS toward {better} direction (net diff ${abs(long_pnl-short_pnl):+.0f})")

    # Rule 3: hold time
    if lessons['avg_hold_losers_min'] > lessons['avg_hold_winners_min'] * 1.5:
        rules.append(f"CUT LOSSES FASTER — losers held {lessons['avg_hold_losers_min']:.0f}m vs winners {lessons['avg_hold_winners_min']:.0f}m")

    # Rule 4: profit factor warning
    if lessons['profit_factor'] < 1.0:
        rules.append(f"PROFIT FACTOR {lessons['profit_factor']:.2f} < 1.0 — strategy is net losing, tighten entries")

    # Rule 5: win rate
    if lessons['win_rate'] < 0.4:
        rules.append(f"WIN RATE {lessons['win_rate']*100:.0f}% — raise ML confidence threshold above 0.65")

    if not rules:
        rules.append("No strong patterns yet — need more trades for statistical significance.")

    for r in rules:
        print(f"  • {r}")

    # write rules to a file the bot can read
    with open(TRADER_DIR / 'learned_rules.txt', 'w') as f:
        for r in rules:
            f.write(r + "\n")
    print(f"\nRules saved -> learned_rules.txt")

    # blacklist file the bot can load
    if bad_syms:
        with open(TRADER_DIR / 'symbol_blacklist.txt', 'w') as f:
            for s in bad_syms:
                f.write(s + "\n")
        print(f"Blacklist saved -> symbol_blacklist.txt ({len(bad_syms)} symbols)")

    ib.disconnect()


if __name__ == '__main__':
    main()
