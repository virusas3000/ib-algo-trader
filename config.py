"""
IB Algo Trader — Configuration
Multi-strategy day trading engine
"""

# ─── CONNECTION ──────────────────────────────────────────
IB_HOST = "127.0.0.1"
IB_PORT = 7497          # 7497 = TWS Paper | 4002 = Gateway Paper | 7496 = TWS Live
CLIENT_ID = 15

# ─── ACCOUNT & RISK ─────────────────────────────────────
RISK_PER_TRADE = 0.005      # 0.5% risk per trade (reduced after losses)
MAX_DAILY_LOSS_PCT = 0.02   # 2% max daily loss → hard stop
MAX_POSITIONS = 3           # max 3 concurrent (avoid overexposure)
MAX_TRADES_PER_DAY = 10
MAX_CONSECUTIVE_LOSSES = 2  # disable strategy after just 2 losses
MAX_POSITION_PCT = 0.10     # max 10% of account in one trade
MIN_RISK_REWARD = 2.0       # require 2:1 R:R minimum

# ─── MARKET HOURS (ET) ───────────────────────────────────
MARKET_OPEN_HOUR = 9
# ─── DAY TRADING ENFORCEMENT ──────────────────────────────
DAY_TRADE_ONLY = True           # STRICT day trading only - no overnight positions
FORCE_CLOSE_HOUR = 15           # Force close all positions by this hour (3 PM ET)
FORCE_CLOSE_MIN = 45            # Force close all positions by this minute (3:45 PM ET)
NO_NEW_POSITIONS_HOUR = 15      # Stop opening new positions after this hour
NO_NEW_POSITIONS_MIN = 30       # Stop opening new positions after this minute (3:30 PM ET)
FINAL_WARNING_HOUR = 15         # Send warning about upcoming close
FINAL_WARNING_MIN = 40          # Send warning 5 minutes before force close (3:40 PM ET)

# ─── MARKET HOURS ─────────────────────────────────────────
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MIN = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN = 0

# ─── ORB STRATEGY ────────────────────────────────────────
ORB_PERIODS = [5, 15, 30]   # minutes
ORB_VOLUME_MULT = 1.5       # volume must be 1.5x 20-day avg
ORB_ATR_STOP = 1.5          # stop = 1.5x ATR
ORB_ATR_T1 = 2.0            # target 1 = 2x ATR
ORB_ATR_T2 = 3.0            # target 2 = 3x ATR
ORB_END_HOUR = 10           # ORB window ends at 10:30 ET
ORB_END_MIN = 30

# ─── VWAP MEAN REVERSION ─────────────────────────────────
VWAP_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "AMZN"]
VWAP_DEVIATION_PCT = 1.5    # tightened to 1.5% for more opportunities
VWAP_RSI_OVERSOLD = 40      # more sensitive
VWAP_RSI_OVERBOUGHT = 60    # more sensitive

# ─── GAP FILL ────────────────────────────────────────────
GAP_MIN_PCT = 2.0           # only trade gaps >2% (avoids weak setups)
GAP_STOP_PCT = 0.5          # wider stop 0.5% beyond gap level
GAP_MAX_SPY_TREND = 0.8     # don't fade gap-up if SPY trending up >0.8%
GAP_REQUIRE_REVERSAL = True # wait for first 5min candle to show reversal

# ─── POWER HOUR ──────────────────────────────────────────
POWER_HOUR_START = 15       # 3:00 PM ET
POWER_HOUR_START_MIN = 0
POWER_HOUR_VOLUME_MULT = 1.3
POWER_HOUR_TARGET_PCT = 0.5
POWER_HOUR_STOP_PCT = 0.25

# ─── VIX REGIME ──────────────────────────────────────────
VIX_LOW = 15                # below = favor mean reversion
VIX_HIGH = 25               # above = reduce size, ORB only
VIX_HIGH_SIZE_MULT = 0.5    # trade half size when VIX > 25

# ─── WATCHLIST ───────────────────────────────────────────
DEFAULT_WATCHLIST = [
    # Major ETFs & Indices
    "SPY", "QQQ", "IWM", "DIA", "VTI", "EFA", "EEM", "GLD", "SLV", "TLT",
    
    # Mega Cap Tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "NFLX", "ADBE", "CRM",
    "ORCL", "INTC", "CSCO", "IBM", "PYPL", "UBER", "SHOP", "SQ",
    
    # Semiconductors
    "AMD", "QCOM", "AVGO", "MU", "AMAT", "LRCX", "MRVL", "ADI",
    
    # Financial Services
    "JPM", "BAC", "WFC", "GS", "MS", "C", "USB", "PNC", "COF",
    "V", "MA", "AXP", "BRK.B", "BLK", "SCHW", "SPGI", "ICE", "CME",
    
    # Healthcare & Biotech
    "JNJ", "UNH", "PFE", "ABBV", "TMO", "ABT", "MRK", "LLY", "DHR", "BMY",
    "AMGN", "GILD", "BIIB", "REGN", "VRTX", "MRNA", "ZTS", "CVS",
    
    # Consumer & Retail
    "WMT", "HD", "PG", "KO", "PEP", "MCD", "SBUX", "NKE", "TGT", "COST",
    "LOW", "DIS", "CMCSA", "VZ", "T", "PM", "MO", "CL",
    
    # Energy & Materials
    "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "VLO", "OXY", "HAL", "BKR",
    
    # Industrial & Defense
    "BA", "CAT", "GE", "MMM", "HON", "UPS", "FDX", "LMT", "RTX", "NOC"
]

# ─── MISC ────────────────────────────────────────────────
BAR_SIZE = "5 mins"
TRADE_LOG_CSV = "trade_history.csv"

# ─── DIRECTIONAL BIAS ────────────────────────────────────
LONG_ONLY = True            # only take LONG trades today

# ─── TELEGRAM NOTIFICATIONS ──────────────────────────────
TELEGRAM_BOT_TOKEN="8656598981:AAEdqezTQoY2RgJ-mw0j-sZzIZ_0hwU8Ze0"  # Your bot token
TELEGRAM_CHAT_ID = "304395405"  # Your chat ID

# ─── SENTIMENT ANALYSIS ───────────────────────────────────
SENTIMENT_ENABLED = True                # Enable social media sentiment analysis
SENTIMENT_UPDATE_INTERVAL = 300         # Update every 5 minutes
SENTIMENT_MIN_MENTIONS = 5              # Minimum mentions to consider a stock
SENTIMENT_MIN_CONFIDENCE = 0.3          # Minimum confidence for sentiment signals
SENTIMENT_WEIGHT = 0.25                 # Sentiment influence on decision (25%)
SENTIMENT_POSITION_MULTIPLIER = 0.5     # Max 50% position size increase from sentiment
MAX_SENTIMENT_POSITIONS = 2             # Max positions from pure sentiment plays
