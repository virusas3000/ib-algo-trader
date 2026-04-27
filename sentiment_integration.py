#!/usr/bin/env python3
"""
Trading Bot Integration with Social Media Sentiment Analysis
Integrates sentiment signals with the existing IB algo trading bot

Author: Hermes Agent + virusas3000
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import threading
from sentiment_analyzer import TrendingStockAnalyzer, SentimentData
import config as cfg

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)

class SentimentTradingIntegration:
    """Integration between sentiment analysis and trading bot"""
    
    def __init__(self, config):
        self.config = config
        self.sentiment_analyzer = TrendingStockAnalyzer()
        self.sentiment_cache = {}
        self.sentiment_signals = {}
        self.last_sentiment_update = None
        self.sentiment_thread = None
        self.running = False
        
        # Sentiment trading parameters
        self.sentiment_config = {
            'enabled': config.get('SENTIMENT_ENABLED', True),
            'update_interval': config.get('SENTIMENT_UPDATE_INTERVAL', 300),  # 5 minutes
            'min_mentions': config.get('SENTIMENT_MIN_MENTIONS', 5),
            'min_confidence': config.get('SENTIMENT_MIN_CONFIDENCE', 0.3),
            'sentiment_weight': config.get('SENTIMENT_WEIGHT', 0.25),  # 25% of decision
            'position_size_multiplier': config.get('SENTIMENT_POSITION_MULTIPLIER', 0.5),
            'max_sentiment_positions': config.get('MAX_SENTIMENT_POSITIONS', 2)
        }
    
    def start_sentiment_monitoring(self):
        """Start background sentiment monitoring"""
        if not self.sentiment_config['enabled']:
            logger.info("Sentiment analysis disabled in config")
            return
        
        self.running = True
        self.sentiment_thread = threading.Thread(target=self._sentiment_monitoring_loop, daemon=True)
        self.sentiment_thread.start()
        logger.info("Started sentiment monitoring thread")
    
    def stop_sentiment_monitoring(self):
        """Stop sentiment monitoring"""
        self.running = False
        if self.sentiment_thread:
            self.sentiment_thread.join(timeout=5)
        logger.info("Stopped sentiment monitoring")
    
    def _sentiment_monitoring_loop(self):
        """Background loop for sentiment analysis"""
        while self.running:
            try:
                logger.info("Updating sentiment data...")
                start_time = time.time()
                
                # Get trending stocks with sentiment
                trending_stocks = self.sentiment_analyzer.get_top_trending_stocks(limit=20)
                sentiment_data = {symbol: data for symbol, data in trending_stocks}
                
                # Generate trading signals
                signals = self.sentiment_analyzer.generate_trading_signals(sentiment_data)
                
                # Update cache
                self.sentiment_cache = sentiment_data
                self.sentiment_signals = {signal['symbol']: signal for signal in signals}
                self.last_sentiment_update = datetime.now()
                
                elapsed = time.time() - start_time
                logger.info(f"Sentiment update completed in {elapsed:.1f}s - Found {len(sentiment_data)} stocks, {len(signals)} signals")
                
                # Log top trending stocks
                if trending_stocks:
                    top_5 = trending_stocks[:5]
                    symbols_str = ", ".join([f"${symbol} ({data.avg_sentiment:+.2f})" for symbol, data in top_5])
                    logger.info(f"Top trending: {symbols_str}")
                
            except Exception as e:
                logger.error(f"Sentiment analysis error: {e}")
            
            # Wait for next update
            time.sleep(self.sentiment_config['update_interval'])
    
    def get_sentiment_for_symbol(self, symbol: str) -> Optional[SentimentData]:
        """Get sentiment data for a specific symbol"""
        return self.sentiment_cache.get(symbol)
    
    def get_sentiment_signal(self, symbol: str) -> Optional[Dict]:
        """Get trading signal for a specific symbol based on sentiment"""
        return self.sentiment_signals.get(symbol)
    
    def should_consider_sentiment_entry(self, symbol: str, side: str) -> Dict:
        """
        Check if sentiment supports a potential entry
        Returns: {'should_enter': bool, 'confidence_boost': float, 'reason': str}
        """
        result = {'should_enter': False, 'confidence_boost': 0.0, 'reason': 'No sentiment data'}
        
        sentiment_data = self.get_sentiment_for_symbol(symbol)
        if not sentiment_data:
            return result
        
        signal = self.get_sentiment_signal(symbol)
        
        # Check minimum criteria
        if sentiment_data.total_mentions < self.sentiment_config['min_mentions']:
            result['reason'] = f'Insufficient mentions ({sentiment_data.total_mentions})'
            return result
        
        # Analyze sentiment alignment
        bullish_sentiment = sentiment_data.avg_sentiment > 0.2
        bearish_sentiment = sentiment_data.avg_sentiment < -0.2
        
        if side.upper() == 'BUY' and bullish_sentiment:
            confidence_boost = min(0.3, sentiment_data.avg_sentiment * self.sentiment_config['sentiment_weight'])
            result = {
                'should_enter': True,
                'confidence_boost': confidence_boost,
                'reason': f'Bullish sentiment: {sentiment_data.avg_sentiment:.2f} ({sentiment_data.total_mentions} mentions)'
            }
        elif side.upper() == 'SELL' and bearish_sentiment:
            confidence_boost = min(0.3, abs(sentiment_data.avg_sentiment) * self.sentiment_config['sentiment_weight'])
            result = {
                'should_enter': True,
                'confidence_boost': confidence_boost,
                'reason': f'Bearish sentiment: {sentiment_data.avg_sentiment:.2f} ({sentiment_data.total_mentions} mentions)'
            }
        else:
            sentiment_str = "neutral" if abs(sentiment_data.avg_sentiment) < 0.2 else ("bullish" if sentiment_data.avg_sentiment > 0 else "bearish")
            result['reason'] = f'Sentiment ({sentiment_str}: {sentiment_data.avg_sentiment:.2f}) conflicts with {side} direction'
        
        return result
    
    def get_sentiment_position_size_multiplier(self, symbol: str, base_size: int) -> int:
        """
        Adjust position size based on sentiment strength
        Returns adjusted position size
        """
        sentiment_data = self.get_sentiment_for_symbol(symbol)
        if not sentiment_data:
            return base_size
        
        # Calculate multiplier based on sentiment strength and social volume
        sentiment_strength = abs(sentiment_data.avg_sentiment)
        volume_factor = min(1.5, sentiment_data.total_mentions / 20.0)  # Max 1.5x for high volume
        
        multiplier = 1.0 + (sentiment_strength * volume_factor * self.sentiment_config['position_size_multiplier'])
        adjusted_size = int(base_size * multiplier)
        
        logger.info(f"${symbol} sentiment position sizing: {base_size} -> {adjusted_size} (multiplier: {multiplier:.2f})")
        return adjusted_size
    
    def get_pure_sentiment_opportunities(self) -> List[Dict]:
        """
        Get trading opportunities based purely on sentiment analysis
        These are stocks not in the main watchlist but trending on social media
        """
        opportunities = []
        
        if not self.sentiment_cache:
            return opportunities
        
        for symbol, sentiment_data in self.sentiment_cache.items():
            signal = self.sentiment_signals.get(symbol)
            
            if (signal and 
                signal['confidence'] >= self.sentiment_config['min_confidence'] and
                sentiment_data.total_mentions >= self.sentiment_config['min_mentions'] * 2):  # Higher threshold for pure sentiment plays
                
                opportunities.append({
                    'symbol': symbol,
                    'signal': signal['signal'],
                    'confidence': signal['confidence'],
                    'sentiment_score': sentiment_data.avg_sentiment,
                    'social_volume': sentiment_data.total_mentions,
                    'trend_momentum': sentiment_data.trend_momentum,
                    'reason': signal['reason'],
                    'entry_type': 'SENTIMENT_BREAKOUT'
                })
        
        # Sort by confidence and social volume
        opportunities.sort(key=lambda x: x['confidence'] * (x['social_volume'] / 10.0), reverse=True)
        return opportunities[:self.sentiment_config['max_sentiment_positions']]
    
    def log_sentiment_summary(self):
        """Log current sentiment summary"""
        if not self.sentiment_cache:
            logger.info("No sentiment data available")
            return
        
        total_stocks = len(self.sentiment_cache)
        bullish_count = sum(1 for data in self.sentiment_cache.values() if data.avg_sentiment > 0.2)
        bearish_count = sum(1 for data in self.sentiment_cache.values() if data.avg_sentiment < -0.2)
        signal_count = len(self.sentiment_signals)
        
        logger.info(f"Sentiment Summary: {total_stocks} stocks tracked, {bullish_count} bullish, {bearish_count} bearish, {signal_count} signals")
        
        # Log strongest signals
        if self.sentiment_signals:
            top_signals = sorted(self.sentiment_signals.values(), key=lambda x: x['confidence'], reverse=True)[:3]
            for i, signal in enumerate(top_signals, 1):
                logger.info(f"  #{i} ${signal['symbol']}: {signal['signal']} (conf: {signal['confidence']:.1%})")

class EnhancedStrategy:
    """Enhanced strategy class that incorporates sentiment analysis"""
    
    def __init__(self, original_strategy, sentiment_integration):
        self.original_strategy = original_strategy
        self.sentiment_integration = sentiment_integration
        
        # Forward the active_strategies attribute if it exists
        if hasattr(original_strategy, 'active_strategies'):
            self.active_strategies = original_strategy.active_strategies
    
    def __getattr__(self, name):
        """Forward any missing attributes to the original strategy"""
        return getattr(self.original_strategy, name)
    
    def check_entry(self, symbol, bars, current_time):
        """Enhanced entry check with sentiment analysis"""
        # Get original strategy signal
        original_signal = self.original_strategy.check_entry(symbol, bars, current_time)
        
        if not original_signal:
            return None
        
        # Check sentiment alignment
        sentiment_check = self.sentiment_integration.should_consider_sentiment_entry(
            symbol, original_signal.side
        )
        
        if sentiment_check['should_enter']:
            # Boost confidence with sentiment
            original_signal.confidence = min(1.0, original_signal.confidence + sentiment_check['confidence_boost'])
            original_signal.reason += f" + {sentiment_check['reason']}"
            logger.info(f"${symbol} sentiment boost: {sentiment_check['reason']}")
        else:
            # Reduce confidence if sentiment conflicts
            original_signal.confidence *= 0.7
            original_signal.reason += f" (sentiment: {sentiment_check['reason']})"
        
        return original_signal
    
    def get_position_size(self, symbol, signal, account_size, risk_per_trade):
        """Enhanced position sizing with sentiment adjustment"""
        # Get original position size
        base_size = self.original_strategy.get_position_size(symbol, signal, account_size, risk_per_trade)
        
        # Apply sentiment adjustment
        adjusted_size = self.sentiment_integration.get_sentiment_position_size_multiplier(symbol, base_size)
        
        return adjusted_size

def integrate_sentiment_with_trader(trader_instance):
    """
    Integration function to add sentiment analysis to existing trader
    Call this from your main trader.py file
    """
    logger.info("Integrating sentiment analysis with trading bot...")
    
    # Initialize sentiment integration
    sentiment_integration = SentimentTradingIntegration(cfg.__dict__)
    
    # Start sentiment monitoring
    sentiment_integration.start_sentiment_monitoring()
    
    # Enhance existing strategy
    if hasattr(trader_instance, 'strategy'):
        trader_instance.strategy = EnhancedStrategy(trader_instance.strategy, sentiment_integration)
        logger.info("Enhanced strategy with sentiment analysis")
    
    # Add sentiment integration to trader instance
    trader_instance.sentiment_integration = sentiment_integration
    
    # Add method to check pure sentiment opportunities
    def check_sentiment_opportunities(self):
        """Check for pure sentiment trading opportunities"""
        opportunities = self.sentiment_integration.get_pure_sentiment_opportunities()
        
        for opp in opportunities:
            logger.info(f"Sentiment opportunity: ${opp['symbol']} {opp['signal']} "
                       f"(conf: {opp['confidence']:.1%}, volume: {opp['social_volume']})")
            
            # Here you could add logic to actually trade these opportunities
            # For now, just log them
    
    trader_instance.check_sentiment_opportunities = check_sentiment_opportunities.__get__(trader_instance)
    
    logger.info("✅ Sentiment analysis integration complete!")
    return sentiment_integration

# Example usage function for testing
def test_sentiment_integration():
    """Test the sentiment analysis system"""
    print("🧪 Testing Sentiment Analysis Integration")
    print("=" * 50)
    
    # Mock config
    config = {
        'SENTIMENT_ENABLED': True,
        'SENTIMENT_UPDATE_INTERVAL': 60,  # 1 minute for testing
        'SENTIMENT_MIN_MENTIONS': 3,
        'SENTIMENT_MIN_CONFIDENCE': 0.2
    }
    
    # Initialize
    integration = SentimentTradingIntegration(config)
    
    # Start monitoring (will run once)
    integration._sentiment_monitoring_loop()
    
    # Test various functions
    print("\n📊 Current Sentiment Summary:")
    integration.log_sentiment_summary()
    
    print("\n🎯 Pure Sentiment Opportunities:")
    opportunities = integration.get_pure_sentiment_opportunities()
    for i, opp in enumerate(opportunities[:5], 1):
        print(f"{i}. ${opp['symbol']} - {opp['signal']} (conf: {opp['confidence']:.1%})")
        print(f"   {opp['reason']}")
    
    if opportunities:
        # Test sentiment checks for top opportunity
        top_opp = opportunities[0]
        side = 'BUY' if 'BUY' in top_opp['signal'] else 'SELL'
        
        print(f"\n🔍 Testing sentiment check for ${top_opp['symbol']} {side}:")
        check_result = integration.should_consider_sentiment_entry(top_opp['symbol'], side)
        print(f"Should enter: {check_result['should_enter']}")
        print(f"Confidence boost: {check_result['confidence_boost']:+.2f}")
        print(f"Reason: {check_result['reason']}")

if __name__ == "__main__":
    test_sentiment_integration()