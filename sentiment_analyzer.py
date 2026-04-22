#!/usr/bin/env python3
"""
Social Media Sentiment Analysis & Trending Stock Discovery
Integrates with IB Algo Trading Bot

Real-time sentiment analysis from:
- Twitter/X posts and trends
- Financial blogs and RSS feeds
- News sentiment analysis
- Reddit trending stocks
- Social media buzz detection

Author: Hermes Agent + virusas3000
"""

import json
import re
import subprocess
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from collections import defaultdict, Counter
import requests

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class StockMention:
    """A mention of a stock symbol with sentiment"""
    symbol: str
    mentions: int
    sentiment_score: float  # -1.0 to 1.0
    confidence: float      # 0.0 to 1.0
    sources: List[str]     # ['twitter', 'reddit', 'blog']
    trending_score: float  # Composite score
    last_updated: datetime

@dataclass
class SentimentData:
    """Aggregated sentiment data for a stock"""
    symbol: str
    total_mentions: int
    bullish_mentions: int
    bearish_mentions: int
    neutral_mentions: int
    avg_sentiment: float
    trend_momentum: float  # Rate of change
    top_keywords: List[str]
    news_events: List[str]
    social_volume: int
    last_updated: datetime

class StockSymbolExtractor:
    """Extract stock symbols from text using regex patterns"""
    
    def __init__(self):
        # Common stock symbol patterns
        self.cashtag_pattern = re.compile(r'\$([A-Z]{1,5})\b')
        self.mention_pattern = re.compile(r'\b([A-Z]{2,5})\s+(?:stock|calls|puts|options|bullish|bearish|moon|dump)\b', re.IGNORECASE)
        self.ticker_pattern = re.compile(r'\b(?:ticker|symbol):\s*([A-Z]{1,5})\b', re.IGNORECASE)
        
        # Common false positives to filter out
        self.blacklist = {
            'CEO', 'IPO', 'SEC', 'FDA', 'API', 'AI', 'ML', 'IT', 'HR', 'PR', 'UI', 'UX',
            'USA', 'USD', 'NYC', 'LA', 'SF', 'UK', 'EU', 'US', 'AM', 'PM', 'EST', 'PST',
            'NEW', 'ALL', 'GET', 'BUY', 'SELL', 'HOLD', 'UP', 'DOWN', 'TOP', 'BOT',
            'HOT', 'BIG', 'HUGE', 'BEST', 'WORST', 'FAST', 'SLOW', 'HIGH', 'LOW'
        }
    
    def extract_symbols(self, text: str) -> List[str]:
        """Extract stock symbols from text"""
        symbols = set()
        
        # Extract cashtags ($AAPL)
        for match in self.cashtag_pattern.finditer(text):
            symbols.add(match.group(1).upper())
        
        # Extract mentioned tickers with context
        for match in self.mention_pattern.finditer(text):
            symbol = match.group(1).upper()
            if symbol not in self.blacklist and len(symbol) <= 5:
                symbols.add(symbol)
        
        # Extract explicit ticker mentions
        for match in self.ticker_pattern.finditer(text):
            symbols.add(match.group(1).upper())
        
        return list(symbols)

class SentimentAnalyzer:
    """Analyze sentiment of text using simple keyword-based approach"""
    
    def __init__(self):
        self.bullish_keywords = {
            'moon', 'rocket', 'bullish', 'calls', 'buy', 'long', 'pump', 'rally',
            'breakout', 'surge', 'gains', 'profit', 'bull', 'uptrend', 'strong',
            'momentum', 'squeeze', 'diamond hands', 'hodl', 'btfd', 'lambo'
        }
        
        self.bearish_keywords = {
            'dump', 'crash', 'bearish', 'puts', 'sell', 'short', 'tank', 'drop',
            'fall', 'decline', 'loss', 'bear', 'downtrend', 'weak', 'collapse',
            'panic', 'fear', 'red', 'bleeding', 'rekt', 'baghold'
        }
    
    def analyze_sentiment(self, text: str) -> Tuple[float, float]:
        """
        Analyze sentiment of text
        Returns: (sentiment_score, confidence)
        sentiment_score: -1.0 (bearish) to 1.0 (bullish)
        confidence: 0.0 to 1.0
        """
        text_lower = text.lower()
        
        bullish_count = sum(1 for word in self.bullish_keywords if word in text_lower)
        bearish_count = sum(1 for word in self.bearish_keywords if word in text_lower)
        
        total_sentiment_words = bullish_count + bearish_count
        
        if total_sentiment_words == 0:
            return 0.0, 0.0  # Neutral, no confidence
        
        sentiment_score = (bullish_count - bearish_count) / total_sentiment_words
        confidence = min(1.0, total_sentiment_words / 3.0)  # More words = higher confidence
        
        return sentiment_score, confidence

