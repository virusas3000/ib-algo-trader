
import pandas as pd
import os
from datetime import datetime

def calculate_win_loss_stats():
    """
    Reads the round trip trades from CSV, calculates win/loss stats for the current day,
    and prints a summary report.
    """
    file_path = os.path.expanduser('~/Desktop/ib_algo_trader/round_trips.csv')

    if not os.path.exists(file_path):
        print("Trading log 'round_trips.csv' not found.")
        return

    try:
        df = pd.read_csv(file_path)
        if df.empty:
            print("No trades recorded in 'round_trips.csv' yet.")
            return

        # Ensure 'exit_time' is a datetime object to filter for today
        df['exit_time'] = pd.to_datetime(df['exit_time'])

        # Filter for trades that occurred today (based on the system's date)
        today = datetime.now().date()
        todays_trades = df[df['exit_time'].dt.date == today]

        if todays_trades.empty:
            print(f"No trades recorded for today ({today.strftime('%Y-%m-%d')}).")
            return

        total_trades = len(todays_trades)
        wins = todays_trades[todays_trades['pnl'] > 0]
        losses = todays_trades[todays_trades['pnl'] <= 0]
        num_wins = len(wins)
        num_losses = len(losses)

        win_rate = (num_wins / total_trades) * 100 if total_trades > 0 else 0
        
        total_pnl = todays_trades['pnl'].sum()
        
        # Build the report string
        report = [
            f"**Daily Trading Report for {today.strftime('%A, %Y-%m-%d')}**",
            "─" * 30,
            f"Total Trades: {total_trades}",
            f"Wins:         {num_wins}",
            f"Losses:       {num_losses}",
            f"**Win Rate:     {win_rate:.2f}%**",
            f"**Total P&L:    ${total_pnl:,.2f}**",
        ]
        
        print('\\n'.join(report))

    except Exception as e:
        print(f"An error occurred while generating the report: {e}")

if __name__ == "__main__":
    calculate_win_loss_stats()
