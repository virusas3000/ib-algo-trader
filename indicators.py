"""
Indicators — technical analysis helpers
"""
from __future__ import annotations
import pandas as pd
import numpy as np


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP — resets each day."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"]
    cum_tpvol = (tp * vol).cumsum()
    cum_vol = vol.cumsum()
    return cum_tpvol / cum_vol.replace(0, np.nan)


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def relative_volume(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Current bar volume relative to N-bar average."""
    avg = df["volume"].rolling(period).mean()
    return df["volume"] / avg.replace(0, np.nan)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all indicators to a OHLCV dataframe."""
    df = df.copy()
    df["ema9"]    = ema(df["close"], 9)
    df["ema20"]   = ema(df["close"], 20)
    df["ema50"]   = ema(df["close"], 50)
    df["sma20"]   = sma(df["close"], 20)
    df["rsi14"]   = rsi(df["close"], 14)
    df["atr14"]   = atr(df, 14)
    df["vwap"]    = vwap(df)
    df["rvol"]    = relative_volume(df, 20)
    # Fill NaN with safe defaults
    df["rsi14"]   = df["rsi14"].fillna(50)
    df["atr14"]   = df["atr14"].fillna(df["close"] * 0.01)
    df["vwap"]    = df["vwap"].fillna(df["close"])
    df["rvol"]    = df["rvol"].fillna(1.0)
    return df
