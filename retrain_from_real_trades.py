"""
Retrain the trade-quality classifier from real fills in trades.db.

This complements the bar-feature ML model: it predicts WIN/LOSS at trade-entry
based on TRADE CONTEXT (hour, day-of-week, strategy, side, symbol-class). The
output (win-probability) is used by ml_integration.py to gate entries via
ML_MIN_CONFIDENCE.

Run:  python3 retrain_from_real_trades.py
"""
from __future__ import annotations

import json
import pickle
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.ensemble import VotingClassifier

ROOT = Path(__file__).parent
DB = ROOT / "trades.db"
OUT_DIR = ROOT / "models"
OUT_DIR.mkdir(exist_ok=True)

CTX_MODEL_PATH  = OUT_DIR / "trade_quality_model.pkl"
CTX_SCALER_PATH = OUT_DIR / "trade_quality_scaler.pkl"
CTX_META_PATH   = OUT_DIR / "trade_quality_meta.json"

# Symbol class buckets (rough — based on observed P&L)
SYMBOL_CLASS = {
    # winners in the dataset
    "UVXY": "vol", "TQQQ": "leveraged_etf",
    "SPY": "broad_etf", "QQQ": "broad_etf", "IWM": "broad_etf",
    "VTI": "broad_etf", "EEM": "broad_etf", "TLT": "bond_etf",
    "AAPL": "mega_tech", "MSFT": "mega_tech", "NVDA": "mega_tech",
    "AMZN": "mega_tech", "GOOGL": "mega_tech", "META": "mega_tech",
    "TSLA": "mega_tech", "AMD": "mega_tech", "AVGO": "mega_tech",
    "LRCX": "semi", "MU": "semi",
}


def symbol_class(sym: str) -> str:
    return SYMBOL_CLASS.get(sym, "single_stock")


def load_trades() -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT exit_time, symbol, side, exit_price, pnl, reason, strategy FROM trades",
        con,
    )
    con.close()
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    # convert HKT->ET (HKT is exit timestamp in DB; subtract 12h for ET)
    df["et_time"] = df["exit_time"] - pd.Timedelta(hours=12)
    df["hour_et"] = df["et_time"].dt.hour
    df["dow"] = df["et_time"].dt.dayofweek
    df["is_long"] = (df["side"] == "LONG").astype(int)
    df["sym_class"] = df["symbol"].map(symbol_class)
    df["win"] = (df["pnl"] > 0).astype(int)
    return df


def featurize(df: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    feat = pd.DataFrame()
    feat["hour_et"] = df["hour_et"]
    feat["dow"] = df["dow"]
    feat["is_long"] = df["is_long"]
    # cyclical hour encoding
    feat["hour_sin"] = np.sin(2 * np.pi * df["hour_et"] / 24)
    feat["hour_cos"] = np.cos(2 * np.pi * df["hour_et"] / 24)
    # one-hot strategy
    for strat in ["ORB", "POWER_HOUR", "GAP_FILL", "MOMENTUM"]:
        feat[f"strat_{strat}"] = (df["strategy"] == strat).astype(int)
    # one-hot symbol class
    for cls in ["vol", "leveraged_etf", "broad_etf", "bond_etf",
                "mega_tech", "semi", "single_stock"]:
        feat[f"sym_{cls}"] = (df["sym_class"] == cls).astype(int)
    # interaction: hour × is_long
    feat["hour_x_long"] = df["hour_et"] * df["is_long"]
    return feat.values.astype(float), list(feat.columns)


def main() -> None:
    print("=" * 60)
    print("RETRAINING TRADE-QUALITY MODEL FROM REAL FILLS")
    print("=" * 60)
    df = load_trades()
    print(f"Loaded {len(df)} trades  | wins={df.win.sum()}  losses={(1-df.win).sum()}  "
          f"win-rate={df.win.mean():.1%}")

    X, feature_names = featurize(df)
    y = df["win"].values

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    rf = RandomForestClassifier(n_estimators=300, max_depth=4,
                                min_samples_leaf=3, class_weight="balanced",
                                random_state=42, n_jobs=-1)
    gb = GradientBoostingClassifier(n_estimators=200, max_depth=3,
                                    learning_rate=0.05, random_state=42)
    lr = LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced",
                            random_state=42)
    ensemble = VotingClassifier(
        estimators=[("rf", rf), ("gb", gb), ("lr", lr)],
        voting="soft",
    )

    # Cross-validated metrics (small dataset → 5-fold stratified)
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    acc = cross_val_score(ensemble, X_scaled, y, cv=cv, scoring="accuracy").mean()
    auc = cross_val_score(ensemble, X_scaled, y, cv=cv, scoring="roc_auc").mean()
    f1  = cross_val_score(ensemble, X_scaled, y, cv=cv, scoring="f1").mean()
    print(f"\n5-fold CV  → accuracy={acc:.3f}  AUC={auc:.3f}  F1(win)={f1:.3f}")

    ensemble.fit(X_scaled, y)
    print(f"Trained ensemble on {len(y)} samples.")

    # Feature importance from RF inside the fitted ensemble
    fitted_rf = ensemble.named_estimators_["rf"]
    importances = sorted(
        zip(feature_names, fitted_rf.feature_importances_),
        key=lambda x: -x[1],
    )
    print("\nTop 10 features:")
    for name, imp in importances[:10]:
        print(f"  {name:25s}  {imp:.3f}")

    # Persist
    with open(CTX_MODEL_PATH, "wb") as f:
        pickle.dump(ensemble, f)
    with open(CTX_SCALER_PATH, "wb") as f:
        pickle.dump(scaler, f)
    meta = {
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "n_samples": int(len(y)),
        "n_wins": int(y.sum()),
        "n_losses": int(len(y) - y.sum()),
        "feature_names": feature_names,
        "cv_accuracy": float(acc),
        "cv_auc": float(auc),
        "cv_f1": float(f1),
        "top_features": [{"name": n, "importance": float(i)}
                         for n, i in importances[:10]],
        "recommended_min_confidence": 0.60,
    }
    CTX_META_PATH.write_text(json.dumps(meta, indent=2))
    print(f"\nSaved → {CTX_MODEL_PATH.name}, {CTX_SCALER_PATH.name}, {CTX_META_PATH.name}")

    # Predict on all trades and show worst contexts
    proba_win = ensemble.predict_proba(X_scaled)[:, list(ensemble.classes_).index(1)]
    df["model_p_win"] = proba_win

    print("\nLowest model-confidence contexts (these would be filtered):")
    cols = ["et_time", "symbol", "side", "strategy", "pnl", "model_p_win"]
    print(df.sort_values("model_p_win").head(10)[cols].to_string(index=False))

    print("\nHighest model-confidence contexts:")
    print(df.sort_values("model_p_win", ascending=False).head(10)[cols].to_string(index=False))

    # If we had filtered with threshold 0.50, what P&L would we have kept?
    for thr in [0.40, 0.50, 0.55, 0.60]:
        kept = df[proba_win >= thr]
        skipped = df[proba_win < thr]
        if len(kept):
            print(f"\nThreshold {thr:.2f}:  kept={len(kept)}  "
                  f"win-rate={kept.win.mean():.1%}  "
                  f"net-pnl=${kept.pnl.sum():+.2f}  "
                  f"skipped={len(skipped)}  skipped-pnl=${skipped.pnl.sum():+.2f}")


if __name__ == "__main__":
    main()
