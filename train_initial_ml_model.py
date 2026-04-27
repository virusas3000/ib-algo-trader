#!/usr/bin/env python3
"""
Initial ML Model Training Script
Uses historical trade data and market data to train the initial ML model

Author: Hermes Agent + virusas3000
"""

import pandas as pd
import numpy as np
import logging
from pathlib import Path
from datetime import datetime, timedelta
import yfinance as yf

from ml_model import MLSignalModel, label_from_forward_return
from ml_indicators import compute_indicators

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)

def download_historical_data(symbols, period="6mo", interval="5m"):
    """Download historical data for training"""
    logger.info(f"Downloading historical data for {len(symbols)} symbols...")
    
    all_data = {}
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period, interval=interval)
            
            if len(hist) > 100:  # Only use if we have enough data
                # Rename columns to match our format
                hist = hist.rename(columns={
                    'Open': 'open',
                    'High': 'high', 
                    'Low': 'low',
                    'Close': 'close',
                    'Volume': 'volume'
                })
                hist = hist[['open', 'high', 'low', 'close', 'volume']].copy()
                all_data[symbol] = hist
                logger.info(f"Downloaded {len(hist)} bars for {symbol}")
            else:
                logger.warning(f"Insufficient data for {symbol}: {len(hist)} bars")
                
        except Exception as e:
            logger.error(f"Failed to download {symbol}: {e}")
    
    logger.info(f"Successfully downloaded data for {len(all_data)} symbols")
    return all_data

def prepare_training_data(market_data_dict):
    """Prepare training dataset from market data"""
    logger.info("Preparing training dataset...")
    
    feature_cols = [
        'sma_20', 'sma_50', 'sma_200', 'ema_12', 'ema_26',
        'rsi_14', 'macd', 'macd_signal', 'macd_histogram',
        'bb_upper', 'bb_lower', 'bb_middle', 'bb_percent',
        'atr_14', 'stoch_k', 'stoch_d', 'cci_20',
        'williams_r', 'volume_sma', 'price_volume_trend',
        'close_sma20_ratio', 'volume_ratio', 'volatility'
    ]
    
    all_training_data = []
    
    for symbol, bars in market_data_dict.items():
        try:
            # Compute technical indicators
            bars_with_indicators = compute_indicators(bars)
            
            if len(bars_with_indicators) < 250:  # Need enough data for labeling
                continue
                
            # Add additional ML features
            bars_with_indicators['close_sma20_ratio'] = bars_with_indicators['close'] / bars_with_indicators['sma_20']
            bars_with_indicators['volume_ratio'] = bars_with_indicators['volume'] / bars_with_indicators['volume_sma']
            bars_with_indicators['volatility'] = bars_with_indicators['close'].rolling(10).std() / bars_with_indicators['close'].rolling(10).mean()
            
            # Generate labels based on future returns
            bars_with_indicators['outcome'] = label_from_forward_return(
                bars_with_indicators, 
                forward_bars=6,      # 6 bars ahead (30 minutes for 5-min bars)
                buy_threshold=0.005, # 0.5% gain threshold
                sell_threshold=-0.005 # 0.5% loss threshold
            )
            
            # Add symbol and timestamp
            bars_with_indicators['symbol'] = symbol
            bars_with_indicators['timestamp'] = bars_with_indicators.index
            
            # Select complete rows
            complete_rows = bars_with_indicators.dropna(subset=feature_cols + ['outcome'])
            
            if len(complete_rows) > 50:
                all_training_data.append(complete_rows)
                logger.info(f"Prepared {len(complete_rows)} training samples for {symbol}")
            
        except Exception as e:
            logger.error(f"Error preparing training data for {symbol}: {e}")
    
    if not all_training_data:
        raise ValueError("No training data could be prepared")
    
    # Combine all data
    combined_data = pd.concat(all_training_data, ignore_index=True)
    
    # Balance the dataset somewhat
    class_counts = combined_data['outcome'].value_counts()
    logger.info(f"Class distribution: {class_counts.to_dict()}")
    
    # Sample to balance classes if needed
    min_class_size = min(class_counts.values())
    if min_class_size > 1000:  # Only balance if we have enough data
        balanced_data = []
        for class_label in class_counts.index:
            class_data = combined_data[combined_data['outcome'] == class_label]
            sampled = class_data.sample(n=min(len(class_data), min_class_size * 2), random_state=42)
            balanced_data.append(sampled)
        combined_data = pd.concat(balanced_data, ignore_index=True)
        logger.info(f"Balanced dataset size: {len(combined_data)}")
    
    return combined_data, feature_cols

def train_initial_model(training_data, feature_cols, save_path="./"):
    """Train the initial ML model"""
    logger.info("Training initial ML model...")
    
    # Initialize model
    model = MLSignalModel(
        model_path=Path(save_path) / "ml_model.pkl",
        scaler_path=Path(save_path) / "ml_scaler.pkl",
        label_path=Path(save_path) / "ml_labels.pkl",
        feature_cols=feature_cols,
        min_confidence=0.65
    )
    
    # Train the model
    model.train(training_data)
    
    return model

def main():
    """Main training function"""
    logger.info("🤖 Starting Initial ML Model Training")
    logger.info("=" * 50)
    
    # Stock symbols to train on (using a subset of the main watchlist)
    training_symbols = [
        # Large cap tech
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META",
        
        # Market indices
        "SPY", "QQQ", "IWM",
        
        # Major stocks with good volume
        "JPM", "BAC", "XOM", "JNJ", "PG", "WMT", "HD", "V", "MA", "UNH"
    ]
    
    try:
        # Step 1: Download historical data
        logger.info("📥 Downloading historical market data...")
        market_data = download_historical_data(training_symbols, period="6mo", interval="5m")
        
        if len(market_data) < 5:
            raise ValueError(f"Insufficient market data downloaded: {len(market_data)} symbols")
        
        # Step 2: Prepare training dataset
        logger.info("🔧 Preparing training dataset...")
        training_data, feature_cols = prepare_training_data(market_data)
        
        logger.info(f"Training dataset prepared: {len(training_data)} samples")
        logger.info(f"Features: {len(feature_cols)} indicators")
        
        # Save training data for inspection
        training_data.to_csv("initial_training_data.csv", index=False)
        logger.info("Saved training data to initial_training_data.csv")
        
        # Step 3: Train the model
        logger.info("🧠 Training ML model...")
        model = train_initial_model(training_data, feature_cols)
        
        # Step 4: Test predictions on a sample
        logger.info("🧪 Testing model predictions...")
        sample_data = training_data.head(10)
        for i, row in sample_data.iterrows():
            test_df = pd.DataFrame([row])
            signal, confidence = model.predict(test_df)
            actual = row['outcome']
            symbol = row['symbol']
            
            logger.info(f"Test: {symbol} | Predicted: {signal} ({confidence:.1%}) | Actual: {actual}")
        
        logger.info("✅ ML Model Training Complete!")
        logger.info(f"Model files saved:")
        logger.info(f"  - ml_model.pkl")
        logger.info(f"  - ml_scaler.pkl") 
        logger.info(f"  - ml_labels.pkl")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ ML training failed: {e}")
        return False

if __name__ == "__main__":
    success = main()
    if success:
        print("\n🚀 ML model training completed successfully!")
        print("You can now restart your trading bot to use the ML predictions.")
    else:
        print("\n💥 ML model training failed. Check the logs above.")