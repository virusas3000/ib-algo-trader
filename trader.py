"""
IB Algo Trader — Multi-Strategy Day Trading Engine
Strategies: ORB, VWAP Mean Reversion, Momentum, Gap Fill, Power Hour
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
from pathlib import Path
import sys
import time as _time
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side as XlSide
from openpyxl.utils import get_column_letter
from ib_insync import IB, Stock, Index, MarketOrder, LimitOrder, util

import config as cfg
from indicators import compute_indicators
from strategy import Strategy, Signal, Side, ExitReason, StrategyName
from telegram_notifier import TelegramNotifier
from sentiment_integration import integrate_sentiment_with_trader
from ml_integration import integrate_ml_with_trader

ET = ZoneInfo("America/New_York")

# ─── Logging ─────────────────────────────────────────────
log_path = Path(__file__).parent / "algo.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("trader")


# ─── Trade Logger ─────────────────────────────────────────

class TradeLogger:
    HEADERS = ["Date", "Time (ET)", "Symbol", "Side", "Strategy",
               "Entry Price", "Exit Price", "Size", "P&L ($)", "P&L (%)", "Reason"]

    def __init__(self):
        self.csv_path = Path(__file__).parent / cfg.TRADE_LOG_CSV
        self.xlsx_path = Path(__file__).parent / "trade_history.xlsx"
        self._init_csv()
        self._init_xlsx()

    def _init_csv(self):
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="") as f:
                csv.writer(f).writerow(self.HEADERS)

    def _init_xlsx(self):
        if self.xlsx_path.exists():
            return
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Trades"
        self._write_header(ws)
        # Summary sheet
        ws2 = wb.create_sheet("Summary")
        ws2["A1"] = "Daily Summary"
        ws2["A1"].font = Font(bold=True, size=14)
        wb.save(self.xlsx_path)

    def _write_header(self, ws):
        header_fill = PatternFill("solid", fgColor="1F4E79")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        thin = XlSide(style="thin", color="AAAAAA")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        for col, h in enumerate(self.HEADERS, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
            cell.border = border
        # Column widths
        widths = [12, 10, 8, 6, 18, 12, 12, 8, 12, 10, 12]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    def log(self, **kwargs):
        # Write to CSV
        row_data = [
            kwargs.get("datetime", "")[:10],
            kwargs.get("datetime", "")[11:19],
            kwargs.get("symbol", ""),
            kwargs.get("side", ""),
            kwargs.get("strategy", ""),
            kwargs.get("entry", ""),
            kwargs.get("exit", ""),
            kwargs.get("size", ""),
            kwargs.get("pnl", ""),
            round(kwargs.get("pnl", 0) / max(abs(kwargs.get("entry", 1) * kwargs.get("size", 1)), 1) * 100, 2),
            kwargs.get("reason", ""),
        ]
        with open(self.csv_path, "a", newline="") as f:
            csv.writer(f).writerow(row_data)

        # Write to Excel
        try:
            wb = openpyxl.load_workbook(self.xlsx_path)
            ws = wb["Trades"]
            next_row = ws.max_row + 1
            pnl = kwargs.get("pnl", 0)
            green = PatternFill("solid", fgColor="C6EFCE")
            red   = PatternFill("solid", fgColor="FFC7CE")
            thin  = XlSide(style="thin", color="CCCCCC")
            border = Border(left=thin, right=thin, top=thin, bottom=thin)
            for col, val in enumerate(row_data, 1):
                cell = ws.cell(row=next_row, column=col, value=val)
                cell.border = border
                cell.alignment = Alignment(horizontal="center")
                if col == 9:  # P&L column
                    cell.fill = green if pnl >= 0 else red
                    cell.font = Font(bold=True, color="006100" if pnl >= 0 else "9C0006")
            # Update summary sheet
            self._update_summary(wb, ws)
            wb.save(self.xlsx_path)
        except Exception as e:
            log.warning(f"Excel write error: {e}")

    def _update_summary(self, wb, ws):
        try:
            ws2 = wb["Summary"]
            ws2.delete_rows(1, ws2.max_row)
            ws2["A1"] = "Daily Trading Summary"
            ws2["A1"].font = Font(bold=True, size=14, color="1F4E79")
            # Collect stats
            trades, wins, losses, total_pnl = 0, 0, 0, 0.0
            strategy_stats = {}
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not row[0]:
                    continue
                trades += 1
                pnl = row[8] or 0
                total_pnl += pnl
                if pnl >= 0: wins += 1
                else: losses += 1
                strat = row[4] or "UNKNOWN"
                if strat not in strategy_stats:
                    strategy_stats[strat] = {"trades": 0, "pnl": 0.0, "wins": 0}
                strategy_stats[strat]["trades"] += 1
                strategy_stats[strat]["pnl"] += pnl
                if pnl >= 0: strategy_stats[strat]["wins"] += 1
            # Write summary
            ws2["A3"] = "Total Trades"; ws2["B3"] = trades
            ws2["A4"] = "Wins";         ws2["B4"] = wins
            ws2["A5"] = "Losses";       ws2["B5"] = losses
            ws2["A6"] = "Win Rate";     ws2["B6"] = f"{wins/trades*100:.1f}%" if trades else "0%"
            ws2["A7"] = "Total P&L";    ws2["B7"] = round(total_pnl, 2)
            ws2["B7"].font = Font(bold=True, color="006100" if total_pnl >= 0 else "9C0006")
            r = 9
            ws2.cell(r, 1, "Strategy").font = Font(bold=True)
            ws2.cell(r, 2, "Trades").font = Font(bold=True)
            ws2.cell(r, 3, "Win Rate").font = Font(bold=True)
            ws2.cell(r, 4, "P&L").font = Font(bold=True)
            for strat, s in strategy_stats.items():
                r += 1
                ws2.cell(r, 1, strat)
                ws2.cell(r, 2, s["trades"])
                ws2.cell(r, 3, f"{s['wins']/s['trades']*100:.0f}%" if s["trades"] else "0%")
                ws2.cell(r, 4, round(s["pnl"], 2))
        except Exception as e:
            log.warning(f"Summary update error: {e}")


# ─── Data Manager ─────────────────────────────────────────

class DataManager:
    def __init__(self, ib: IB):
        self.ib = ib
        self._contracts = {}

    def qualify(self, symbol: str) -> bool:
        if symbol in self._contracts:
            return True
        contract = Stock(symbol, "SMART", "USD")
        try:
            self.ib.qualifyContracts(contract)
            self._contracts[symbol] = contract
            return True
        except Exception as e:
            log.warning(f"Could not qualify {symbol}: {e}")
            return False

    def fetch_bars(self, symbol: str, duration: str = "1 D", bar_size: str = "5 mins") -> pd.DataFrame:
        if symbol not in self._contracts:
            return pd.DataFrame()
        try:
            bars = self.ib.reqHistoricalData(
                self._contracts[symbol],
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
            if not bars:
                return pd.DataFrame()
            df = util.df(bars)
            df.columns = [c.lower() for c in df.columns]
            return compute_indicators(df)
        except Exception as e:
            raise ConnectionError(str(e))

    def fetch_vix(self) -> float:
        try:
            vix = Index("VIX", "CBOE")
            self.ib.qualifyContracts(vix)
            ticker = self.ib.reqMktData(vix, "", False, False)
            self.ib.sleep(2)
            val = ticker.last or ticker.close or 20.0
            self.ib.cancelMktData(vix)
            return float(val)
        except Exception:
            return 20.0  # default neutral


# ─── Order Manager ────────────────────────────────────────

class OrderManager:
    def __init__(self, ib: IB, dry_run: bool = False, telegram: "TelegramNotifier" = None):
        self.ib = ib
        self.dry_run = dry_run
        self.telegram = telegram

    def _on_fill(self, trade, fill):
        """Callback fired by ib_insync when an order gets a fill."""
        try:
            symbol = trade.contract.symbol
            action = trade.order.action
            avg_price = trade.orderStatus.avgFillPrice
            filled = trade.orderStatus.filled
            order_id = trade.order.orderId
            log.info(f"[FILL] {symbol} {action} {filled} @ {avg_price:.2f} (orderId={order_id})")
            if self.telegram:
                self.telegram.send_order_filled(
                    symbol=symbol,
                    action=action,
                    size=filled,
                    avg_fill_price=avg_price,
                    order_id=order_id,
                )
        except Exception as e:
            log.error(f"Error in fill callback: {e}")

    def enter(self, signal: Signal):
        if self.dry_run:
            log.info(f"[DRY-RUN] Would enter: {signal.symbol} {signal.side.value} x{signal.size} @ {signal.entry:.2f}")
            return
        action = "BUY" if signal.side == Side.LONG else "SELL"
        order = MarketOrder(action, signal.size)
        contract = Stock(signal.symbol, "SMART", "USD")
        trade = self.ib.placeOrder(contract, order)
        trade.fillEvent += self._on_fill
        log.info(f"Order placed: {trade}")
        
        # Send Telegram trade entry notification
        if self.telegram:
            try:
                # Get approximate account size for position % calculation
                account_size = 1000000  # Default fallback, will be updated from strategy if available
                if hasattr(self, '_account_size'):
                    account_size = self._account_size
                    
                self.telegram.send_trade_entry(
                    symbol=signal.symbol,
                    side=signal.side.value,
                    strategy=signal.strategy.value,
                    entry_price=signal.entry,
                    size=signal.size,
                    account_size=account_size
                )
            except Exception as e:
                log.error(f"Failed to send trade entry notification: {e}")

    def exit(self, symbol: str, side: Side, size: int, reason: str):
        if self.dry_run:
            log.info(f"[DRY-RUN] Would exit: {symbol} {side.value} x{size} reason={reason}")
            return
        action = "SELL" if side == Side.LONG else "BUY"
        order = MarketOrder(action, size)
        contract = Stock(symbol, "SMART", "USD")
        trade = self.ib.placeOrder(contract, order)
        trade.fillEvent += self._on_fill
        log.info(f"Exit order placed: {trade}")


# ─── Main Trader ──────────────────────────────────────────

class Trader:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.ib = IB()
        self.data: DataManager = None
        self.orders: OrderManager = None
        self.strategy: Strategy = None
        self.trade_log = TradeLogger()
        self.telegram = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
        self.connected = False

    def connect(self, retry: bool = False):
        max_attempts = 9999 if retry else 1
        for attempt in range(1, max_attempts + 1):
            log.info(f"Connecting to IB at {cfg.IB_HOST}:{cfg.IB_PORT} (clientId={cfg.CLIENT_ID}, attempt={attempt})...")
            try:
                if self.ib.isConnected():
                    self.ib.disconnect()
                    _time.sleep(1)
                self.ib.connect(cfg.IB_HOST, cfg.IB_PORT, clientId=cfg.CLIENT_ID, timeout=15)
                self.connected = True
                log.info("Connected to IB TWS.")
                if attempt > 1:  # Only send reconnection alerts, not initial connection
                    self.telegram.send_connection_alert("CONNECTED", "Successfully reconnected to IB TWS")
                return
            except Exception as e:
                log.error(f"Connection failed: {e}")
                if attempt == max_attempts:
                    self.telegram.send_connection_alert("CONNECTION FAILED", f"Unable to connect after {max_attempts} attempts")
                    sys.exit(1)
                if attempt == 1:  # Send disconnect alert on first failure
                    self.telegram.send_connection_alert("DISCONNECTED", "Lost connection to IB TWS, attempting to reconnect...")
                log.info("Retrying in 30 seconds...")
                _time.sleep(30)

    def _init_strategy(self):
        accounts = self.ib.managedAccounts()
        log.info(f"Accounts: {accounts}")
        self.ib.reqAccountSummary()
        self.ib.sleep(3)
        account_values = {v.tag: v.value for v in self.ib.accountSummary()
                         if v.tag == "NetLiquidation"}
        account_size = float(account_values.get("NetLiquidation", 100000))
        log.info(f"Account size: ${account_size:,.2f}")

        vix = self.data.fetch_vix()
        log.info(f"VIX: {vix:.1f} → Regime: {'High vol (ORB only)' if vix > cfg.VIX_HIGH else 'Low vol (mean rev)' if vix < cfg.VIX_LOW else 'Normal (all strategies)'}")

        self.strategy = Strategy(account_size, vix)
        
        # 🚀 Initialize sentiment analysis integration
        log.info("🧠 Initializing sentiment analysis...")
        try:
            self.sentiment_integration = integrate_sentiment_with_trader(self)
        except Exception as e:
            log.error(f"Sentiment integration failed: {e}")
            self.sentiment_integration = None
            log.info("📊 Continuing without sentiment analysis...")

        # 🤖 Initialize ML integration  
        log.info("🤖 Initializing ML analysis...")
        try:
            self.ml_integration = integrate_ml_with_trader(self, self.sentiment_integration)
        except Exception as e:
            log.error(f"ML integration failed: {e}")
            self.ml_integration = None
            log.info("📈 Continuing without ML analysis...")

    def run(self):
        watchlist = list(cfg.DEFAULT_WATCHLIST)
        # === Merge in trending tickers from Reddit (auto-refreshed by cron) ===
        try:
            from pathlib import Path as _P
            tr_file = _P(__file__).parent / 'trending_watchlist.txt'
            if tr_file.exists():
                trending = []
                for line in tr_file.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    sym = line.split()[0].split('#')[0].strip()
                    if sym and sym not in watchlist:
                        trending.append(sym)
                if trending:
                    watchlist.extend(trending)
                    log.info(f"[TRENDING] Added {len(trending)} trending tickers from Reddit: {trending}")
        except Exception as e:
            log.warning(f"[TRENDING] Could not load trending list: {e}")
        # === Apply learned blacklist from historical losing trades ===
        try:
            from pathlib import Path as _P
            bl_file = _P(__file__).parent / 'symbol_blacklist.txt'
            if bl_file.exists():
                blacklist = {s.strip() for s in bl_file.read_text().splitlines() if s.strip()}
                before = len(watchlist)
                watchlist = [s for s in watchlist if s not in blacklist]
                log.info(f"[LEARN] Blacklist applied: removed {before-len(watchlist)} symbols {sorted(blacklist)}")
        except Exception as e:
            log.warning(f"[LEARN] Could not apply blacklist: {e}")
        self.data = DataManager(self.ib)
        self.orders = OrderManager(self.ib, dry_run=self.dry_run, telegram=self.telegram)
        self._init_strategy()

        log.info(f"Watchlist: {watchlist}")
        log.info(f"Strategies active: {[s.value for s in self.strategy.active_strategies]}")
        log.info("=" * 60)

        # Send startup notification
        self.telegram.send_system_start(
            account_size=self.strategy.account_size,
            watchlist=watchlist,
            strategies=[s.value for s in self.strategy.active_strategies]
        )

        # Pre-qualify contracts
        for sym in watchlist:
            if self.data.qualify(sym):
                log.info(f"  Qualified: {sym}")

        # Fetch previous closes for gap detection
        self._fetch_prev_closes(watchlist)

        log.info(f"Waiting for market... ET time: {datetime.now(ET).strftime('%H:%M:%S ET')}")

        try:
            while True:
                self.ib.sleep(1)  # keeps ib_insync event loop alive
                if not self.ib.isConnected():
                    log.warning("[RECONNECT] Lost connection — reconnecting...")
                    self.connect(retry=True)
                    self.data = DataManager(self.ib)
                    self.orders = OrderManager(self.ib, dry_run=self.dry_run, telegram=self.telegram)
                    for s in watchlist:
                        self.data.qualify(s)
                    log.info("[RECONNECT] Back online!")
                now = datetime.now(ET)
                now_t = now.time()

                if now_t < time(cfg.MARKET_OPEN_HOUR, cfg.MARKET_OPEN_MIN):
                    if now.second == 0 and now.minute % 5 == 0:
                        log.info(f"Pre-market... {now.strftime('%H:%M ET')}")
                    continue

                # Day Trading Enforcement Checks
                if self.strategy.is_daily_loss_exceeded():
                    log.error("KILL SWITCH — Max daily loss exceeded! Closing all.")
                    self._flatten_all(ExitReason.MAX_LOSS)
                    break

                if now_t >= time(cfg.FINAL_WARNING_HOUR, cfg.FINAL_WARNING_MIN) and not hasattr(self, '_final_warning_sent'):
                    if self.strategy.positions:
                        log.warning("[DAY TRADE] Final warning: Force close in 5 minutes!")
                        self.telegram.send_message(f"🚨 DAY TRADE WARNING: {len(self.strategy.positions)} positions will be force-closed in 5 minutes at {cfg.FORCE_CLOSE_HOUR}:{cfg.FORCE_CLOSE_MIN:02d} PM ET")
                        self._final_warning_sent = True

                if now_t >= time(cfg.FORCE_CLOSE_HOUR, cfg.FORCE_CLOSE_MIN):
                    if self.strategy.positions:
                        log.info("[DAY TRADE EOD] Force closing all positions for day trading compliance...")
                        self.telegram.send_message(f"🔒 DAY TRADE: Closing all {len(self.strategy.positions)} positions at market close")
                        self._flatten_all(ExitReason.EOD_CLOSE)
                    else:
                        log.info("[DAY TRADE EOD] No positions to close - day trading session complete")
                    self._print_summary()
                    self._send_daily_summary()
                    break

                # Prevent new positions near market close
                can_open_new_positions = now_t < time(cfg.NO_NEW_POSITIONS_HOUR, cfg.NO_NEW_POSITIONS_MIN)
                if not can_open_new_positions and not hasattr(self, '_new_pos_warning_sent'):
                    log.info(f"[DAY TRADE] No new positions after {cfg.NO_NEW_POSITIONS_HOUR}:{cfg.NO_NEW_POSITIONS_MIN:02d} PM ET")
                    self._new_pos_warning_sent = True

                # Check for aggressive trading mode
                aggressive_mode = Path("aggressive_mode.flag").exists()
                scan_frequency = 1 if aggressive_mode else 2  # Every minute if aggressive, every 2 minutes otherwise
                
                if now.second != 0:  # Only scan at top of minute
                    continue

                # More frequent scanning in aggressive mode
                should_scan = now.minute % scan_frequency == 0
                
                if should_scan:
                    log.info(f"[SCAN] {now.strftime('%H:%M ET')} | "
                             f"Positions: {len(self.strategy.positions)} | "
                             f"Trades: {self.strategy.trade_count} | "
                             f"P&L: ${self.strategy.daily_pnl:+.2f}")
                    
                    # 🧠 Check sentiment opportunities every 10 minutes
                    if hasattr(self, 'sentiment_integration') and now.minute % 10 == 0:
                        try:
                            self.check_sentiment_opportunities()
                            if now.minute % 30 == 0:  # Log sentiment summary every 30 min
                                self.sentiment_integration.log_sentiment_summary()
                        except Exception as e:
                            log.error(f"Sentiment opportunity check failed: {e}")

                    # 🤖 ML analysis and retraining every 15 minutes
                    if hasattr(self, 'ml_integration') and now.minute % 15 == 0:
                        try:
                            self.check_ml_opportunities()
                            if now.minute % 60 == 0:  # Log ML status every hour
                                self.ml_integration.log_ml_summary()
                                self.manage_ml_retraining()  # Check for retraining
                        except Exception as e:
                            log.error(f"ML analysis failed: {e}")

                for sym in watchlist:
                    try:
                        self._process_symbol(sym, now)
                    except Exception as e:
                        log.error(f"Error on {sym}: {e}")
                        self.telegram.send_error_alert(str(e), sym)

        except KeyboardInterrupt:
            log.info("Interrupted.")
        finally:
            self._cleanup()

    def _process_symbol(self, symbol: str, now: datetime):
        df = self.data.fetch_bars(symbol)
        if df.empty:
            return

        # Update SPY open price for momentum bias
        if symbol == "SPY" and self.strategy.spy_open is None:
            self.strategy.spy_open = float(df.iloc[0]["open"])

        # Update ORB levels
        for bar in df.itertuples():
            self.strategy.update_orb(
                symbol,
                pd.Timestamp(bar.date).to_pydatetime().replace(tzinfo=ET),
                float(bar.high), float(bar.low)
            )

        # Check exits first
        exit_reason = self.strategy.check_exits(symbol, df)
        if exit_reason:
            pos = self.strategy.positions[symbol]
            exit_price = float(df.iloc[-1]["close"])
            self.orders.exit(symbol, pos.side, pos.size, exit_reason.value)
            self.strategy.record_exit(symbol, exit_price, exit_reason)
            
            # Log trade and send Telegram notification
            pnl = round((exit_price - pos.entry_price) * pos.size * (1 if pos.side == Side.LONG else -1), 2)
            self.trade_log.log(
                datetime=now.isoformat(), symbol=symbol,
                side=pos.side.value, strategy=pos.strategy.value,
                entry=pos.entry_price, exit=exit_price,
                size=pos.size, pnl=pnl,
                reason=exit_reason.value
            )
            
            # Send Telegram exit notification
            self.telegram.send_trade_exit(
                symbol=symbol, side=pos.side.value, strategy=pos.strategy.value,
                entry_price=pos.entry_price, exit_price=exit_price, 
                size=pos.size, pnl=pnl, reason=exit_reason.value
            )
            return

        # Check entries — try all strategies (only if day trading allows new positions)
        now_t = now.time()
        can_open_new_positions = now_t < time(cfg.NO_NEW_POSITIONS_HOUR, cfg.NO_NEW_POSITIONS_MIN)
        
        if not can_open_new_positions:
            return  # Skip entry checks after cutoff time for day trading
            
        signal = None
        for check_fn in [
            lambda: self.strategy.check_gap_fill(symbol, df, now),
            lambda: self.strategy.check_orb(symbol, df, now),
            lambda: self.strategy.check_vwap_reversion(symbol, df, now),
            lambda: self.strategy.check_momentum(symbol, df, now),
            lambda: self.strategy.check_power_hour(symbol, df, now),
        ]:
            signal = check_fn()
            if signal:
                break

        if signal:
            log.info(f"[DAY TRADE ENTRY] Opening {signal.symbol} {signal.side.value} position (time: {now_t.strftime('%H:%M:%S')})")
            self.orders.enter(signal)
            self.strategy.record_entry(signal, now)
            
            # Send Telegram entry notification
            self.telegram.send_trade_entry(
                symbol=signal.symbol, side=signal.side.value, strategy=signal.strategy.value,
                entry_price=signal.entry, size=signal.size, account_size=self.strategy.account_size
            )

    def _fetch_prev_closes(self, watchlist):
        log.info("Fetching previous closes for gap detection...")
        for sym in watchlist:
            try:
                df = self.data.fetch_bars(sym, duration="2 D", bar_size="1 day")
                if not df.empty and len(df) >= 2:
                    self.strategy.prev_closes[sym] = float(df.iloc[-2]["close"])
                    log.info(f"  Prev close {sym}: {self.strategy.prev_closes[sym]:.2f}")
            except Exception as e:
                log.warning(f"  Could not fetch prev close for {sym}: {e}")
        log.info("Prev closes fetched. Starting main loop...")

    def _flatten_all(self, reason: ExitReason):
        for sym in list(self.strategy.positions.keys()):
            try:
                df = self.data.fetch_bars(sym)
                price = float(df.iloc[-1]["close"]) if not df.empty else 0
                pos = self.strategy.positions[sym]
                self.orders.exit(sym, pos.side, pos.size, reason.value)
                self.strategy.record_exit(sym, price, reason)
                
                # Send Telegram exit notification for mass closure
                pnl = round((price - pos.entry_price) * pos.size * (1 if pos.side == Side.LONG else -1), 2)
                try:
                    self.telegram.send_trade_exit(
                        symbol=sym, side=pos.side.value, strategy=pos.strategy.value,
                        entry_price=pos.entry_price, exit_price=price, 
                        size=pos.size, pnl=pnl, reason=reason.value
                    )
                except Exception as e:
                    log.error(f"Failed to send trade exit notification for {sym}: {e}")
            except Exception as e:
                log.error(f"Error flattening {sym}: {e}")

    def _print_summary(self):
        log.info(self.strategy.get_summary())
    
    def _send_daily_summary(self):
        """Send end of day summary to Telegram"""
        try:
            trades = self.strategy.trade_count
            wins = len([t for t in self.strategy.completed_trades if t.pnl >= 0])
            losses = trades - wins
            total_pnl = self.strategy.daily_pnl
            
            self.telegram.send_daily_summary(
                trades=trades, wins=wins, losses=losses,
                total_pnl=total_pnl, account_size=self.strategy.account_size
            )
        except Exception as e:
            log.error(f"Error sending daily summary: {e}")

    def _cleanup(self):
        if self.ib.isConnected():
            try:
                self.ib.disconnect()
            except Exception:
                pass
        log.info("Disconnected from IB.")


# ─── Entry Point ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="IB Multi-Strategy Algo Trader")
    parser.add_argument("--dry-run", action="store_true", help="Signals only, no real orders")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    if args.port:
        cfg.IB_PORT = args.port

    print(r"""
    ╔═══════════════════════════════════════════════╗
    ║   IB Multi-Strategy Day Trader                ║
    ║                                               ║
    ║   Strategies: ORB | VWAP Rev | Momentum      ║
    ║               Gap Fill | Power Hour           ║
    ║   Risk: Kelly | VIX Regime | Auto-reconnect  ║
    ╚═══════════════════════════════════════════════╝
    """)

    trader = Trader(dry_run=args.dry_run)
    trader.connect(retry=True)
    trader.run()


if __name__ == "__main__":
    main()
