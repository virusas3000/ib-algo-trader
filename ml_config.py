"""
ML Trading Bot — Configuration
Machine Learning powered day trading engine
"""

# ─── CONNECTION ──────────────────────────────────────────
IB_HOST = "127.0.0.1"
IB_PORT = 7497          # 7497 = TWS Paper | 4002 = Gateway Paper | 7496 = TWS Live
CLIENT_ID = 16          # Different from algo bot to avoid conflicts

# ─── ACCOUNT & RISK ─────────────────────────────────────
RISK_PER_TRADE      = 0.005     # 0.5% risk per trade
MAX_DAILY_LOSS_PCT  = 0.02      # 2% max daily loss → hard stop
MAX_POSITIONS       = 3
MAX_TRADES_PER_DAY  = 10
MAX_POSITION_PCT    = 0.10      # max 10% of account per trade
MAX_CONSECUTIVE_LOSSES = 2

# ─── DAY TRADING ENFORCEMENT ─────────────────────────────
DAY_TRADE_ONLY         = True
FORCE_CLOSE_HOUR       = 15
FORCE_CLOSE_MIN        = 45
NO_NEW_POSITIONS_HOUR  = 15
NO_NEW_POSITIONS_MIN   = 30
FINAL_WARNING_HOUR     = 15
FINAL_WARNING_MIN      = 40

# ─── MARKET HOURS (ET) ───────────────────────────────────
MARKET_OPEN_HOUR  = 9
MARKET_OPEN_MIN   = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN  = 0

# ─── ML MODEL SETTINGS ───────────────────────────────────
ML_MODEL_PATH        = "ml_model.pkl"       # Saved trained model
ML_SCALER_PATH       = "ml_scaler.pkl"      # Saved feature scaler
ML_LABEL_PATH        = "ml_labels.pkl"      # Saved label encoder
ML_MIN_CONFIDENCE    = 0.60                 # Min probability to act (60%)
ML_RETRAIN_DAYS      = 7                    # Retrain model every 7 days
ML_TRAIN_LOOKBACK    = 90                   # Days of history for training
ML_FEATURES = [                             # Feature columns fed to model
    "rsi14", "ema9", "ema20", "ema50",
    "vwap_dev_pct", "rvol", "atr14",
    "bb_width", "macd", "macd_signal",
    "price_vs_open_pct", "hour", "minute",
    "sma20_slope", "volume_spike",
    "orb_breakout", "gap_pct",
    "rsi_divergence", "candle_body_pct",
]
ML_LABEL_COLUMN      = "outcome"            # BUY / SELL / HOLD
ML_ENSEMBLE          = True                 # Use ensemble of RF + XGBoost + LR

# ─── ORB SETTINGS ────────────────────────────────────────
ORB_VOLUME_MULT = 1.5
ORB_ATR_STOP    = 1.5
ORB_ATR_T1      = 2.0
ORB_ATR_T2      = 3.0

# ─── VIX REGIME ──────────────────────────────────────────
VIX_LOW           = 15
VIX_HIGH          = 25
VIX_HIGH_SIZE_MULT = 0.5

# ─── DIRECTIONAL BIAS ────────────────────────────────────
LONG_ONLY = True

# ─── WATCHLIST ───────────────────────────────────────────
DEFAULT_WATCHLIST = [
    "SPY", "QQQ", "IWM", "DIA", "VTI",
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "NFLX",
    "AMD", "QCOM", "AVGO", "MU",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA",
    "JNJ", "UNH", "PFE", "ABBV", "MRNA", "BIIB",
    "WMT", "HD", "PG", "KO", "MCD", "COST", "DIS",
    "XOM", "CVX", "COP", "MPC", "VLO", "OXY",
    "BA", "CAT", "GE", "HON", "UPS", "LMT",
]

# ─── MISC ────────────────────────────────────────────────
BAR_SIZE      = "5 mins"
TRADE_LOG_CSV = "ml_trade_history.csv"

# ─── TELEGRAM NOTIFICATIONS ──────────────────────────────
TELEGRAM_BOT_TOKEN = "8656598981:AAEdqezTQoY2RgJ-mw0j-sZzIZ_0hwU8Ze0"
TELEGRAM_CHAT_ID   = "304395405"
