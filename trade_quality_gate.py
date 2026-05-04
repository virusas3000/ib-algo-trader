"""
Trade Quality Gate — context-feature classifier trained on real fills.

Lightweight helper: given (symbol, side, strategy, datetime_et), returns
predicted win probability. Used by the trader to veto low-quality entries
even if the bar-feature ML model is bullish.

Usage:
    from trade_quality_gate import predict_win_prob, should_take
    p = predict_win_prob('NVDA', 'LONG', 'ORB', datetime.now(ET))
    if not should_take(p, threshold=0.50):
        log.info(f"Skipping — quality model p_win={p:.2f}")
        return
"""
from __future__ import annotations

import json
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
MODEL_PATH  = ROOT / "models" / "trade_quality_model.pkl"
SCALER_PATH = ROOT / "models" / "trade_quality_scaler.pkl"
META_PATH   = ROOT / "models" / "trade_quality_meta.json"

SYMBOL_CLASS = {
    "UVXY": "vol", "TQQQ": "leveraged_etf",
    "SPY": "broad_etf", "QQQ": "broad_etf", "IWM": "broad_etf",
    "VTI": "broad_etf", "EEM": "broad_etf", "TLT": "bond_etf",
    "AAPL": "mega_tech", "MSFT": "mega_tech", "NVDA": "mega_tech",
    "AMZN": "mega_tech", "GOOGL": "mega_tech", "META": "mega_tech",
    "TSLA": "mega_tech", "AMD": "mega_tech", "AVGO": "mega_tech",
    "LRCX": "semi", "MU": "semi",
}

_model = None
_scaler = None
_meta = None


def _load() -> bool:
    global _model, _scaler, _meta
    if _model is not None:
        return True
    if not MODEL_PATH.exists():
        return False
    _model  = pickle.load(open(MODEL_PATH, "rb"))
    _scaler = pickle.load(open(SCALER_PATH, "rb"))
    _meta   = json.loads(META_PATH.read_text())
    return True


def _featurize(symbol: str, side: str, strategy: str, dt_et: datetime) -> np.ndarray:
    sym_cls = SYMBOL_CLASS.get(symbol, "single_stock")
    is_long = 1 if side.upper() == "LONG" else 0
    h = dt_et.hour
    dow = dt_et.weekday()
    feats = {
        "hour_et": h, "dow": dow, "is_long": is_long,
        "hour_sin": np.sin(2 * np.pi * h / 24),
        "hour_cos": np.cos(2 * np.pi * h / 24),
    }
    for s in ["ORB", "POWER_HOUR", "GAP_FILL", "MOMENTUM"]:
        feats[f"strat_{s}"] = 1 if strategy == s else 0
    for c in ["vol", "leveraged_etf", "broad_etf", "bond_etf",
              "mega_tech", "semi", "single_stock"]:
        feats[f"sym_{c}"] = 1 if sym_cls == c else 0
    feats["hour_x_long"] = h * is_long
    # Order must match training feature_names
    order = _meta["feature_names"]
    return np.array([[feats[n] for n in order]], dtype=float)


def predict_win_prob(symbol: str, side: str, strategy: str,
                     dt_et: datetime) -> float:
    """Return P(win) ∈ [0,1]. Returns 0.5 (neutral) if model unavailable."""
    if not _load():
        return 0.5
    X = _featurize(symbol, side, strategy, dt_et)
    Xs = _scaler.transform(X)
    proba = _model.predict_proba(Xs)[0]
    win_idx = list(_model.classes_).index(1)
    return float(proba[win_idx])


def should_take(p_win: float, threshold: float = 0.50) -> bool:
    """Convenience: True iff predicted win-prob >= threshold."""
    return p_win >= threshold


if __name__ == "__main__":
    # Smoke test
    from datetime import datetime
    cases = [
        ("NVDA",  "LONG",  "ORB",        datetime(2026, 4, 28, 12, 30)),  # historical winner pattern
        ("PM",    "SHORT", "GAP_FILL",   datetime(2026, 4, 23,  9, 59)),  # historical disaster
        ("IWM",   "LONG",  "ORB",        datetime(2026, 4, 24, 14, 20)),  # post-lunch chop
        ("UVXY",  "SHORT", "POWER_HOUR", datetime(2026, 4, 28, 15, 43)),  # historical winner
    ]
    print(f"{'symbol':<6} {'side':<6} {'strategy':<11} {'time':<19} {'p_win':>6}")
    for sym, side, strat, dt in cases:
        p = predict_win_prob(sym, side, strat, dt)
        print(f"{sym:<6} {side:<6} {strat:<11} {dt.strftime('%Y-%m-%d %H:%M'):<19} {p:>6.3f}")
