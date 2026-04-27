#!/usr/bin/env python3
"""
ML Model Integration with IB Algo Trading Bot
Integrates machine learning signals with existing technical and sentiment strategies

Author: Hermes Agent + virusas3000
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'ml_trading_bot'))

import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import config as cfg

# Import ML components
from ml_model import MLSignalModel, TrainingDataCollector, label_from_forward_return
from ml_indicators import compute_indicators as compute_ml_indicators

logger = logging.getLogger(__name__)

class MLTradingIntegration:
    """Integration between ML models and trading bot"""
    
    def __init__(self, config):
        self.config = config
        self.enabled = config.get('ML_ENABLED', True)
        
        # ML model configuration
        self.model_config = {
            'model_path': config.get('ML_MODEL_PATH', 'ml_model.pkl'),
            'scaler_path': config.get('ML_SCALER_PATH', 'ml_scaler.pkl'), 
            'label_path': config.get('ML_LABELS_PATH', 'ml_labels.pkl'),
            'training_data_path': config.get('ML_TRAINING_DATA_PATH', 'ml_training_data.csv'),
            'min_confidence': config.get('ML_MIN_CONFIDENCE', 0.65),
            'retrain_interval_hours': config.get('ML_RETRAIN_INTERVAL', 24),
            'min_training_samples': config.get('ML_MIN_TRAINING_SAMPLES', 500),
        }
        
        # Feature columns for ML model
        self.feature_cols = [
            'sma_20', 'sma_50', 'sma_200', 'ema_12', 'ema_26',
            'rsi_14', 'macd', 'macd_signal', 'macd_histogram',
            'bb_upper', 'bb_lower', 'bb_middle', 'bb_percent',
            'atr_14', 'stoch_k', 'stoch_d', 'cci_20',
            'williams_r', 'volume_sma', 'price_volume_trend',
            'close_sma20_ratio', 'volume_ratio', 'volatility'
        ]
        
        # Initialize components
        self.ml_model = MLSignalModel(
            model_path=self.model_config['model_path'],
            scaler_path=self.model_config['scaler_path'], 
            label_path=self.model_config['label_path'],
            feature_cols=self.feature_cols,
            min_confidence=self.model_config['min_confidence']
        )
        
        self.training_collector = TrainingDataCollector(
            path=self.model_config['training_data_path']
        )
        
        self.last_retrain_time = None
        logger.info(f"ML Integration initialized - Model loaded: {self.ml_model.model is not None}")
    
    def get_ml_signal(self, symbol: str, bars: pd.DataFrame) -> Optional[Dict]:
        """
        Get ML trading signal for a symbol
        Returns: {'signal': str, 'confidence': float, 'reason': str} or None
        """
        if not self.enabled or self.ml_model.model is None:
            return None
        
        try:
            # Compute indicators needed for ML model
            enriched_bars = self._compute_ml_features(bars.copy())
            
            # Record data for future retraining
            if len(enriched_bars) > 0:
                self.training_collector.record(symbol, enriched_bars, self.feature_cols)
            
            # Get ML prediction
            signal, confidence = self.ml_model.predict(enriched_bars)
            
            if signal == 'HOLD' or confidence < self.model_config['min_confidence']:
                return None
            
            return {
                'signal': signal,
                'confidence': confidence,
                'reason': f'ML model prediction (conf: {confidence:.1%})',
                'type': 'ML_SIGNAL'
            }
            
        except Exception as e:
            logger.error(f"ML signal error for {symbol}: {e}")
            return None
    
    def _compute_ml_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute all ML features for the dataframe"""
        try:
            # Use the compute_indicators from ml_trading_bot
            df_with_indicators = compute_ml_indicators(df)
            
            # Add some additional features for ML
            if len(df_with_indicators) >= 20:
                df_with_indicators['close_sma20_ratio'] = df_with_indicators['close'] / df_with_indicators['sma_20']
                df_with_indicators['volume_ratio'] = df_with_indicators['volume'] / df_with_indicators['volume_sma']
                df_with_indicators['volatility'] = df_with_indicators['close'].rolling(10).std() / df_with_indicators['close'].rolling(10).mean()
            else:
                # Fill with default values if not enough data
                df_with_indicators['close_sma20_ratio'] = 1.0
                df_with_indicators['volume_ratio'] = 1.0  
                df_with_indicators['volatility'] = 0.02
            
            return df_with_indicators
            
        except Exception as e:
            logger.error(f"Error computing ML features: {e}")
            return df
    
    def should_consider_ml_entry(self, symbol: str, side: str, bars: pd.DataFrame) -> Dict:
        """
        Check if ML model supports a potential entry
        Returns: {'should_enter': bool, 'confidence_boost': float, 'reason': str}
        """
        result = {'should_enter': False, 'confidence_boost': 0.0, 'reason': 'No ML signal'}
        
        ml_signal = self.get_ml_signal(symbol, bars)
        if not ml_signal:
            return result
        
        # Check if ML signal aligns with proposed trade direction
        if ((side.upper() == 'BUY' and ml_signal['signal'] == 'BUY') or
            (side.upper() == 'SELL' and ml_signal['signal'] == 'SELL')):
            
            confidence_boost = min(0.25, ml_signal['confidence'] * 0.3)  # Max 25% boost
            result = {
                'should_enter': True,
                'confidence_boost': confidence_boost,
                'reason': f"ML supports {side}: {ml_signal['reason']}"
            }
        else:
            result['reason'] = f"ML conflicts: predicts {ml_signal['signal']} vs {side} direction"
        
        return result
    
    def get_pure_ml_opportunities(self, symbols: List[str], bars_dict: Dict[str, pd.DataFrame]) -> List[Dict]:
        """
        Get trading opportunities based purely on ML predictions
        """
        opportunities = []
        
        if not self.enabled or self.ml_model.model is None:
            return opportunities
        
        for symbol in symbols:
            if symbol not in bars_dict:
                continue
                
            try:
                bars = bars_dict[symbol]
                ml_signal = self.get_ml_signal(symbol, bars)
                
                if ml_signal and ml_signal['confidence'] >= self.model_config['min_confidence']:
                    opportunities.append({
                        'symbol': symbol,
                        'signal': ml_signal['signal'],
                        'confidence': ml_signal['confidence'],
                        'reason': ml_signal['reason'],
                        'entry_type': 'ML_PURE',
                        'current_price': float(bars.iloc[-1]['close']) if len(bars) > 0 else 0.0
                    })
                    
            except Exception as e:
                logger.error(f"Error getting ML opportunity for {symbol}: {e}")
        
        # Sort by confidence
        opportunities.sort(key=lambda x: x['confidence'], reverse=True)
        return opportunities[:5]  # Top 5 ML opportunities
    
    def should_retrain_model(self) -> bool:
        """Check if model should be retrained"""
        if not self.enabled:
            return False
            
        # Check if enough time has passed
        if self.last_retrain_time:
            hours_since_retrain = (datetime.now() - self.last_retrain_time).total_seconds() / 3600
            if hours_since_retrain < self.model_config['retrain_interval_hours']:
                return False
        
        # Check if we have enough new training data
        training_path = Path(self.model_config['training_data_path'])
        if not training_path.exists():
            return False
            
        try:
            df = pd.read_csv(training_path)
            return len(df) >= self.model_config['min_training_samples']
        except Exception as e:
            logger.error(f"Error checking training data: {e}")
            return False
    
    def retrain_model(self) -> bool:
        """Retrain the ML model with collected data"""
        if not self.enabled:
            return False
            
        logger.info("Starting ML model retraining...")
        
        try:
            # Flush any remaining data
            self.training_collector.flush()
            
            # Load and label training data
            labeled_df = self.training_collector.load_and_label(self.feature_cols)
            
            if labeled_df is None or len(labeled_df) < self.model_config['min_training_samples']:
                logger.warning(f"Insufficient training data for retraining: {len(labeled_df) if labeled_df is not None else 0}")
                return False
            
            # Train the model
            self.ml_model.train(labeled_df)
            self.last_retrain_time = datetime.now()
            
            # Log training results
            class_counts = labeled_df['outcome'].value_counts().to_dict()
            logger.info(f"ML model retrained successfully with {len(labeled_df)} samples")
            logger.info(f"Class distribution: {class_counts}")
            
            return True
            
        except Exception as e:
            logger.error(f"ML model retraining failed: {e}")
            return False
    
    def get_ml_position_size_multiplier(self, symbol: str, base_size: int, bars: pd.DataFrame) -> int:
        """
        Adjust position size based on ML model confidence
        """
        if not self.enabled:
            return base_size
            
        ml_signal = self.get_ml_signal(symbol, bars)
        if not ml_signal:
            return base_size
        
        # Increase position size for high confidence ML signals
        confidence_multiplier = 1.0 + (ml_signal['confidence'] - 0.5) * 0.5  # Up to 1.25x for 100% confidence
        adjusted_size = int(base_size * confidence_multiplier)
        
        logger.info(f"${symbol} ML position sizing: {base_size} -> {adjusted_size} (ML conf: {ml_signal['confidence']:.1%})")
        return adjusted_size
    
    def log_ml_summary(self):
        """Log current ML model status"""
        status = "ENABLED" if self.enabled else "DISABLED"
        model_status = "TRAINED" if self.ml_model.model is not None else "UNTRAINED"
        
        logger.info(f"ML Integration Status: {status} | Model: {model_status}")
        
        if self.enabled and self.ml_model.model is not None:
            # Check training data size
            training_path = Path(self.model_config['training_data_path'])
            if training_path.exists():
                try:
                    df = pd.read_csv(training_path)
                    logger.info(f"Training data samples: {len(df)}")
                    if self.should_retrain_model():
                        logger.info("🔄 Model ready for retraining")
                except Exception:
                    pass
            
            # Log last retrain time
            if self.last_retrain_time:
                hours_ago = (datetime.now() - self.last_retrain_time).total_seconds() / 3600
                logger.info(f"Last retrained: {hours_ago:.1f}h ago")


