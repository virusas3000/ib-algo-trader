#!/usr/bin/env python3
"""
Scrapes Reddit + X.com (via nitter mirrors) for trending stock tickers,
scores them, writes top N to trending_watchlist.txt and trending.db.

No auth needed.
"""
import os
import re
import sys
import json
import sqlite3
import time
from datetime import datetime
from collections import Counter
from urllib.request import Request, urlopen
from html.parser import HTMLParser

HERE = os.path.expanduser("~/Desktop/ib_algo_trader")
OUT_FILE = os.path.join(HERE, "trending_watchlist.txt")
DB_FILE = os.path.join(HERE, "trending.db")

SUBREDDITS = ["wallstreetbets", "stocks", "investing", "StockMarket", "options"]

# X.com via nitter mirrors (rotated). Search queries hit "$TICKER" cashtags.
NITTER_INSTANCES = [
    "nitter.tiekoetter.com",
    "nitter.net",
]
# Search popular cashtag queries — we let nitter return whatever's trending under them.
# Also scrape these high-signal accounts' latest tweets.
X_ACCOUNTS = ["DeItaone", "unusual_whales", "zerohedge", "FirstSquawk", "WallStreetSilv"]
X_QUERIES = ["stocks", "calls", "puts", "earnings", "squeeze"]

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 ib-trending/1.0"
TOP_N = 15

# Common false-positive tickers to filter
BLACKLIST = {
    "CEO","IPO","SEC","FDA","API","AI","ML","IT","HR","PR","UI","UX","TLDR",
    "USA","USD","NYC","LA","SF","UK","EU","US","AM","PM","EST","PST","ET",
    "NEW","ALL","GET","BUY","SELL","HOLD","UP","DOWN","TOP","BOT","DD","YOLO",
    "HOT","BIG","HUGE","BEST","WORST","FAST","SLOW","HIGH","LOW","FOR","AND",
    "THE","BUT","NOT","WAS","HAS","NOW","ARE","OUT","CAN","ONE","TWO","WSB",
    "OP","IMO","TBH","LOL","FYI","OK","ATH","EOD","EOW","YTD","QQQ","SPY",
    "I","A","ETF","COVID","GDP","CPI","PPI","FED","ECB","NATO","BRICS",
    "IRA","401K","ROI","P/E","EPS","PE","ROIC","ROE","WACC","DCF",
    "CALL","PUT","ITM","OTM","ATM","DTE","IV","HV","RH","TD","IBKR",
    "CNBC","CNN","BBC","FOX","WSJ","NYT","BLOOMBERG","REUTERS",
    "GEX","SPX","NDX","DJI","RUT","VIX","DXY","TLT","TO","IS","BE","DO",
    "KEEL","FIG","ATH","ATL","FOMO","ETH","BTC","NFT","DAO","DEX",
    "MSOS","GTBIF","BITF","TCNNF","CURLF",  # crypto/cannabis OTC noise
    "IRAN","CHINA","RUSSIA","ISRAEL","UKRAINE","JAPAN","KOREA","INDIA",
    "DOJ","FBI","CIA","NSA","IRS","DOD","DOE","HHS","DHS","TSA","NASA",
    "IN","ON","AT","TO","BY","OF","UP","SO","NO","GO","WE","YOU","HE","SHE",
    "WILL","SHARE","DCA","XSP","CAR","EV","EDIT","TPU","MU","PE",
    "TRUMP","BIDEN","HARRIS","VANCE","MUSK","POWELL","YELLEN",
    "CPU","GPU","TA","USDC","CFO","FCF","RAM","SSD","OS","UI","FX","TVL",  # tech/finance jargon, not tickers
    "OPEC","FOMC","CAGR","NATO","OECD","WTO","IMF","BOE","BOJ","RBA","SNB",  # orgs/econ acronyms
    "YOY","QOQ","MOM","TTM","ARR","MRR","CAC","LTV","NPV","IRR","EBIT","EBITDA",  # finance metrics
    "AAL",  # spurious — was scoring 10 on r/investing as 'all'
}

# Tickers to KEEP even if short
WHITELIST_SHORT = {"F","T","C","V","X","M","K","O","D","Y","S","R","W","L","B","G","H","Z","P","E","I"}

CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")
TICKER_RE = re.compile(r"\b([A-Z]{2,5})\b")


def fetch_subreddit(sub, sort="hot", limit=50):
    url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit={limit}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("data", {}).get("children", [])
    except Exception as e:
        print(f"  ⚠ {sub} fetch failed: {e}", file=sys.stderr)
        return []


