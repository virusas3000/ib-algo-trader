"""
Strategy Engine — multi-strategy day trading logic
Strategies: ORB, VWAP Mean Reversion, Momentum Continuation, Gap Fill, Power Hour
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Dict, List, Optional

import pandas as pd

import config as cfg
from indicators import compute_indicators

log = logging.getLogger("strategy")


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class StrategyName(Enum):
    ORB = "ORB"
    VWAP_REVERSION = "VWAP_REVERSION"
    MOMENTUM = "MOMENTUM"
    GAP_FILL = "GAP_FILL"
    POWER_HOUR = "POWER_HOUR"


class ExitReason(Enum):
    STOP = "STOP"
    TARGET1 = "TARGET1"
    TARGET2 = "TARGET2"
    EOD_CLOSE = "EOD_CLOSE"
    MAX_LOSS = "MAX_LOSS"
    SIGNAL_REVERSE = "SIGNAL_REVERSE"


@dataclass
class Signal:
    symbol: str
    side: Side
    strategy: StrategyName
    entry: float
    stop: float
    target1: float
    target2: float
    size: int = 0
    reason: str = ""


@dataclass
class Position:
    symbol: str
    side: Side
    strategy: StrategyName
    entry_price: float
    stop: float
    target1: float
    target2: float
    size: int
    t1_hit: bool = False
    entry_time: Optional[datetime] = None


@dataclass
class StrategyStats:
    name: str
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    consecutive_losses: int = 0
    disabled: bool = False

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0


class Strategy:
    def __init__(self, account_size: float, vix: float = 20.0):
        self.account_size = account_size
        self.vix = vix
        self.positions: Dict[str, Position] = {}
        self.daily_pnl = 0.0
        self.trade_count = 0
        self.spy_open = None  # SPY price at open for momentum bias
        self.prev_closes: Dict[str, float] = {}  # for gap detection
        self.orb_levels: Dict[str, Dict] = {}    # {symbol: {high, low, set}}
        self.stats: Dict[str, StrategyStats] = {
            s.value: StrategyStats(s.value) for s in StrategyName
        }

    # ── Regime ───────────────────────────────────────────

    @property
    def size_multiplier(self) -> float:
        """Reduce size in high VIX environments."""
        if self.vix > cfg.VIX_HIGH:
            return cfg.VIX_HIGH_SIZE_MULT
        return 1.0

    @property
    def active_strategies(self) -> List[StrategyName]:
        """Only ORB when VIX > 25."""
        if self.vix > cfg.VIX_HIGH:
            return [StrategyName.ORB]
        return [s for s in StrategyName if not self.stats[s.value].disabled]

    def favor_mean_reversion(self) -> bool:
        return self.vix < cfg.VIX_LOW

    # ── Position Sizing ───────────────────────────────────

    def calc_size(self, entry: float, stop: float) -> int:
        risk_amount = self.account_size * cfg.RISK_PER_TRADE * self.size_multiplier
        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0 or risk_per_share != risk_per_share:  # NaN check
            return 0
        size = int(risk_amount / risk_per_share)
        max_size = int(self.account_size * cfg.MAX_POSITION_PCT / entry)
        return min(size, max_size)

    # ── Limits ───────────────────────────────────────────

    def can_trade(self, strategy: StrategyName, side: Side = None) -> bool:
        if self.is_daily_loss_exceeded():
            return False
        if self.trade_count >= cfg.MAX_TRADES_PER_DAY:
            return False
        if len(self.positions) >= cfg.MAX_POSITIONS:
            return False
        if strategy not in self.active_strategies:
            return False
        if strategy.value in self.stats and self.stats[strategy.value].disabled:
            return False
        # Long-only filter
        if getattr(cfg, 'LONG_ONLY', False) and side == Side.SHORT:
            return False
        return True

    def is_daily_loss_exceeded(self) -> bool:
        return self.daily_pnl <= -(self.account_size * cfg.MAX_DAILY_LOSS_PCT)

    # ── ORB ──────────────────────────────────────────────

    def update_orb(self, symbol: str, bar_time: datetime, high: float, low: float):
        et_time = bar_time.time() if hasattr(bar_time, 'time') else bar_time
        orb_start = time(cfg.MARKET_OPEN_HOUR, cfg.MARKET_OPEN_MIN)
        orb_end_15 = time(9, 45)
        if orb_start <= et_time <= orb_end_15:
            if symbol not in self.orb_levels:
                self.orb_levels[symbol] = {"high": high, "low": low, "set": False}
            else:
                self.orb_levels[symbol]["high"] = max(self.orb_levels[symbol]["high"], high)
                self.orb_levels[symbol]["low"] = min(self.orb_levels[symbol]["low"], low)
        elif et_time > orb_end_15 and symbol in self.orb_levels:
            self.orb_levels[symbol]["set"] = True

    def check_orb(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if symbol in self.positions:
            return None
        if symbol not in self.orb_levels or not self.orb_levels[symbol].get("set"):
            return None

        orb = self.orb_levels[symbol]
        last = df.iloc[-1]
        close = float(last["close"])
        rvol = float(last.get("rvol", 1.0))
        atr = float(last.get("atr14", close * 0.01))

        if rvol < cfg.ORB_VOLUME_MULT:
            return None

        if close > orb["high"]:
            side = Side.LONG
        elif close < orb["low"]:
            side = Side.SHORT
        else:
            return None

        if not self.can_trade(StrategyName.ORB, side):
            return None

        if side == Side.LONG:
            stop = close - atr * cfg.ORB_ATR_STOP
            t1 = close + atr * cfg.ORB_ATR_T1
            t2 = close + atr * cfg.ORB_ATR_T2
        else:
            stop = close + atr * cfg.ORB_ATR_STOP
            t1 = close - atr * cfg.ORB_ATR_T1
            t2 = close - atr * cfg.ORB_ATR_T2

        size = self.calc_size(close, stop)
        if size <= 0:
            return None

        return Signal(symbol, side, StrategyName.ORB, close, stop, t1, t2, size,
                      f"ORB breakout rvol={rvol:.1f}x")

    # ── VWAP Mean Reversion ───────────────────────────────

    def check_vwap_reversion(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.VWAP_REVERSION):
            return None
        if symbol not in cfg.VWAP_SYMBOLS:
            return None
        if symbol in self.positions:
            return None

        # Only trade VWAP reversion 10:00 AM - 2:30 PM ET
        t = now.time()
        if not (time(10, 0) <= t <= time(14, 30)):
            return None

        last = df.iloc[-1]
        close = float(last["close"])
        vwap_val = float(last.get("vwap", close))
        rsi_val = float(last.get("rsi14", 50))
        atr = float(last.get("atr14", close * 0.01))

        dev_pct = (close - vwap_val) / vwap_val * 100

        if dev_pct <= -cfg.VWAP_DEVIATION_PCT and rsi_val < cfg.VWAP_RSI_OVERSOLD:
            # Oversold below VWAP → long back to VWAP
            stop = close - atr * 1.2
            t1 = vwap_val
            t2 = vwap_val + atr * 0.5
            side = Side.LONG
            rr = (t1 - close) / (close - stop) if close != stop else 0
            if rr < cfg.MIN_RISK_REWARD:
                return None
        elif dev_pct >= cfg.VWAP_DEVIATION_PCT and rsi_val > cfg.VWAP_RSI_OVERBOUGHT:
            # Overbought above VWAP → short back to VWAP
            stop = close + atr * 1.2
            t1 = vwap_val
            t2 = vwap_val - atr * 0.5
            side = Side.SHORT
            rr = (close - t1) / (stop - close) if stop != close else 0
            if rr < cfg.MIN_RISK_REWARD:
                return None
        else:
            return None

        size = self.calc_size(close, stop)
        if size <= 0:
            return None

        return Signal(symbol, side, StrategyName.VWAP_REVERSION, close, stop, t1, t2, size,
                      f"VWAP dev={dev_pct:.1f}% rsi={rsi_val:.0f}")

    # ── Momentum Continuation ─────────────────────────────

    def check_momentum(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.MOMENTUM):
            return None
        if symbol in self.positions:
            return None
        if self.spy_open is None:
            return None

        # Only after 10:00 AM ET
        if now.time() < time(10, 0):
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        close = float(last["close"])
        ema9 = float(last.get("ema9", close))
        prev_ema9 = float(prev.get("ema9", close))
        atr = float(last.get("atr14", close * 0.01))

        # SPY momentum bias
        spy_change = 0.0
        if symbol == "SPY" and self.spy_open:
            spy_change = (close - self.spy_open) / self.spy_open * 100

        # Pullback to 9EMA in uptrend
        if spy_change > 0.5 and close > ema9 and float(prev["close"]) < prev_ema9:
            # Price just crossed back above 9EMA = pullback entry
            stop = ema9 - atr * 0.5
            t1 = close + atr * 1.5
            t2 = close + atr * 2.5
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.LONG, StrategyName.MOMENTUM, close, stop, t1, t2, size,
                              f"Momentum pullback to 9EMA spy={spy_change:.1f}%")

        return None

    # ── Gap Fill ──────────────────────────────────────────

    def check_gap_fill(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.GAP_FILL):
            return None
        if symbol in self.positions:
            return None
        if symbol not in self.prev_closes:
            return None

        # Only first 45 min
        if now.time() > time(10, 15):
            return None

        prev_close = self.prev_closes[symbol]
        last = df.iloc[-1]
        prev_bar = df.iloc[-2] if len(df) > 1 else last
        open_price = float(df.iloc[0]["open"]) if len(df) > 0 else float(last["close"])
        close = float(last["close"])
        atr = float(last.get("atr14", close * 0.01))

        gap_pct = (open_price - prev_close) / prev_close * 100

        if abs(gap_pct) < cfg.GAP_MIN_PCT:
            return None

        # SPY trend filter — don't fade gap-ups if SPY is trending strongly up
        spy_trend = 0.0
        if self.spy_open and symbol != "SPY":
            spy_close = close  # fallback
            spy_trend = (spy_close - self.spy_open) / self.spy_open * 100 if self.spy_open else 0

        if gap_pct > cfg.GAP_MIN_PCT:
            # Gap up → only short if SPY NOT in strong uptrend
            if spy_trend > cfg.GAP_MAX_SPY_TREND:
                return None
            # Require reversal: current bar must close below open (bearish candle)
            if cfg.GAP_REQUIRE_REVERSAL and float(last["close"]) >= float(last["open"]):
                return None
            # Price must have already started pulling back from gap
            if close >= open_price:
                return None
            stop = open_price * (1 + cfg.GAP_STOP_PCT / 100)
            t1 = prev_close + (open_price - prev_close) * 0.5
            t2 = prev_close
            rr = (close - t1) / (stop - close) if stop != close else 0
            if abs(rr) < cfg.MIN_RISK_REWARD:
                return None
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.SHORT, StrategyName.GAP_FILL, close, stop, t1, t2, size,
                              f"Gap up fill gap={gap_pct:.1f}% spy_trend={spy_trend:.1f}%")

        elif gap_pct < -cfg.GAP_MIN_PCT:
            # Gap down → only long if SPY NOT in strong downtrend
            if spy_trend < -cfg.GAP_MAX_SPY_TREND:
                return None
            # Require reversal: current bar must close above open (bullish candle)
            if cfg.GAP_REQUIRE_REVERSAL and float(last["close"]) <= float(last["open"]):
                return None
            if close <= open_price:
                return None
            stop = open_price * (1 - cfg.GAP_STOP_PCT / 100)
            t1 = prev_close - (prev_close - open_price) * 0.5
            t2 = prev_close
            rr = (t1 - close) / (close - stop) if close != stop else 0
            if rr < cfg.MIN_RISK_REWARD:
                return None
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.LONG, StrategyName.GAP_FILL, close, stop, t1, t2, size,
                              f"Gap down fill gap={gap_pct:.1f}% spy_trend={spy_trend:.1f}%")

        return None

    # ── Power Hour ────────────────────────────────────────

    def check_power_hour(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.POWER_HOUR):
            return None
        if symbol in self.positions:
            return None

        t = now.time()
        if not (time(cfg.POWER_HOUR_START, cfg.POWER_HOUR_START_MIN) <= t <= time(15, 30)):
            return None

        last = df.iloc[-1]
        close = float(last["close"])
        rvol = float(last.get("rvol", 1.0))

        if rvol < cfg.POWER_HOUR_VOLUME_MULT:
            return None

        # Check if breaking previous day high/low
        if "high" not in df.columns or len(df) < 2:
            return None

        prev_high = float(df["high"].iloc[:-1].max())
        prev_low = float(df["low"].iloc[:-1].min())

        target_pct = cfg.POWER_HOUR_TARGET_PCT / 100
        stop_pct = cfg.POWER_HOUR_STOP_PCT / 100

        if close > prev_high:
            stop = close * (1 - stop_pct)
            t1 = close * (1 + target_pct)
            t2 = close * (1 + target_pct * 2)
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.LONG, StrategyName.POWER_HOUR, close, stop, t1, t2, size,
                              f"Power hour breakout high rvol={rvol:.1f}x")

        elif close < prev_low:
            stop = close * (1 + stop_pct)
            t1 = close * (1 - target_pct)
            t2 = close * (1 - target_pct * 2)
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.SHORT, StrategyName.POWER_HOUR, close, stop, t1, t2, size,
                              f"Power hour breakdown low rvol={rvol:.1f}x")

        return None

    # ── Exit Logic ────────────────────────────────────────

    def check_exits(self, symbol: str, df: pd.DataFrame) -> Optional[ExitReason]:
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]
        last = df.iloc[-1]
        close = float(last["close"])

        if pos.side == Side.LONG:
            if close <= pos.stop:
                return ExitReason.STOP
            if not pos.t1_hit and close >= pos.target1:
                pos.t1_hit = True
                return ExitReason.TARGET1
            if pos.t1_hit and close >= pos.target2:
                return ExitReason.TARGET2
        else:
            if close >= pos.stop:
                return ExitReason.STOP
            if not pos.t1_hit and close <= pos.target1:
                pos.t1_hit = True
                return ExitReason.TARGET1
            if pos.t1_hit and close <= pos.target2:
                return ExitReason.TARGET2

        return None

    # ── Trade Recording ───────────────────────────────────

    def record_entry(self, signal: Signal, now: datetime):
        self.positions[signal.symbol] = Position(
            symbol=signal.symbol,
            side=signal.side,
            strategy=signal.strategy,
            entry_price=signal.entry,
            stop=signal.stop,
            target1=signal.target1,
            target2=signal.target2,
            size=signal.size,
            entry_time=now,
        )
        self.trade_count += 1
        log.info(f"TRADE ENTERED | {signal.symbol} {signal.side.value} "
                 f"x{signal.size} @ {signal.entry:.2f} | "
                 f"Stop={signal.stop:.2f} T1={signal.target1:.2f} T2={signal.target2:.2f} | "
                 f"Strategy={signal.strategy.value} | {signal.reason}")

    def record_exit(self, symbol: str, exit_price: float, reason: ExitReason):
        if symbol not in self.positions:
            return
        pos = self.positions.pop(symbol)
        if pos.side == Side.LONG:
            pnl = (exit_price - pos.entry_price) * pos.size
        else:
            pnl = (pos.entry_price - exit_price) * pos.size

        self.daily_pnl += pnl
        stats = self.stats[pos.strategy.value]
        stats.pnl += pnl

        if pnl > 0:
            stats.wins += 1
            stats.consecutive_losses = 0
        else:
            stats.losses += 1
            stats.consecutive_losses += 1
            if stats.consecutive_losses >= cfg.MAX_CONSECUTIVE_LOSSES:
                stats.disabled = True
                log.warning(f"[STRATEGY DISABLED] {pos.strategy.value} — {cfg.MAX_CONSECUTIVE_LOSSES} consecutive losses")

        log.info(f"TRADE EXITED | {symbol} {pos.side.value} @ {exit_price:.2f} | "
                 f"P&L=${pnl:+.2f} | Reason={reason.value} | Strategy={pos.strategy.value}")

    def get_summary(self) -> str:
        lines = ["=" * 60, "  END OF DAY SUMMARY", "=" * 60]
        lines.append(f"  Total Trades: {self.trade_count}")
        lines.append(f"  Daily P&L:    ${self.daily_pnl:+,.2f}")
        lines.append(f"  VIX Regime:   {self.vix:.1f}")
        lines.append("")
        for name, s in self.stats.items():
            if s.wins + s.losses > 0:
                lines.append(f"  {name}: {s.wins}W/{s.losses}L "
                              f"WR={s.win_rate:.0%} P&L=${s.pnl:+.2f}"
                              + (" [DISABLED]" if s.disabled else ""))
        lines.append("=" * 60)
        return "\n".join(lines)