class EnhancedMLStrategy:
    """Enhanced strategy class that incorporates ML predictions"""
    
    def __init__(self, original_strategy, ml_integration, sentiment_integration=None):
        self.original_strategy = original_strategy
        self.ml_integration = ml_integration
        self.sentiment_integration = sentiment_integration
        
        # Forward the active_strategies attribute if it exists
        if hasattr(original_strategy, 'active_strategies'):
            self.active_strategies = original_strategy.active_strategies
    
    def __getattr__(self, name):
        """Forward any missing attributes to the original strategy"""
        return getattr(self.original_strategy, name)
    
    def check_entry(self, symbol, bars, current_time):
        """Enhanced entry check with ML and sentiment analysis"""
        # Get original strategy signal
        original_signal = self.original_strategy.check_entry(symbol, bars, current_time)
        
        if not original_signal:
            # Check for pure ML opportunities if no technical signal
            ml_signal = self.ml_integration.get_ml_signal(symbol, bars)
            if ml_signal and ml_signal['confidence'] >= 0.8:  # High confidence threshold for pure ML
                # Convert ML signal to strategy signal format
                from strategy import Signal, Side
                side = Side.BUY if ml_signal['signal'] == 'BUY' else Side.SELL
                
                logger.info(f"${symbol} Pure ML signal: {ml_signal['signal']} (conf: {ml_signal['confidence']:.1%})")
                return Signal(
                    side=side,
                    confidence=ml_signal['confidence'],
                    reason=f"Pure ML: {ml_signal['reason']}",
                    price=float(bars.iloc[-1]['close'])
                )
            return None
        
        # Enhance existing signal with ML
        ml_check = self.ml_integration.should_consider_ml_entry(symbol, original_signal.side, bars)
        
        # Apply ML enhancement
        if ml_check['should_enter']:
            original_signal.confidence = min(1.0, original_signal.confidence + ml_check['confidence_boost'])
            original_signal.reason += f" + {ml_check['reason']}"
            logger.info(f"${symbol} ML enhancement: {ml_check['reason']}")
        else:
            # Reduce confidence if ML conflicts
            original_signal.confidence *= 0.8
            original_signal.reason += f" (ML: {ml_check['reason']})"
        
        # Apply sentiment if available
        if self.sentiment_integration:
            sentiment_check = self.sentiment_integration.should_consider_sentiment_entry(symbol, original_signal.side)
            if sentiment_check['should_enter']:
                original_signal.confidence = min(1.0, original_signal.confidence + sentiment_check['confidence_boost'] * 0.5)
                original_signal.reason += f" + {sentiment_check['reason']}"
            else:
                original_signal.confidence *= 0.9
                
        return original_signal
    
    def get_position_size(self, symbol, signal, account_size, risk_per_trade, bars):
        """Enhanced position sizing with ML and sentiment adjustments"""
        # Get base position size
        base_size = self.original_strategy.get_position_size(symbol, signal, account_size, risk_per_trade)
        
        # Apply ML adjustment
        adjusted_size = self.ml_integration.get_ml_position_size_multiplier(symbol, base_size, bars)
        
        # Apply sentiment adjustment if available
        if self.sentiment_integration:
            adjusted_size = self.sentiment_integration.get_sentiment_position_size_multiplier(symbol, adjusted_size)
        
        return adjusted_size