def fetch_nitter(path):
    """Try each nitter instance until one works. Returns HTML text or ''."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }
    for inst in NITTER_INSTANCES:
        url = f"https://{inst}{path}"
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=15) as r:
                if r.status == 200:
                    body = r.read().decode("utf-8", errors="ignore")
                    if "tweet-content" in body:
                        return body
        except Exception:
            continue
    return ""


# Extract tweet text from nitter HTML
TWEET_RE = re.compile(
    r'<div class="tweet-content[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL,
)
TAG_STRIP = re.compile(r'<[^>]+>')


def extract_tweets(html):
    """Return list of plain-text tweet bodies from nitter HTML."""
    out = []
    for m in TWEET_RE.finditer(html):
        text = TAG_STRIP.sub(' ', m.group(1))
        text = re.sub(r'\s+', ' ', text).strip()
        if text:
            out.append(text)
    return out


def fetch_x_signals():
    """Pull tweet text from X via nitter — both account timelines and search queries."""
    tweets = []
    for acc in X_ACCOUNTS:
        html = fetch_nitter(f"/{acc}")
        if html:
            tweets.extend(extract_tweets(html))
        time.sleep(1)
    for q in X_QUERIES:
        html = fetch_nitter(f"/search?q=%24{q.upper()}&f=tweets")
        if html:
            tweets.extend(extract_tweets(html))
        time.sleep(1)
    return tweets


def extract_tickers(text, cashtag_only=False):
    """Pull tickers from text. Cashtags (with $) score 3x.
    If cashtag_only=True, ignore bare uppercase words (use for X.com which is noisy)."""
    found = Counter()
    if not text:
        return found
    for m in CASHTAG_RE.finditer(text):
        sym = m.group(1).upper()
        if sym not in BLACKLIST:
            found[sym] += 3
    if not cashtag_only:
        for m in TICKER_RE.finditer(text):
            sym = m.group(1).upper()
            if sym in BLACKLIST:
                continue
            if len(sym) == 1 and sym not in WHITELIST_SHORT:
                continue
            found[sym] += 1
    return found


def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trending (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scraped_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            score INTEGER NOT NULL,
            sources TEXT
        )
    """)
    conn.commit()
    return conn


def main():
    print(f"🔍 Scanning {len(SUBREDDITS)} subreddits + X via nitter...")
    scores = Counter()
    sources_per = {}

    # ── Reddit ──
    for sub in SUBREDDITS:
        posts = fetch_subreddit(sub, "hot", 50)
        time.sleep(1)
        sub_count = 0
        for p in posts:
            d = p.get("data", {})
            text = (d.get("title", "") or "") + " " + (d.get("selftext", "") or "")
            ups = d.get("ups", 0)
            weight = max(1, min(5, ups // 100))
            tickers = extract_tickers(text)
            for sym, count in tickers.items():
                scores[sym] += count * weight
                sources_per.setdefault(sym, set()).add(f"r/{sub}")
                sub_count += 1
        print(f"  r/{sub}: {len(posts)} posts, {sub_count} ticker mentions")

    # ── X via nitter ──
    print("  Fetching X.com via nitter...")
    tweets = fetch_x_signals()
    x_count = 0
    for tw in tweets:
        tickers = extract_tickers(tw, cashtag_only=True)  # X is noisy, cashtags only
        for sym, count in tickers.items():
            scores[sym] += count * 2  # X mentions weighted 2x (faster signal)
            sources_per.setdefault(sym, set()).add("x.com")
            x_count += 1
    print(f"  x.com: {len(tweets)} tweets, {x_count} ticker mentions")

    # Filter: must appear in at least 2 sources OR have score >= 5
    filtered = [
        (sym, sc) for sym, sc in scores.items()
        if len(sources_per[sym]) >= 2 or sc >= 5
    ]
    filtered.sort(key=lambda x: -x[1])
    top = filtered[:TOP_N]

    if not top:
        print("⚠ No trending tickers found this run.")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(OUT_FILE, "w") as f:
        f.write(f"# Auto-generated by trending_scraper.py at {now}\n")
        f.write(f"# Top {len(top)} trending tickers from Reddit + X.com\n")
        f.write("# Format: SYMBOL  # score (sources)\n")
        for sym, sc in top:
            srcs = ",".join(sorted(sources_per[sym]))
            f.write(f"{sym}  # score={sc} sources={srcs}\n")

    conn = init_db()
    for sym, sc in top:
        conn.execute(
            "INSERT INTO trending (scraped_at, symbol, score, sources) VALUES (?, ?, ?, ?)",
            (now, sym, sc, ",".join(sorted(sources_per[sym]))),
        )
    conn.commit()
    conn.close()

    print(f"\n✅ Wrote {len(top)} trending tickers to {OUT_FILE}")
    print("─" * 40)
    for i, (sym, sc) in enumerate(top, 1):
        srcs = ",".join(sorted(sources_per[sym]))
        print(f"  {i:2d}. {sym:6s}  score={sc:4d}  [{srcs}]")


if __name__ == "__main__":
    main()
