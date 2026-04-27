#!/usr/bin/env python3
"""
Force Trading Trigger - Manually trigger trading scans
"""
import logging
import signal
import sys
import os
import time
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)

def find_trader_process():
    """Find the running trader process PID"""
    try:
        result = os.popen("ps aux | grep 'python3 trader.py' | grep -v grep").read()
        if result.strip():
            pid = int(result.split()[1])
            logger.info(f"Found trader process: PID {pid}")
            return pid
    except Exception as e:
        logger.error(f"Error finding trader process: {e}")
    return None

def trigger_immediate_scan():
    """Send a signal to force immediate trading scan"""
    pid = find_trader_process()
    if not pid:
        logger.error("No trader process found! Make sure trader.py is running.")
        return False
    
    try:
        # Send SIGUSR1 to trigger immediate scan
        os.kill(pid, signal.SIGUSR1)
        logger.info(f"✅ Sent trading trigger signal to PID {pid}")
        return True
    except Exception as e:
        logger.error(f"Failed to send signal: {e}")
        return False

def enable_aggressive_mode():
    """Patch the trader to be more aggressive"""
    logger.info("🔥 Enabling aggressive trading mode...")
    
    # Create a flag file that the trader can check
    flag_file = Path("aggressive_mode.flag")
    with open(flag_file, "w") as f:
        f.write("1")
    
    logger.info("✅ Aggressive mode enabled")

def show_current_status():
    """Show current trading bot status"""
    logger.info("📊 Current Trading Bot Status:")
    logger.info(f"   Process: {'✅ RUNNING' if find_trader_process() else '❌ NOT FOUND'}")
    logger.info(f"   Aggressive mode: {'✅ ENABLED' if Path('aggressive_mode.flag').exists() else '❌ DISABLED'}")

if __name__ == "__main__":
    logger.info("🚀 Force Trading Controller")
    logger.info("=" * 50)
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 force_trading.py scan      - Trigger immediate scan")
        print("  python3 force_trading.py aggressive - Enable aggressive mode") 
        print("  python3 force_trading.py status     - Show status")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == "scan":
        logger.info("🎯 Triggering immediate trading scan...")
        if trigger_immediate_scan():
            logger.info("✅ Trading scan triggered!")
        else:
            logger.error("❌ Failed to trigger trading scan")
            
    elif command == "aggressive":
        enable_aggressive_mode()
        trigger_immediate_scan()
        
    elif command == "status":
        show_current_status()
        
    else:
        logger.error(f"Unknown command: {command}")