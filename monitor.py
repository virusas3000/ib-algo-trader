"""
Algo Log Monitor — watches algo.log and prints alerts for key events.
Run this separately; OpenClaw heartbeat will pick up output.
"""
import time
import subprocess
import os

LOG_FILE = os.path.expanduser("~/Desktop/ib_algo_trader/algo.log")

KEYWORDS = {
    "TRADE ENTERED": "🟢 TRADE ENTERED",
    "TRADE EXITED": "🔴 TRADE EXITED",
    "STOP HIT": "🛑 STOP LOSS HIT",
    "TARGET": "🎯 TARGET HIT",
    "KILL SWITCH": "⚠️ KILL SWITCH TRIGGERED",
    "ConnectionError": "❌ CONNECTION LOST",
    "Error": "⚠️ ERROR",
    "profit": "💰 PROFIT",
    "loss": "📉 LOSS",
}

def tail_log(file_path):
    with open(file_path, "r") as f:
        f.seek(0, 2)  # go to end
        while True:
            line = f.readline()
            if not line:
                time.sleep(1)
                continue
            yield line

if __name__ == "__main__":
    print("📡 Monitor started — watching algo.log for trade events...")
    for line in tail_log(LOG_FILE):
        line = line.strip()
        for keyword, label in KEYWORDS.items():
            if keyword.lower() in line.lower():
                print(f"ALERT: {label}\n{line}")
                break