class TwitterSentimentTracker:
    """Track sentiment from Twitter/X using x-cli"""
    
    def __init__(self):
        self.symbol_extractor = StockSymbolExtractor()
        self.sentiment_analyzer = SentimentAnalyzer()
    
    def check_x_cli_available(self) -> bool:
        """Check if x-cli is installed and configured"""
        try:
            result = subprocess.run(['x-cli', '--help'], capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def search_stock_tweets(self, query: str, max_tweets: int = 50) -> List[Dict]:
        """Search for tweets about stocks"""
        if not self.check_x_cli_available():
            logger.warning("x-cli not available, skipping Twitter search")
            return []
        
        try:
            cmd = ['x-cli', '-j', 'tweet', 'search', query, '--max', str(max_tweets)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.error(f"x-cli search failed: {result.stderr}")
                return []
            
            tweets = json.loads(result.stdout) if result.stdout.strip() else []
            return tweets if isinstance(tweets, list) else []
            
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            logger.error(f"Twitter search error: {e}")
            return []
    
    def analyze_tweets_for_stocks(self, tweets: List[Dict]) -> Dict[str, StockMention]:
        """Analyze tweets and extract stock sentiment"""
        stock_mentions = defaultdict(lambda: {'mentions': 0, 'sentiment_sum': 0.0, 'confidence_sum': 0.0, 'sources': []})
        
        for tweet in tweets:
            text = tweet.get('text', '')
            symbols = self.symbol_extractor.extract_symbols(text)
            sentiment, confidence = self.sentiment_analyzer.analyze_sentiment(text)
            
            for symbol in symbols:
                stock_mentions[symbol]['mentions'] += 1
                stock_mentions[symbol]['sentiment_sum'] += sentiment
                stock_mentions[symbol]['confidence_sum'] += confidence
                stock_mentions[symbol]['sources'].append('twitter')
        
        # Convert to StockMention objects
        mentions = {}
        for symbol, data in stock_mentions.items():
            avg_sentiment = data['sentiment_sum'] / data['mentions'] if data['mentions'] > 0 else 0.0
            avg_confidence = data['confidence_sum'] / data['mentions'] if data['mentions'] > 0 else 0.0
            trending_score = data['mentions'] * abs(avg_sentiment) * avg_confidence
            
            mentions[symbol] = StockMention(
                symbol=symbol,
                mentions=data['mentions'],
                sentiment_score=avg_sentiment,
                confidence=avg_confidence,
                sources=['twitter'],
                trending_score=trending_score,
                last_updated=datetime.now()
            )
        
        return mentions

class BlogSentimentTracker:
    """Track sentiment from financial blogs using blogwatcher-cli"""
    
    def __init__(self):
        self.symbol_extractor = StockSymbolExtractor()
        self.sentiment_analyzer = SentimentAnalyzer()
        self.financial_blogs = [
            ("Seeking Alpha", "https://seekingalpha.com"),
            ("MarketWatch", "https://www.marketwatch.com"),
            ("Benzinga", "https://www.benzinga.com"),
            ("Yahoo Finance", "https://finance.yahoo.com"),
            ("The Motley Fool", "https://www.fool.com"),
            ("Zacks", "https://www.zacks.com"),
            ("TheStreet", "https://www.thestreet.com"),
            ("InvestorPlace", "https://investorplace.com")
        ]
    
    def check_blogwatcher_available(self) -> bool:
        """Check if blogwatcher-cli is installed"""
        try:
            result = subprocess.run(['blogwatcher-cli', '--help'], capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def setup_financial_blogs(self):
        """Add financial blogs to blogwatcher if not already added"""
        if not self.check_blogwatcher_available():
            logger.warning("blogwatcher-cli not available, installing...")
            try:
                # Install blogwatcher-cli for macOS
                subprocess.run(['curl', '-sL', 
                               'https://github.com/JulienTant/blogwatcher-cli/releases/latest/download/blogwatcher-cli_darwin_arm64.tar.gz'], 
                              capture_output=True, check=True)
            except subprocess.CalledProcessError:
                logger.error("Failed to install blogwatcher-cli")
                return
        
        for name, url in self.financial_blogs:
            try:
                cmd = ['blogwatcher-cli', 'add', name, url]
                subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                logger.info(f"Added blog: {name}")
            except subprocess.TimeoutExpired:
                logger.warning(f"Timeout adding blog: {name}")
    
    def scan_blogs_for_updates(self) -> List[Dict]:
        """Scan all blogs for new articles"""
        if not self.check_blogwatcher_available():
            return []
        
        try:
            # Scan for new articles
            subprocess.run(['blogwatcher-cli', 'scan'], capture_output=True, timeout=60)
            
            # Get unread articles
            result = subprocess.run(['blogwatcher-cli', 'articles'], capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                return []
            
            # Parse the output (blogwatcher doesn't have JSON output, so we parse text)
            articles = self._parse_articles_output(result.stdout)
            return articles
            
        except subprocess.TimeoutExpired:
            logger.error("Timeout scanning blogs")
            return []
    
    def _parse_articles_output(self, output: str) -> List[Dict]:
        """Parse blogwatcher text output into article data"""
        articles = []
        lines = output.split('\n')
        
        current_article = {}
        for line in lines:
            line = line.strip()
            if line.startswith('[') and ']' in line:
                # New article ID line
                if current_article:
                    articles.append(current_article)
                current_article = {'id': line.split(']')[0][1:], 'title': line.split(']')[1].strip()}
            elif line.startswith('Blog:'):
                current_article['blog'] = line.replace('Blog:', '').strip()
            elif line.startswith('URL:'):
                current_article['url'] = line.replace('URL:', '').strip()
            elif line.startswith('Published:'):
                current_article['published'] = line.replace('Published:', '').strip()
        
        if current_article:
            articles.append(current_article)
        
        return articles

class RedditSentimentTracker:
    """Track sentiment from Reddit using web scraping (no API key needed)"""
    
    def __init__(self):
        self.symbol_extractor = StockSymbolExtractor()
        self.sentiment_analyzer = SentimentAnalyzer()
        self.subreddits = ['wallstreetbets', 'stocks', 'investing', 'SecurityAnalysis', 'ValueInvesting']
    
    def get_trending_stocks_from_reddit(self) -> Dict[str, StockMention]:
        """Get trending stocks from Reddit using web scraping"""
        stock_mentions = defaultdict(lambda: {'mentions': 0, 'sentiment_sum': 0.0, 'confidence_sum': 0.0})
        
        for subreddit in self.subreddits:
            try:
                # Use Reddit's JSON API (no auth needed for public posts)
                url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=25"
                headers = {'User-Agent': 'TradingBot/1.0'}
                
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    posts = data.get('data', {}).get('children', [])
                    
                    for post in posts:
                        post_data = post.get('data', {})
                        title = post_data.get('title', '')
                        selftext = post_data.get('selftext', '')
                        
                        full_text = f"{title} {selftext}"
                        symbols = self.symbol_extractor.extract_symbols(full_text)
                        sentiment, confidence = self.sentiment_analyzer.analyze_sentiment(full_text)
                        
                        for symbol in symbols:
                            stock_mentions[symbol]['mentions'] += 1
                            stock_mentions[symbol]['sentiment_sum'] += sentiment
                            stock_mentions[symbol]['confidence_sum'] += confidence
                
            except Exception as e:
                logger.error(f"Error scraping Reddit r/{subreddit}: {e}")
        
        # Convert to StockMention objects
        mentions = {}
        for symbol, data in stock_mentions.items():
            if data['mentions'] >= 2:  # Filter out noise
                avg_sentiment = data['sentiment_sum'] / data['mentions']
                avg_confidence = data['confidence_sum'] / data['mentions']
                trending_score = data['mentions'] * abs(avg_sentiment) * avg_confidence
                
                mentions[symbol] = StockMention(
                    symbol=symbol,
                    mentions=data['mentions'],
                    sentiment_score=avg_sentiment,
                    confidence=avg_confidence,
                    sources=['reddit'],
                    trending_score=trending_score,
                    last_updated=datetime.now()
                )
        
        return mentions

class TrendingStockAnalyzer:
    """Main class that aggregates sentiment from all sources"""
    
    def __init__(self):
        self.twitter_tracker = TwitterSentimentTracker()
        self.blog_tracker = BlogSentimentTracker()
        self.reddit_tracker = RedditSentimentTracker()
        self.historical_data = {}  # Store historical sentiment data
    
    def get_trending_stocks(self) -> Dict[str, SentimentData]:
        """Get trending stocks from all sources with sentiment analysis"""
        all_mentions = defaultdict(lambda: {
            'total_mentions': 0,
            'bullish_mentions': 0,
            'bearish_mentions': 0,
            'neutral_mentions': 0,
            'sentiment_sum': 0.0,
            'confidence_sum': 0.0,
            'sources': set(),
            'keywords': [],
            'news_events': []
        })
        
        # Get Twitter sentiment
        logger.info("Analyzing Twitter sentiment...")
        try:
            twitter_queries = ['$SPY OR $QQQ OR $AAPL OR $TSLA', 'stock market', 'trading', '$NVDA OR $AMZN']
            for query in twitter_queries:
                tweets = self.twitter_tracker.search_stock_tweets(query, max_tweets=20)
                mentions = self.twitter_tracker.analyze_tweets_for_stocks(tweets)
                
                for symbol, mention in mentions.items():
                    data = all_mentions[symbol]
                    data['total_mentions'] += mention.mentions
                    data['sentiment_sum'] += mention.sentiment_score * mention.mentions
                    data['confidence_sum'] += mention.confidence * mention.mentions
                    data['sources'].add('twitter')
                    
                    if mention.sentiment_score > 0.1:
                        data['bullish_mentions'] += mention.mentions
                    elif mention.sentiment_score < -0.1:
                        data['bearish_mentions'] += mention.mentions
                    else:
                        data['neutral_mentions'] += mention.mentions
        except Exception as e:
            logger.error(f"Twitter analysis failed: {e}")
        
        # Get Reddit sentiment
        logger.info("Analyzing Reddit sentiment...")
        try:
            reddit_mentions = self.reddit_tracker.get_trending_stocks_from_reddit()
            
            for symbol, mention in reddit_mentions.items():
                data = all_mentions[symbol]
                data['total_mentions'] += mention.mentions
                data['sentiment_sum'] += mention.sentiment_score * mention.mentions
                data['confidence_sum'] += mention.confidence * mention.mentions
                data['sources'].add('reddit')
                
                if mention.sentiment_score > 0.1:
                    data['bullish_mentions'] += mention.mentions
                elif mention.sentiment_score < -0.1:
                    data['bearish_mentions'] += mention.mentions
                else:
                    data['neutral_mentions'] += mention.mentions
        except Exception as e:
            logger.error(f"Reddit analysis failed: {e}")
        
        # Convert to SentimentData objects
        sentiment_data = {}
        for symbol, data in all_mentions.items():
            if data['total_mentions'] >= 3:  # Minimum threshold
                avg_sentiment = data['sentiment_sum'] / data['total_mentions'] if data['total_mentions'] > 0 else 0.0
                
                # Calculate trend momentum (compare with historical data)
                trend_momentum = 0.0
                if symbol in self.historical_data:
                    prev_mentions = self.historical_data[symbol].get('total_mentions', 0)
                    trend_momentum = (data['total_mentions'] - prev_mentions) / max(prev_mentions, 1)
                
                sentiment_data[symbol] = SentimentData(
                    symbol=symbol,
                    total_mentions=data['total_mentions'],
                    bullish_mentions=data['bullish_mentions'],
                    bearish_mentions=data['bearish_mentions'],
                    neutral_mentions=data['neutral_mentions'],
                    avg_sentiment=avg_sentiment,
                    trend_momentum=trend_momentum,
                    top_keywords=data['keywords'][:10],
                    news_events=data['news_events'],
                    social_volume=data['total_mentions'],
                    last_updated=datetime.now()
                )
        
        # Update historical data
        self.historical_data.update({symbol: {'total_mentions': data.total_mentions} for symbol, data in sentiment_data.items()})
        
        return sentiment_data
    
    def get_top_trending_stocks(self, limit: int = 10) -> List[Tuple[str, SentimentData]]:
        """Get top trending stocks sorted by social volume and sentiment"""
        sentiment_data = self.get_trending_stocks()
        
        # Sort by a composite score: mentions * abs(sentiment) * (1 + trend_momentum)
        sorted_stocks = sorted(
            sentiment_data.items(),
            key=lambda x: x[1].total_mentions * abs(x[1].avg_sentiment) * (1 + max(0, x[1].trend_momentum)),
            reverse=True
        )
        
        return sorted_stocks[:limit]
    
    def generate_trading_signals(self, sentiment_data: Dict[str, SentimentData]) -> List[Dict]:
        """Generate trading signals based on sentiment analysis"""
        signals = []
        
        for symbol, data in sentiment_data.items():
            # Strong bullish signal
            if (data.avg_sentiment > 0.3 and 
                data.total_mentions > 10 and 
                data.bullish_mentions > data.bearish_mentions * 2 and
                data.trend_momentum > 0.5):
                
                signals.append({
                    'symbol': symbol,
                    'signal': 'STRONG_BUY',
                    'confidence': min(1.0, data.total_mentions / 50.0),
                    'reason': f'High bullish sentiment ({data.avg_sentiment:.2f}) with {data.total_mentions} mentions',
                    'social_volume': data.total_mentions,
                    'sentiment_score': data.avg_sentiment,
                    'timestamp': datetime.now()
                })
            
            # Strong bearish signal
            elif (data.avg_sentiment < -0.3 and 
                  data.total_mentions > 10 and 
                  data.bearish_mentions > data.bullish_mentions * 2 and
                  data.trend_momentum > 0.5):
                
                signals.append({
                    'symbol': symbol,
                    'signal': 'STRONG_SELL',
                    'confidence': min(1.0, data.total_mentions / 50.0),
                    'reason': f'High bearish sentiment ({data.avg_sentiment:.2f}) with {data.total_mentions} mentions',
                    'social_volume': data.total_mentions,
                    'sentiment_score': data.avg_sentiment,
                    'timestamp': datetime.now()
                })
            
            # Moderate signals
            elif data.total_mentions > 5 and abs(data.avg_sentiment) > 0.2:
                signal_type = 'BUY' if data.avg_sentiment > 0 else 'SELL'
                signals.append({
                    'symbol': symbol,
                    'signal': signal_type,
                    'confidence': min(0.7, data.total_mentions / 30.0),
                    'reason': f'Moderate sentiment ({data.avg_sentiment:.2f}) with {data.total_mentions} mentions',
                    'social_volume': data.total_mentions,
                    'sentiment_score': data.avg_sentiment,
                    'timestamp': datetime.now()
                })
        
        return sorted(signals, key=lambda x: x['confidence'] * abs(x['sentiment_score']), reverse=True)

def main():
    """Main function for testing the sentiment analyzer"""
    analyzer = TrendingStockAnalyzer()
    
    print("🔍 Scanning social media for trending stocks...")
    print("=" * 60)
    
    # Get trending stocks
    trending_stocks = analyzer.get_top_trending_stocks(limit=15)
    
    if not trending_stocks:
        print("❌ No trending stocks found. Check your API credentials.")
        return
    
    print(f"📈 Top {len(trending_stocks)} Trending Stocks:\n")
    
    for i, (symbol, data) in enumerate(trending_stocks, 1):
        sentiment_emoji = "🟢" if data.avg_sentiment > 0.1 else "🔴" if data.avg_sentiment < -0.1 else "⚪"
        trend_emoji = "🚀" if data.trend_momentum > 0.5 else "📉" if data.trend_momentum < -0.2 else "➡️"
        
        print(f"{i:2d}. {sentiment_emoji} ${symbol} {trend_emoji}")
        print(f"     Mentions: {data.total_mentions} | Sentiment: {data.avg_sentiment:+.2f}")
        print(f"     Bullish: {data.bullish_mentions} | Bearish: {data.bearish_mentions} | Neutral: {data.neutral_mentions}")
        print(f"     Trend Momentum: {data.trend_momentum:+.1%}")
        print()
    
    # Generate trading signals
    print("\n📊 Trading Signals Based on Sentiment:")
    print("=" * 60)
    
    sentiment_data = {symbol: data for symbol, data in trending_stocks}
    signals = analyzer.generate_trading_signals(sentiment_data)
    
    if signals:
        for i, signal in enumerate(signals[:10], 1):
            signal_emoji = "🟢" if 'BUY' in signal['signal'] else "🔴"
            print(f"{i:2d}. {signal_emoji} ${signal['symbol']} - {signal['signal']}")
            print(f"     Confidence: {signal['confidence']:.1%} | {signal['reason']}")
            print()
    else:
        print("No strong trading signals detected.")

if __name__ == "__main__":
    main()