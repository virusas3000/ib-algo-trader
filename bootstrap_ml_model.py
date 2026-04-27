#!/usr/bin/env python3
"""
Initial ML Model Training Script - Daily Data Version
Creates a basic trained ML model to bootstrap the system

Author: Hermes Agent + virusas3000
"""

import pandas as pd
import numpy as np
import logging
from pathlib import Path
from datetime import datetime, timedelta
import pickle
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.model_selection import train_test_split

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)

def create_synthetic_training_data():
    """Create synthetic training data for initial model bootstrapping"""
    logger.info("Creating synthetic training data for ML model bootstrapping...")
    
    np.random.seed(42)  # For reproducibility
    n_samples = 2000
    
    # Define feature columns
    feature_cols = [
        'sma_20', 'sma_50', 'sma_200', 'ema_12', 'ema_26',
        'rsi_14', 'macd', 'macd_signal', 'macd_histogram',
        'bb_upper', 'bb_lower', 'bb_middle', 'bb_percent',
        'atr_14', 'stoch_k', 'stoch_d', 'cci_20',
        'williams_r', 'volume_sma', 'price_volume_trend',
        'close_sma20_ratio', 'volume_ratio', 'volatility'
    ]
    
    # Generate synthetic features based on realistic market patterns
    data = {
        # Moving averages (price-like values around 100-200)
        'sma_20': np.random.normal(150, 30, n_samples),
        'sma_50': np.random.normal(148, 30, n_samples),
        'sma_200': np.random.normal(145, 25, n_samples),
        'ema_12': np.random.normal(152, 32, n_samples),
        'ema_26': np.random.normal(149, 30, n_samples),
        
        # RSI (0-100)
        'rsi_14': np.random.beta(2, 2, n_samples) * 100,
        
        # MACD values
        'macd': np.random.normal(0, 2, n_samples),
        'macd_signal': np.random.normal(0, 1.5, n_samples),
        'macd_histogram': np.random.normal(0, 1, n_samples),
        
        # Bollinger Bands
        'bb_upper': np.random.normal(160, 35, n_samples),
        'bb_lower': np.random.normal(140, 25, n_samples),
        'bb_middle': np.random.normal(150, 30, n_samples),
        'bb_percent': np.random.beta(2, 2, n_samples),  # 0-1
        
        # ATR (positive values)
        'atr_14': np.random.exponential(2, n_samples),
        
        # Stochastic (0-100)
        'stoch_k': np.random.beta(2, 2, n_samples) * 100,
        'stoch_d': np.random.beta(2, 2, n_samples) * 100,
        
        # CCI (typically -200 to 200)
        'cci_20': np.random.normal(0, 50, n_samples),
        
        # Williams %R (-100 to 0)
        'williams_r': -np.random.beta(2, 2, n_samples) * 100,
        
        # Volume indicators
        'volume_sma': np.random.exponential(1000000, n_samples),
        'price_volume_trend': np.random.normal(0, 1000000, n_samples),
        
        # Ratio indicators
        'close_sma20_ratio': np.random.normal(1.0, 0.05, n_samples),
        'volume_ratio': np.random.exponential(1, n_samples),
        'volatility': np.random.exponential(0.02, n_samples),
    }
    
    df = pd.DataFrame(data)
    
    # Create realistic labels based on feature patterns
    outcomes = []
    for i, row in df.iterrows():
        # Define bullish conditions
        bullish_score = 0
        
        # RSI conditions
        if 30 <= row['rsi_14'] <= 70:  # Not overbought/oversold
            bullish_score += 1
        
        # MACD bullish
        if row['macd'] > row['macd_signal']:
            bullish_score += 1
        
        # Price above moving averages
        if row['close_sma20_ratio'] > 1.01:  # Above SMA20
            bullish_score += 1
        
        # Bollinger band position
        if 0.2 <= row['bb_percent'] <= 0.8:  # Middle of bands
            bullish_score += 1
        
        # Stochastic conditions
        if row['stoch_k'] > row['stoch_d'] and row['stoch_k'] > 20:
            bullish_score += 1
        
        # Volume confirmation
        if row['volume_ratio'] > 1.2:  # Above average volume
            bullish_score += 1
        
        # Generate outcome based on score with some randomness
        rand_factor = np.random.random()
        
        if bullish_score >= 4 and rand_factor > 0.3:
            outcomes.append('BUY')
        elif bullish_score <= 2 and rand_factor > 0.3:
            outcomes.append('SELL')
        else:
            outcomes.append('HOLD')
    
    df['outcome'] = outcomes
    
    # Add some metadata
    df['symbol'] = np.random.choice(['SPY', 'QQQ', 'AAPL', 'MSFT', 'AMZN'], n_samples)
    df['timestamp'] = pd.date_range(start='2023-01-01', periods=n_samples, freq='5min')
    
    logger.info(f"Generated {len(df)} synthetic training samples")
    class_counts = df['outcome'].value_counts()
    logger.info(f"Class distribution: {class_counts.to_dict()}")
    
    return df, feature_cols

