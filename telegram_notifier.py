"""
Telegram Notification Module for IB Algo Trader
Sends trading alerts to Telegram bot
"""
import logging
import requests
from typing import Optional
from datetime import datetime

log = logging.getLogger("telegram")

class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        """
        Initialize Telegram notifier
        
        Args:
            bot_token: Your Telegram bot token (from BotFather)
            chat_id: Your Telegram chat ID (where to send messages)
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.enabled = bool(bot_token and chat_id)
        
        if self.enabled:
            log.info(f"Telegram notifications enabled for chat {chat_id}")
        else:
            log.warning("Telegram notifications disabled - missing token or chat_id")
    
    def send_message(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Send a message to Telegram"""
        if not self.enabled:
            return False
            
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message
            }
            
            # Only add parse_mode if it's not None
            if parse_mode is not None:
                payload["parse_mode"] = parse_mode
            
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            
            log.debug(f"Telegram message sent successfully")
            return True
            
        except Exception as e:
            log.error(f"Failed to send Telegram message: {e}")
            return False
    
    def send_trade_entry(self, symbol: str, side: str, strategy: str, 
                        entry_price: float, size: int, account_size: float) -> bool:
        """Send trade entry notification"""
        position_value = entry_price * size
        position_pct = (position_value / account_size) * 100
        
        message = f"""🚀 TRADE ENTRY
━━━━━━━━━━━━━━━━
📈 {symbol} - {side} 
💰 Price: ${entry_price:.2f}
📊 Size: {size} shares
🎯 Strategy: {strategy}
💼 Position: ${position_value:,.0f} ({position_pct:.1f}%)
⏰ Time: {datetime.now().strftime('%H:%M:%S ET')}"""
        return self.send_message(message, parse_mode=None)
    
    def send_trade_exit(self, symbol: str, side: str, strategy: str,
                       entry_price: float, exit_price: float, size: int, 
                       pnl: float, reason: str) -> bool:
        """Send trade exit notification"""
        pnl_pct = (pnl / (entry_price * size)) * 100
        profit_emoji = "✅💚" if pnl >= 0 else "❌💸"
        
        message = f"""{profit_emoji} TRADE EXIT
━━━━━━━━━━━━━━━━
📈 {symbol} - {side}
📊 Strategy: {strategy}
💰 Entry: ${entry_price:.2f}
💰 Exit: ${exit_price:.2f}
📊 Size: {size} shares
💵 P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)
🔄 Reason: {reason}
⏰ Time: {datetime.now().strftime('%H:%M:%S ET')}"""
        return self.send_message(message, parse_mode=None)
    
    def send_connection_alert(self, status: str, message: str) -> bool:
        """Send connection status alerts"""
        emoji = "🔴" if "disconnected" in status.lower() or "error" in status.lower() else "🟢"
        
        alert = f"""
{emoji} **CONNECTION ALERT**
━━━━━━━━━━━━━━━━
**Status:** {status}
**Details:** {message}
⏰ **Time:** {datetime.now().strftime('%H:%M:%S ET')}
"""
        return self.send_message(alert)
    
    def send_daily_summary(self, trades: int, wins: int, losses: int, 
                          total_pnl: float, account_size: float) -> bool:
        """Send end-of-day summary"""
        win_rate = (wins / trades * 100) if trades > 0 else 0
        pnl_pct = (total_pnl / account_size) * 100
        summary_emoji = "🎉💰" if total_pnl >= 0 else "😞📉"
        
        message = f"""
{summary_emoji} DAILY SUMMARY
━━━━━━━━━━━━━━━━
📊 Total Trades: {trades}
✅ Wins: {wins}
❌ Losses: {losses} 
📈 Win Rate: {win_rate:.1f}%
💰 Total P&L: ${total_pnl:+.2f} ({pnl_pct:+.2f}%)
💼 Account: ${account_size:,.0f}
🔒 Day Trading: All positions closed ✓
⏰ Date: {datetime.now().strftime('%Y-%m-%d')}
"""
        return self.send_message(message, parse_mode=None)
    
    def send_system_start(self, account_size: float, watchlist: list, strategies: list) -> bool:
        """Send system startup notification"""
        message = f"""
🤖 ALGO TRADER STARTED
━━━━━━━━━━━━━━━━
💼 Account Size: ${account_size:,.0f}
📈 Watchlist: {', '.join(watchlist[:5])}{'...' if len(watchlist) > 5 else ''} ({len(watchlist)} total)
🎯 Strategies: {', '.join(strategies)}
🔒 Mode: Day Trading Only (positions close at 3:45 PM ET)
⏰ Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}

Ready to trade! 🚀
"""
        return self.send_message(message, parse_mode=None)
    
    def send_order_filled(self, symbol: str, action: str, size: float, avg_fill_price: float, order_id: int) -> bool:
        """Send notification when an order is confirmed filled by IB"""
        emoji = "✅" if action == "BUY" else "🔴"
        message = f"""{emoji} ORDER FILLED
━━━━━━━━━━━━━━━━
📌 {symbol} — {action}
💰 Fill Price: ${avg_fill_price:.2f}
📊 Shares: {int(size)}
🆔 Order ID: {order_id}
⏰ Time: {datetime.now().strftime('%H:%M:%S ET')}"""
        return self.send_message(message, parse_mode=None)

    def send_error_alert(self, error_msg: str, symbol: Optional[str] = None) -> bool:
        """Send error alert"""
        symbol_info = f" ({symbol})" if symbol else ""
        
        message = f"""
⚠️ **SYSTEM ERROR**{symbol_info}
━━━━━━━━━━━━━━━━
**Error:** {error_msg}
⏰ **Time:** {datetime.now().strftime('%H:%M:%S ET')}
"""
        return self.send_message(message)