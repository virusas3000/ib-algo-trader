"""
Indicators — technical analysis helpers (extended for ML feature engineering)
"""
from __future__ import annotations
import pandas as pd
import numpy as np


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    tp  = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"]
    return (tp * vol).cumsum() / vol.cumsum().replace(0, np.nan)


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def relative_volume(df: pd.DataFrame, period: int = 20) -> pd.Series:
    avg = df["volume"].rolling(period).mean()
    return df["volume"] / avg.replace(0, np.nan)


def bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0):
    mid   = series.rolling(period).mean()
    std   = series.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid.replace(0, np.nan)
    return upper, mid, lower, width


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    fast_ema   = series.ewm(span=fast,   adjust=False).mean()
    slow_ema   = series.ewm(span=slow,   adjust=False).mean()
    macd_line  = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all indicators + ML feature columns to an OHLCV dataframe."""
    df = df.copy()

    # ── Core indicators ──────────────────────────────────
    df["ema9"]    = ema(df["close"], 9)
    df["ema20"]   = ema(df["close"], 20)
    df["ema50"]   = ema(df["close"], 50)
    df["sma20"]   = sma(df["close"], 20)
    df["rsi14"]   = rsi(df["close"], 14)
    df["atr14"]   = atr(df, 14)
    df["vwap"]    = vwap(df)
    df["rvol"]    = relative_volume(df, 20)

    # ── Bollinger Bands ───────────────────────────────────
    bb_upper, bb_mid, bb_lower, bb_width = bollinger_bands(df["close"])
    df["bb_upper"] = bb_upper
    df["bb_mid"]   = bb_mid
    df["bb_lower"] = bb_lower
    df["bb_width"] = bb_width

    # ── MACD ─────────────────────────────────────────────
    macd_line, signal_line, histogram = macd(df["close"])
    df["macd"]         = macd_line
    df["macd_signal"]  = signal_line
    df["macd_hist"]    = histogram

    # ── ML Feature engineering ────────────────────────────
    # VWAP deviation %
    df["vwap_dev_pct"] = (df["close"] - df["vwap"]) / df["vwap"].replace(0, np.nan) * 100

    # Price vs open of day %
    df["price_vs_open_pct"] = (df["close"] - df["open"]) / df["open"].replace(0, np.nan) * 100

    # SMA20 slope (momentum proxy)
    df["sma20_slope"] = df["sma20"].diff(3) / df["sma20"].shift(3).replace(0, np.nan) * 100

    # Volume spike (current vs 5-bar rolling avg)
    df["volume_spike"] = df["volume"] / df["volume"].rolling(5).mean().replace(0, np.nan)

    # ORB breakout flag (set externally per symbol, default 0)
    if "orb_breakout" not in df.columns:
        df["orb_breakout"] = 0

    # Gap % (set externally per symbol, default 0)
    if "gap_pct" not in df.columns:
        df["gap_pct"] = 0.0

    # RSI divergence (price going up but RSI going down)
    price_chg = df["close"].diff(3)
    rsi_chg   = df["rsi14"].diff(3)
    df["rsi_divergence"] = ((price_chg > 0) & (rsi_chg < 0)).astype(int) - \
                           ((price_chg < 0) & (rsi_chg > 0)).astype(int)

    # Candle body % of range
    body  = (df["close"] - df["open"]).abs()
    range_ = (df["high"] - df["low"]).replace(0, np.nan)
    df["candle_body_pct"] = body / range_

    # Time features (will be filled by trader at runtime)
    if "hour"   not in df.columns: df["hour"]   = 0
    if "minute" not in df.columns: df["minute"] = 0

    # ── Fill NaN with safe defaults ───────────────────────
    df["rsi14"]         = df["rsi14"].fillna(50)
    df["atr14"]         = df["atr14"].fillna(df["close"] * 0.01)
    df["vwap"]          = df["vwap"].fillna(df["close"])
    df["rvol"]          = df["rvol"].fillna(1.0)
    df["bb_width"]      = df["bb_width"].fillna(0.0)
    df["macd"]          = df["macd"].fillna(0.0)
    df["macd_signal"]   = df["macd_signal"].fillna(0.0)
    df["vwap_dev_pct"]  = df["vwap_dev_pct"].fillna(0.0)
    df["sma20_slope"]   = df["sma20_slope"].fillna(0.0)
    df["volume_spike"]  = df["volume_spike"].fillna(1.0)
    df["candle_body_pct"] = df["candle_body_pct"].fillna(0.5)
    df["rsi_divergence"]  = df["rsi_divergence"].fillna(0)

    return df