def integrate_ml_with_trader(trader_instance, sentiment_integration=None):
    """
    Integration function to add ML to existing trader
    Call this from your main trader.py file after sentiment integration
    """
    logger.info("Integrating ML analysis with trading bot...")
    
    # Initialize ML integration
    ml_integration = MLTradingIntegration(cfg.__dict__)
    
    # Enhance existing strategy
    if hasattr(trader_instance, 'strategy'):
        trader_instance.strategy = EnhancedMLStrategy(
            trader_instance.strategy, 
            ml_integration,
            sentiment_integration
        )
        logger.info("Enhanced strategy with ML analysis")
    
    # Add ML integration to trader instance
    trader_instance.ml_integration = ml_integration
    
    # Add methods to check ML opportunities and manage retraining
    def check_ml_opportunities(self):
        """Check for pure ML trading opportunities"""
        # This would need access to current market data - implement based on your data structure
        logger.info("ML opportunity scanning not yet implemented - needs market data access")
    
    def manage_ml_retraining(self):
        """Handle automatic model retraining"""
        if self.ml_integration.should_retrain_model():
            logger.info("🔄 Starting automatic ML model retraining...")
            success = self.ml_integration.retrain_model()
            if success:
                logger.info("✅ ML model retrained successfully")
                # Send notification if available
                if hasattr(self, 'telegram') and self.telegram:
                    self.telegram.send_message("🤖 ML trading model retrained successfully!")
            else:
                logger.warning("❌ ML model retraining failed")
    
    trader_instance.check_ml_opportunities = check_ml_opportunities.__get__(trader_instance)
    trader_instance.manage_ml_retraining = manage_ml_retraining.__get__(trader_instance)
    
    logger.info("✅ ML integration complete!")
    return ml_integration


# Test function
def test_ml_integration():
    """Test the ML integration system"""
    print("🧪 Testing ML Integration")
    print("=" * 50)
    
    # Mock config
    config = {
        'ML_ENABLED': True,
        'ML_MIN_CONFIDENCE': 0.6,
        'ML_RETRAIN_INTERVAL': 1  # 1 hour for testing
    }
    
    # Initialize
    ml_integration = MLTradingIntegration(config)
    
    # Test status
    print("\n📊 ML Integration Status:")
    ml_integration.log_ml_summary()
    
    # Test retrain check
    print(f"\n🔄 Should retrain: {ml_integration.should_retrain_model()}")
    

if __name__ == "__main__":
    test_ml_integration()