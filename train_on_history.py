"""
Train the ML model on 10 YEARS of real historical stock data.

Pipeline:
  1. Download daily OHLCV for entire watchlist from yfinance (10y, free)
  2. Compute identical feature set the live bot uses (ml_indicators.compute_indicators)
  3. Label each bar by FORWARD RETURN over next 5 bars:
        > +1.5%  -> BUY
        < -1.5%  -> SELL
        else     -> HOLD
  4. Train ensemble (RF + XGBoost + LogReg) via existing MLPredictor
  5. Save model files atomically — live bot picks them up on next reload

Run:  python3 train_on_history.py
"""
import sys, time, warnings, traceback
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf

warnings.filterwarnings('ignore')
sys.path.insert(0, str(Path(__file__).parent))

from ml_indicators import compute_indicators
from ml_model import MLSignalModel as MLPredictor
import ml_config as mlc
import config as cfg

LOOKBACK_YEARS = 10
FORWARD_BARS   = 5      # predict 5-day forward return
BUY_THRESH     = 1.5    # % forward return to label BUY
SELL_THRESH    = -1.5   # % forward return to label SELL
INTERVAL       = "1d"   # daily bars (yfinance free tier supports 10y daily)

OUT_DIR        = Path(__file__).parent
MODEL_PATH     = OUT_DIR / 'ml_model.pkl'
SCALER_PATH    = OUT_DIR / 'ml_scaler.pkl'
LABELS_PATH    = OUT_DIR / 'ml_labels.pkl'
DATASET_CACHE  = OUT_DIR / 'historical_dataset.parquet'


def download_symbol(symbol: str, period: str = f"{LOOKBACK_YEARS}y") -> pd.DataFrame:
    """Download OHLCV from yfinance and normalize column names."""
    try:
        df = yf.download(symbol, period=period, interval=INTERVAL,
                         auto_adjust=True, progress=False, threads=False)
        if df is None or df.empty:
            return pd.DataFrame()
        # yfinance sometimes returns multi-index columns when threading
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.rename(columns={'Open': 'open', 'High': 'high', 'Low': 'low',
                                'Close': 'close', 'Volume': 'volume'})
        df = df[['open', 'high', 'low', 'close', 'volume']].copy()
        df['symbol'] = symbol
        df.index.name = 'datetime'
        return df.reset_index()
    except Exception as e:
        print(f"  [WARN] {symbol}: {e}")
        return pd.DataFrame()


def label_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Add 'outcome' column based on forward return over FORWARD_BARS days."""
    df = df.copy()
    fwd_close = df['close'].shift(-FORWARD_BARS)
    fwd_ret_pct = (fwd_close - df['close']) / df['close'] * 100
    df['fwd_ret_pct'] = fwd_ret_pct
    conds = [fwd_ret_pct >= BUY_THRESH, fwd_ret_pct <= SELL_THRESH]
    choices = ['BUY', 'SELL']
    df['outcome'] = np.select(conds, choices, default='HOLD')
    return df


def build_dataset(symbols: list) -> pd.DataFrame:
    """Download + featurize + label every symbol; concat into one big df."""
    all_dfs = []
    total = len(symbols)
    print(f"\nDownloading {LOOKBACK_YEARS}y of daily data for {total} symbols...")
    for i, sym in enumerate(symbols, 1):
        raw = download_symbol(sym)
        if raw.empty or len(raw) < 100:
            print(f"  [{i:3d}/{total}] {sym:6s}  SKIP (insufficient data)")
            continue
        # compute_indicators expects lowercase OHLCV; add hour/minute=0 for daily
        feats = compute_indicators(raw)
        feats['hour'] = 9      # market open hour (daily bars stand in for session)
        feats['minute'] = 30
        labeled = label_bars(feats)
        all_dfs.append(labeled)
        bars = len(labeled)
        outcome_dist = labeled['outcome'].value_counts().to_dict()
        print(f"  [{i:3d}/{total}] {sym:6s}  {bars:5d} bars  "
              f"BUY={outcome_dist.get('BUY',0):4d} "
              f"SELL={outcome_dist.get('SELL',0):4d} "
              f"HOLD={outcome_dist.get('HOLD',0):4d}")
        time.sleep(0.05)   # be polite to yfinance

    if not all_dfs:
        raise RuntimeError("No data downloaded for any symbol.")

    big = pd.concat(all_dfs, ignore_index=True)
    # drop rows with NaN forward return (last FORWARD_BARS bars per symbol)
    big = big.dropna(subset=['fwd_ret_pct'])
    return big


def train(df: pd.DataFrame):
    """Train ensemble model via existing MLPredictor."""
    print("\n" + "=" * 60)
    print("TRAINING ENSEMBLE MODEL")
    print("=" * 60)
    print(f"Total samples:  {len(df):,}")
    print(f"Symbols:        {df['symbol'].nunique()}")
    print(f"Date range:     {df['datetime'].min()} -> {df['datetime'].max()}")
    print(f"Outcome dist:")
    for k, v in df['outcome'].value_counts().items():
        pct = v / len(df) * 100
        print(f"   {k:5s}  {v:7,d}  ({pct:5.1f}%)")

    predictor = MLPredictor(
        model_path=str(MODEL_PATH),
        scaler_path=str(SCALER_PATH),
        label_path=str(LABELS_PATH),
        feature_cols=mlc.ML_FEATURES,
        min_confidence=mlc.ML_MIN_CONFIDENCE,
    )
    print(f"\nTraining on {len(mlc.ML_FEATURES)} features: {mlc.ML_FEATURES}")
    metrics = predictor.train(df)
    print("\n--- TRAINING METRICS ---")
    if isinstance(metrics, dict):
        for k, v in metrics.items():
            print(f"  {k}: {v}")
    else:
        print(f"  {metrics}")
    print(f"\nModel saved to:")
    print(f"  {MODEL_PATH}")
    print(f"  {SCALER_PATH}")
    print(f"  {LABELS_PATH}")


def main():
    symbols = list(cfg.DEFAULT_WATCHLIST)
    # apply blacklist if present
    bl = OUT_DIR / 'symbol_blacklist.txt'
    if bl.exists():
        blacklist = {s.strip() for s in bl.read_text().splitlines() if s.strip()}
        symbols = [s for s in symbols if s not in blacklist]
        print(f"[BLACKLIST] excluded: {sorted(blacklist)}")

    print(f"Universe: {len(symbols)} symbols")

    if DATASET_CACHE.exists() and '--rebuild' not in sys.argv:
        print(f"\nLoading cached dataset: {DATASET_CACHE}")
        big = pd.read_parquet(DATASET_CACHE)
    else:
        big = build_dataset(symbols)
        try:
            big.to_parquet(DATASET_CACHE, index=False)
            print(f"\nDataset cached: {DATASET_CACHE} ({len(big):,} rows)")
        except Exception as e:
            print(f"  [WARN] could not cache parquet: {e}")

    try:
        train(big)
        print("\n" + "=" * 60)
        print("TRAINING COMPLETE — model files updated")
        print("Live bot will reload on next ML retrain interval (24h)")
        print("Or restart it now to apply immediately.")
        print("=" * 60)
    except Exception as e:
        print(f"\n[ERROR] training failed: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
