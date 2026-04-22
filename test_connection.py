#!/usr/bin/env python3
"""
Test IB Connection and Telegram Notifications
"""
import sys
import time
sys.path.append('/Users/vickhung/Desktop/ib_algo_trader')

from ib_insync import IB
from telegram_notifier import TelegramNotifier
import config as cfg

def test_connection():
    print("Testing IB TWS connection...")
    
    # Initialize Telegram
    telegram = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    
    # Test IB connection
    ib = IB()
    
    try:
        print(f"Connecting to {cfg.IB_HOST}:{cfg.IB_PORT}...")
        ib.connect(cfg.IB_HOST, cfg.IB_PORT, clientId=cfg.CLIENT_ID, timeout=10)
        print("✅ Connected to IB TWS successfully!")
        
        # Send success notification
        telegram.send_connection_alert("CONNECTED", "Successfully connected to IB TWS for testing")
        
        # Get account info
        time.sleep(2)
        accounts = ib.managedAccounts()
        print(f"Accounts: {accounts}")
        
        ib.disconnect()
        print("✅ Connection test completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        telegram.send_connection_alert("CONNECTION FAILED", f"Test connection failed: {e}")
        return False

if __name__ == "__main__":
    test_connection()