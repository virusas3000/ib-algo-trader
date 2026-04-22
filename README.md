# IB Algo Trader — ORB Momentum Breakout

Automated day trading system that connects to Interactive Brokers via `ib_insync`.
**Paper trading only** by default.

## Strategy

**Opening Range Breakout (ORB) + Momentum Confirmation**

1. First 15 minutes (9:30–9:45 ET): Records the high/low of the opening range
2. After 9:45: Watches for price to break above ORB high (long) or below ORB low (short)
3. Requires confirmation: Volume surge (>1.5x avg) + price above/below VWAP + EMA/RSI confirmation
4. Bracket orders: Entry + Stop Loss + Take Profit submitted as one group
5. Partial exits: 50% at Target 1 (1.5x ATR), 25% at Target 2 (3x ATR), trail rest on EMA9
6. Risk: 0.5% of account per trade, max 2% daily loss (hard kill switch)

## Setup

### 1. Install IB TWS or IB Gateway

Download from: https://www.interactivebrokers.com/en/trading/tws.php

- **TWS (recommended for development)**: Full GUI, see orders visually
- **IB Gateway (for production)**: Lightweight, API-only

### 2. Get a Paper Trading Account

- Sign up at https://www.interactivebrokers.com
- Or get a free trial: https://www.interactivebrokers.com/en/trading/free-trial.php
- Paper account is auto-created with your live account
- At login, select "Paper Trading" mode

### 3. Enable API in TWS/Gateway

Open TWS → Edit → Global Configuration → API → Settings:

- ✅ Enable ActiveX and Socket Clients
- ✅ Socket port: **7497** (paper TWS) or **4002** (paper Gateway)
- ❌ Uncheck "Read-Only API" (so we can place orders)
- ✅ Allow connections from localhost only

### 4. Install Python Dependencies

```bash
cd ~/Desktop/ib_algo_trader
pip3 install -r requirements.txt
```

### 5. Run

```bash
# Dry run (signals only, no orders):
python3 trader.py --dry-run

# Live paper trading:
python3 trader.py

# Use IB Gateway instead of TWS:
python3 trader.py --port 4002
```

## Files

```
ib_algo_trader/
├── trader.py          # Main engine — connects to IB, runs the loop
├── strategy.py        # Strategy logic — ORB levels, entry/exit rules
├── indicators.py      # Technical indicators (EMA, RSI, ATR, VWAP, MACD)
├── config.py          # All tunable parameters in one place
├── requirements.txt   # Python dependencies
├── trades.log         # Runtime log (created on first run)
└── trade_history.csv  # Every trade logged here (created on first run)
```

## Configuration (config.py)

| Parameter | Default | Description |
|-----------|---------|-------------|
| IB_PORT | 7497 | TWS paper=7497, Gateway paper=4002 |
| RISK_PER_TRADE | 0.5% | Account risk per trade |
| MAX_DAILY_LOSS_PCT | 2% | Kill switch — stops all trading |
| MAX_POSITIONS | 3 | Max concurrent open positions |
| MAX_TRADES_PER_DAY | 10 | Prevents overtrading |
| ORB_MINUTES | 15 | Opening range period |
| RVOL_THRESHOLD | 1.5 | Min relative volume for entry |
| STOP_ATR_MULT | 1.5 | Stop loss = 1.5x ATR |
| TARGET1_ATR_MULT | 1.5 | First target = 1.5x ATR (sell 50%) |
| TARGET2_ATR_MULT | 3.0 | Second target = 3x ATR (sell 25%) |

## Watchlist

Default: SPY, QQQ, IWM, AAPL, TSLA, NVDA, AMD, META, AMZN

Edit `DEFAULT_WATCHLIST` in config.py to change.

## Connection Ports Reference

| Setup | Port |
|-------|------|
| TWS Paper Trading | 7497 |
| TWS Live Trading | 7496 |
| IB Gateway Paper | 4002 |
| IB Gateway Live | 4001 |

## Safety Features

- **Paper trading by default** (port 7497)
- **Max daily loss kill switch** — auto-flattens all positions
- **Consecutive loss pause** — 30 min cooldown after 3 losses
- **EOD force close** — all positions closed by 3:50 PM ET
- **Dry-run mode** — test signals without placing any orders
- **Bracket orders** — stop loss always attached to every entry

## macOS Notes (Apple Silicon)

- TWS runs via Rosetta 2 automatically — no action needed
- If macOS blocks TWS: System Settings → Privacy & Security → Open Anyway
- Check firewall allows TWS/Gateway network access

## ⚠️ Disclaimer

This is for **educational and paper trading purposes only**. Algorithmic trading involves substantial risk. Never trade with money you can't afford to lose. Always test extensively on paper before considering live trading.
