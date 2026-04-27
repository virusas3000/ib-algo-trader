"""
ML Model — Train, predict, and retrain the trading signal classifier.

Model: Ensemble of RandomForest + XGBoost + LogisticRegression
Labels: BUY / SELL / HOLD  (derived from forward returns on trade history)
"""
from __future__ import annotations

import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger("ml_model")


# ── Label generation ─────────────────────────────────────────────────────────

def label_from_forward_return(df: pd.DataFrame,
                               forward_bars: int = 6,
                               buy_threshold: float = 0.003,
                               sell_threshold: float = -0.003) -> pd.Series:
    """
    Label each bar based on future return:
      BUY  (+1) if close goes up > buy_threshold  within forward_bars
      SELL (-1) if close goes down > |sell_threshold| within forward_bars
      HOLD  (0) otherwise
    """
    future_close = df["close"].shift(-forward_bars)
    fwd_ret = (future_close - df["close"]) / df["close"]
    labels = pd.Series("HOLD", index=df.index)
    labels[fwd_ret >  buy_threshold]   = "BUY"
    labels[fwd_ret <  sell_threshold]  = "SELL"
    return labels


# ── Model class ──────────────────────────────────────────────────────────────

class MLSignalModel:
    """
    Ensemble ML model that replaces hard-coded strategy logic.
    Predicts BUY / SELL / HOLD with a confidence probability.
    Falls back to HOLD if model is not trained yet.
    """

    def __init__(self, model_path: str, scaler_path: str, label_path: str,
                 feature_cols: list, min_confidence: float = 0.60):
        self.model_path    = Path(model_path)
        self.scaler_path   = Path(scaler_path)
        self.label_path    = Path(label_path)
        self.feature_cols  = feature_cols
        self.min_confidence = min_confidence

        self.model   = None
        self.scaler  = None
        self.classes = None
        self._load()

    # ── Persistence ──────────────────────────────────────

    def _load(self):
        try:
            if self.model_path.exists() and self.scaler_path.exists():
                self.model   = pickle.load(open(self.model_path,  "rb"))
                self.scaler  = pickle.load(open(self.scaler_path, "rb"))
                self.classes = pickle.load(open(self.label_path,  "rb"))
                log.info(f"ML model loaded from {self.model_path}")
            else:
                log.warning("No trained ML model found — will run in HOLD-only mode until trained.")
        except Exception as e:
            log.error(f"Failed to load ML model: {e}")

    def save(self):
        pickle.dump(self.model,   open(self.model_path,  "wb"))
        pickle.dump(self.scaler,  open(self.scaler_path, "wb"))
        pickle.dump(self.classes, open(self.label_path,  "wb"))
        log.info(f"ML model saved to {self.model_path}")

    # ── Training ─────────────────────────────────────────

    def train(self, df: pd.DataFrame):
        """
        Train the ensemble on a labelled DataFrame.
        df must have self.feature_cols and an 'outcome' column (BUY/SELL/HOLD).
        """
        from sklearn.ensemble import RandomForestClassifier, VotingClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler, LabelEncoder
        try:
            from xgboost import XGBClassifier
            xgb_available = True
        except ImportError:
            xgb_available = False
            log.warning("XGBoost not installed — using RF + LR only")

        df = df.dropna(subset=self.feature_cols + ["outcome"])
        if len(df) < 100:
            log.warning(f"Only {len(df)} samples — need at least 100 to train. Skipping.")
            return

        X = df[self.feature_cols].values
        y = df["outcome"].values

        le = LabelEncoder()
        y_enc = le.fit_transform(y)
        self.classes = le.classes_

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # Build ensemble
        estimators = [
            ("rf", RandomForestClassifier(n_estimators=200, max_depth=8,
                                          min_samples_leaf=5, random_state=42, n_jobs=-1)),
            ("lr", LogisticRegression(max_iter=1000, C=0.5, random_state=42)),
        ]
        if xgb_available:
            estimators.append(("xgb", XGBClassifier(
                n_estimators=200, max_depth=5, learning_rate=0.05,
                use_label_encoder=False, eval_metric="mlogloss",
                random_state=42, n_jobs=-1
            )))

        ensemble = VotingClassifier(estimators=estimators, voting="soft")
        ensemble.fit(X_scaled, y_enc)

        self.model  = ensemble
        self.scaler = scaler
        self.save()

        # Log accuracy on training set
        preds = ensemble.predict(X_scaled)
        acc = (preds == y_enc).mean()
        class_counts = pd.Series(y).value_counts().to_dict()
        log.info(f"ML model trained: {len(df)} samples, train accuracy={acc:.2%}, "
                 f"classes={class_counts}")

    # ── Inference ────────────────────────────────────────

    def predict(self, df: pd.DataFrame) -> tuple[str, float]:
        """
        Given the latest bar (last row of df), return (signal, confidence).
        signal: 'BUY' | 'SELL' | 'HOLD'
        confidence: 0.0 – 1.0
        """
        if self.model is None:
            return "HOLD", 0.0

        try:
            last = df.iloc[-1][self.feature_cols].values.reshape(1, -1)
            if np.isnan(last).any():
                return "HOLD", 0.0

            scaled   = self.scaler.transform(last)
            proba    = self.model.predict_proba(scaled)[0]
            best_idx = int(np.argmax(proba))
            signal   = self.classes[best_idx]
            confidence = float(proba[best_idx])

            if confidence < self.min_confidence:
                return "HOLD", confidence

            return signal, confidence

        except Exception as e:
            log.error(f"ML predict error: {e}")
            return "HOLD", 0.0


# ── Data collector for retraining ────────────────────────────────────────────

class TrainingDataCollector:
    """
    Collects intraday bars + computed features into a CSV
    that can be labelled and fed back to the model for retraining.
    """

    def __init__(self, path: str = "ml_training_data.csv"):
        self.path = Path(path)
        self._buf: list[dict] = []

    def record(self, symbol: str, df: pd.DataFrame, feature_cols: list):
        """Append the latest bar's features to buffer."""
        last = df.iloc[-1]
        row  = {"symbol": symbol, "timestamp": datetime.now().isoformat()}
        for col in feature_cols:
            row[col] = float(last.get(col, 0.0))
        self._buf.append(row)
        if len(self._buf) >= 50:
            self.flush()

    def flush(self):
        if not self._buf:
            return
        new_df = pd.DataFrame(self._buf)
        if self.path.exists():
            new_df.to_csv(self.path, mode="a", header=False, index=False)
        else:
            new_df.to_csv(self.path, index=False)
        self._buf = []

    def load_and_label(self, feature_cols: list) -> Optional[pd.DataFrame]:
        """Load raw collected data and generate forward-return labels."""
        if not self.path.exists():
            return None
        df = pd.read_csv(self.path)
        if len(df) < 200:
            return None
        # Sort by time and label
        df = df.sort_values("timestamp").reset_index(drop=True)
        df["outcome"] = label_from_forward_return(df)
        return df.dropna(subset=feature_cols + ["outcome"])