def train_bootstrap_model(training_data, feature_cols, save_path="./"):
    """Train the bootstrap ML model with synthetic data"""
    logger.info("Training bootstrap ML model...")
    
    # Prepare data
    X = training_data[feature_cols].values
    y = training_data['outcome'].values
    
    # Encode labels
    le = LabelEncoder()
    y_enc = le.fit_transform(y)
    classes = le.classes_
    
    # Scale features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # Split for validation
    X_train, X_test, y_train, y_test = train_test_split(
        X_scaled, y_enc, test_size=0.2, random_state=42, stratify=y_enc
    )
    
    # Build ensemble (without XGBoost for simplicity)
    estimators = [
        ("rf", RandomForestClassifier(n_estimators=100, max_depth=10,
                                      min_samples_leaf=5, random_state=42, n_jobs=-1)),
        ("lr", LogisticRegression(max_iter=1000, C=1.0, random_state=42)),
    ]
    
    ensemble = VotingClassifier(estimators=estimators, voting="soft")
    ensemble.fit(X_train, y_train)
    
    # Evaluate
    train_acc = ensemble.score(X_train, y_train)
    test_acc = ensemble.score(X_test, y_test)
    
    logger.info(f"Model trained - Train accuracy: {train_acc:.2%}, Test accuracy: {test_acc:.2%}")
    
    # Save model components
    model_path = Path(save_path) / "ml_model.pkl"
    scaler_path = Path(save_path) / "ml_scaler.pkl"
    label_path = Path(save_path) / "ml_labels.pkl"
    
    pickle.dump(ensemble, open(model_path, "wb"))
    pickle.dump(scaler, open(scaler_path, "wb"))
    pickle.dump(classes, open(label_path, "wb"))
    
    logger.info(f"Model saved to {model_path}")
    logger.info(f"Scaler saved to {scaler_path}")
    logger.info(f"Labels saved to {label_path}")
    
    return ensemble, scaler, classes

def test_model(model, scaler, classes, feature_cols):
    """Test the trained model with sample predictions"""
    logger.info("Testing model predictions...")
    
    # Generate test samples
    test_samples = pd.DataFrame({
        # Bullish pattern
        'sma_20': [150], 'sma_50': [145], 'sma_200': [140], 'ema_12': [152], 'ema_26': [148],
        'rsi_14': [45], 'macd': [0.5], 'macd_signal': [0.2], 'macd_histogram': [0.3],
        'bb_upper': [160], 'bb_lower': [140], 'bb_middle': [150], 'bb_percent': [0.6],
        'atr_14': [2.0], 'stoch_k': [60], 'stoch_d': [55], 'cci_20': [20],
        'williams_r': [-40], 'volume_sma': [1000000], 'price_volume_trend': [50000],
        'close_sma20_ratio': [1.02], 'volume_ratio': [1.5], 'volatility': [0.02]
    })
    
    # Make prediction
    X_test = scaler.transform(test_samples[feature_cols].values)
    proba = model.predict_proba(X_test)[0]
    best_idx = int(np.argmax(proba))
    predicted_class = classes[best_idx]
    confidence = float(proba[best_idx])
    
    logger.info(f"Test prediction: {predicted_class} (confidence: {confidence:.1%})")
    logger.info(f"All probabilities: {dict(zip(classes, proba))}")

def main():
    """Main bootstrap training function"""
    logger.info("🤖 Starting ML Model Bootstrap Training")
    logger.info("=" * 60)
    
    try:
        # Step 1: Create synthetic training data
        logger.info("🧪 Generating synthetic training data...")
        training_data, feature_cols = create_synthetic_training_data()
        
        # Save synthetic training data for inspection
        training_data.to_csv("bootstrap_training_data.csv", index=False)
        logger.info("Saved bootstrap training data to bootstrap_training_data.csv")
        
        # Step 2: Train the model
        logger.info("🧠 Training bootstrap ML model...")
        model, scaler, classes = train_bootstrap_model(training_data, feature_cols)
        
        # Step 3: Test the model
        logger.info("🧪 Testing model predictions...")
        test_model(model, scaler, classes, feature_cols)
        
        logger.info("✅ Bootstrap ML Model Training Complete!")
        logger.info("📋 Summary:")
        logger.info(f"  - Training samples: {len(training_data):,}")
        logger.info(f"  - Features: {len(feature_cols)}")
        logger.info(f"  - Classes: {list(classes)}")
        logger.info(f"  - Model files created:")
        logger.info(f"    • ml_model.pkl")
        logger.info(f"    • ml_scaler.pkl")
        logger.info(f"    • ml_labels.pkl")
        
        logger.info("🚀 The ML model is now ready for integration!")
        logger.info("   It will learn and improve from real trading data over time.")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Bootstrap training failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

if __name__ == "__main__":
    success = main()
    if success:
        print("\n🎉 Bootstrap ML model created successfully!")
        print("The trading bot can now use ML predictions and will improve over time.")
    else:
        print("\n💥 Bootstrap training failed. Check the logs above.")