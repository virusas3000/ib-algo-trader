#!/bin/bash
# Watchdog: auto-restart trader.py if it's not running
LOG="/Users/vickhung/Desktop/ib_algo_trader/watchdog.log"
TRADER_DIR="/Users/vickhung/Desktop/ib_algo_trader"

if ! pgrep -f "python.*trader.py" > /dev/null 2>&1; then
    echo "$(date): trader.py not running — restarting..." >> "$LOG"
    cd "$TRADER_DIR"
    nohup python3 trader.py >> algo.log 2>&1 &
    echo "$(date): Restarted with PID $!" >> "$LOG"
else
    echo "$(date): trader.py is alive ✓" >> "$LOG"
fi
