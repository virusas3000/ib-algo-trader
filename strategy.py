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
    VWAP_RECLAIM = "VWAP_RECLAIM"
    GAP_AND_GO = "GAP_AND_GO"
    BULL_FLAG = "BULL_FLAG"
    RSI_EXTREME = "RSI_EXTREME"
    HOD_BREAKOUT = "HOD_BREAKOUT"
    EMA_CROSS = "EMA_CROSS"
    # Andrew Aziz strategies
    ABCD_PATTERN = "ABCD_PATTERN"
    RED_TO_GREEN = "RED_TO_GREEN"
    BOTTOM_REVERSAL = "BOTTOM_REVERSAL"
    FALLEN_ANGEL = "FALLEN_ANGEL"
    MA_TREND = "MA_TREND"


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
        self._exit_cooldown: Dict[str, datetime] = {}  # symbol → time of last exit
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
        """Only ORB when VIX > 25. Honors DISABLED_STRATEGIES from config."""
        disabled_manual = set(getattr(cfg, 'DISABLED_STRATEGIES', []))
        if self.vix > cfg.VIX_HIGH:
            return [s for s in [StrategyName.ORB] if s.value not in disabled_manual]
        return [s for s in StrategyName
                if not self.stats[s.value].disabled
                and s.value not in disabled_manual]

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
        # Long-only / Short-only filter
        if getattr(cfg, 'SHORT_ONLY', False) and side == Side.LONG:
            return False
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
        # Skip ORB during dead-zone hours (post-lunch chop, pre-power-hour)
        block_hours = set(getattr(cfg, 'ORB_BLOCK_HOURS_ET', []))
        if hasattr(now, 'hour') and now.hour in block_hours:
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

    # ── VWAP Reclaim ─────────────────────────────────────
    # Price dips below VWAP then closes back above — high win rate 62-68%

    def check_vwap_reclaim(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.VWAP_RECLAIM, Side.LONG):
            return None
        if symbol in self.positions:
            return None
        # 9:45 AM - 3:00 PM ET only
        t = now.time()
        if not (time(9, 45) <= t <= time(15, 0)):
            return None
        if len(df) < 3:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]
        close = float(last["close"])
        vwap_val = float(last.get("vwap", close))
        prev_close = float(prev["close"])
        prev_vwap = float(prev.get("vwap", prev_close))
        rvol = float(last.get("rvol", 1.0))
        atr = float(last.get("atr14", close * 0.01))
        rsi_val = float(last.get("rsi14", 50))

        # Reclaim: prev bar closed BELOW vwap, current bar closes ABOVE vwap
        was_below = prev_close < prev_vwap
        now_above = close > vwap_val
        volume_ok = rvol >= 1.3
        rsi_ok = 35 < rsi_val < 70  # not extreme — clean reclaim

        if was_below and now_above and volume_ok and rsi_ok:
            stop = vwap_val - atr * 0.8  # tight stop just below vwap
            t1 = close + atr * 1.5
            t2 = close + atr * 2.5
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.LONG, StrategyName.VWAP_RECLAIM, close, stop, t1, t2, size,
                              f"VWAP reclaim rvol={rvol:.1f}x rsi={rsi_val:.0f}")
        return None

    # ── Gap & Go ──────────────────────────────────────────
    # News-driven gap 2%+ with volume — buy first 5-min candle high

    def check_gap_and_go(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.GAP_AND_GO, Side.LONG):
            return None
        if symbol in self.positions:
            return None
        if symbol not in self.prev_closes:
            return None
        # Only 9:45 - 10:15 AM ET
        t = now.time()
        if not (time(9, 45) <= t <= time(10, 15)):
            return None
        if len(df) < 2:
            return None

        prev_close = self.prev_closes[symbol]
        open_price = float(df.iloc[0]["open"])
        last = df.iloc[-1]
        close = float(last["close"])
        first_candle_high = float(df.iloc[0]["high"])
        first_candle_low = float(df.iloc[0]["low"])
        rvol = float(last.get("rvol", 1.0))
        atr = float(last.get("atr14", close * 0.01))
        vwap_val = float(last.get("vwap", close))

        gap_pct = (open_price - prev_close) / prev_close * 100

        # Gap up 2%+ with strong volume and price above VWAP
        if gap_pct >= 2.0 and rvol >= 2.0 and close > vwap_val:
            # Entry: break of first 5-min candle high
            if close > first_candle_high:
                stop = first_candle_low  # stop below first candle low
                t1 = close + (close - stop) * 1.5
                t2 = close + (close - stop) * 2.5
                rr = (t1 - close) / (close - stop) if close != stop else 0
                if rr < 1.2:
                    return None
                size = self.calc_size(close, stop)
                if size > 0:
                    return Signal(symbol, Side.LONG, StrategyName.GAP_AND_GO, close, stop, t1, t2, size,
                                  f"Gap&Go gap={gap_pct:.1f}% rvol={rvol:.1f}x")
        return None

    # ── Bull Flag / First Pullback ────────────────────────
    # Strong open move up → first consolidation → buy flag breakout

    def check_bull_flag(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.BULL_FLAG, Side.LONG):
            return None
        if symbol in self.positions:
            return None
        # 10:00 AM - 2:00 PM ET
        t = now.time()
        if not (time(10, 0) <= t <= time(14, 0)):
            return None
        if len(df) < 8:
            return None

        last = df.iloc[-1]
        close = float(last["close"])
        ema9_val = float(last.get("ema9", close))
        rvol = float(last.get("rvol", 1.0))
        atr = float(last.get("atr14", close * 0.01))
        vwap_val = float(last.get("vwap", close))

        # Need strong prior move: first candle of day up >1%
        open_price = float(df.iloc[0]["open"])
        day_move_pct = (close - open_price) / open_price * 100
        if day_move_pct < 0.8:
            return None

        # Consolidation: last 3-5 bars tightening (low range = flag)
        flag_bars = df.iloc[-5:-1]
        flag_range = float(flag_bars["high"].max() - flag_bars["low"].min())
        initial_move = float(df.iloc[0]["high"] - df.iloc[0]["low"])
        is_flag = flag_range < initial_move * 0.6  # flag range < 60% of first bar

        # Breakout: close above flag high with returning volume
        flag_high = float(flag_bars["high"].max())
        breakout = close > flag_high
        price_above_vwap = close > vwap_val
        price_above_ema9 = close > ema9_val

        if is_flag and breakout and price_above_vwap and price_above_ema9 and rvol >= 1.4:
            stop = float(flag_bars["low"].min())  # below flag low
            t1 = close + atr * 1.5
            t2 = close + atr * 2.5
            if close - stop < atr * 0.3:  # stop too tight
                return None
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.LONG, StrategyName.BULL_FLAG, close, stop, t1, t2, size,
                              f"Bull flag breakout rvol={rvol:.1f}x day_move={day_move_pct:.1f}%")
        return None

    # ── RSI Extreme Mean Reversion ────────────────────────
    # RSI < 25 or > 75 on 5-min — fade the extremes on ranging days

    def check_rsi_extreme(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.RSI_EXTREME):
            return None
        if symbol in self.positions:
            return None
        # Only during low-VIX ranging hours 10:30 AM - 2:30 PM ET
        t = now.time()
        if not (time(10, 30) <= t <= time(14, 30)):
            return None
        # Only when VIX is low (ranging market)
        if self.vix > 20:
            return None
        if len(df) < 5:
            return None

        last = df.iloc[-1]
        close = float(last["close"])
        rsi_val = float(last.get("rsi14", 50))
        bb_lower = float(last.get("bb_lower", close * 0.98))
        bb_upper = float(last.get("bb_upper", close * 1.02))
        vwap_val = float(last.get("vwap", close))
        atr = float(last.get("atr14", close * 0.01))
        rvol = float(last.get("rvol", 1.0))

        # RSI extreme oversold: fade down move back to VWAP
        if rsi_val < 25 and close <= bb_lower and rvol < 2.0:
            # Volume declining = exhaustion, not breakdown
            stop = close - atr * 1.0
            t1 = vwap_val
            t2 = vwap_val + atr * 0.5
            rr = (t1 - close) / (close - stop) if close != stop else 0
            if rr >= 1.2:
                size = self.calc_size(close, stop)
                if size > 0:
                    return Signal(symbol, Side.LONG, StrategyName.RSI_EXTREME, close, stop, t1, t2, size,
                                  f"RSI extreme oversold rsi={rsi_val:.0f} vol_declining")

        # RSI extreme overbought: fade up move back to VWAP
        if not getattr(cfg, 'LONG_ONLY', False):
            if rsi_val > 75 and close >= bb_upper and rvol < 2.0:
                stop = close + atr * 1.0
                t1 = vwap_val
                t2 = vwap_val - atr * 0.5
                rr = (close - t1) / (stop - close) if stop != close else 0
                if rr >= 1.2:
                    size = self.calc_size(close, stop)
                    if size > 0:
                        return Signal(symbol, Side.SHORT, StrategyName.RSI_EXTREME, close, stop, t1, t2, size,
                                      f"RSI extreme overbought rsi={rsi_val:.0f} vol_declining")
        return None

    # ── HOD Breakout ──────────────────────────────────────
    # New high of day on 2x+ volume — momentum continuation

    def check_hod_breakout(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.HOD_BREAKOUT, Side.LONG):
            return None
        if symbol in self.positions:
            return None
        # 10:00 AM - 3:30 PM ET
        t = now.time()
        if not (time(10, 0) <= t <= time(15, 30)):
            return None
        if len(df) < 10:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]
        close = float(last["close"])
        high = float(last["high"])
        prev_hod = float(prev.get("hod", high))
        rvol = float(last.get("rvol", 1.0))
        atr = float(last.get("atr14", close * 0.01))
        vwap_val = float(last.get("vwap", close))
        rsi_val = float(last.get("rsi14", 50))

        # New HOD: current high exceeds all prior highs
        is_new_hod = high > prev_hod
        strong_volume = rvol >= 2.0
        above_vwap = close > vwap_val
        rsi_ok = rsi_val < 78  # not too overbought

        if is_new_hod and strong_volume and above_vwap and rsi_ok:
            # Tight stop just below the breakout candle
            stop = float(last["low"])
            if close - stop < atr * 0.2:
                stop = close - atr * 0.5  # ensure meaningful stop
            t1 = close + atr * 1.5
            t2 = close + atr * 2.5
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.LONG, StrategyName.HOD_BREAKOUT, close, stop, t1, t2, size,
                              f"HOD breakout new_high={high:.2f} rvol={rvol:.1f}x")
        return None

    # ── EMA 9/21 Cross + Volume ───────────────────────────
    # 9 EMA crosses above 21 EMA with 1.5x+ volume — trend following

    def check_ema_cross(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.EMA_CROSS):
            return None
        if symbol in self.positions:
            return None
        # After 10:00 AM ET, before 3:30 PM
        t = now.time()
        if not (time(10, 0) <= t <= time(15, 30)):
            return None
        if len(df) < 3:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]
        close = float(last["close"])
        ema9_now = float(last.get("ema9", close))
        ema21_now = float(last.get("ema21", close))
        ema9_prev = float(prev.get("ema9", close))
        ema21_prev = float(prev.get("ema21", close))
        rvol = float(last.get("rvol", 1.0))
        atr = float(last.get("atr14", close * 0.01))
        vwap_val = float(last.get("vwap", close))
        macd = float(last.get("macd", 0))
        macd_sig = float(last.get("macd_signal", 0))

        # Bullish cross: 9 EMA just crossed above 21 EMA
        bullish_cross = ema9_prev <= ema21_prev and ema9_now > ema21_now
        volume_confirm = rvol >= 1.5
        above_vwap = close > vwap_val
        macd_confirm = macd > macd_sig  # MACD confirming

        if bullish_cross and volume_confirm and above_vwap and macd_confirm:
            stop = ema21_now - atr * 0.5  # stop below 21 EMA
            t1 = close + atr * 1.5
            t2 = close + atr * 3.0  # trail on 9 EMA
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.LONG, StrategyName.EMA_CROSS, close, stop, t1, t2, size,
                              f"EMA 9/21 cross rvol={rvol:.1f}x macd_bullish")

        if not getattr(cfg, 'LONG_ONLY', False):
            bearish_cross = ema9_prev >= ema21_prev and ema9_now < ema21_now
            below_vwap = close < vwap_val
            macd_bear = macd < macd_sig
            if bearish_cross and volume_confirm and below_vwap and macd_bear:
                stop = ema21_now + atr * 0.5
                t1 = close - atr * 1.5
                t2 = close - atr * 3.0
                size = self.calc_size(close, stop)
                if size > 0:
                    return Signal(symbol, Side.SHORT, StrategyName.EMA_CROSS, close, stop, t1, t2, size,
                                  f"EMA 9/21 cross bearish rvol={rvol:.1f}x")
        return None

    # ══════════════════════════════════════════════════════
    # ANDREW AZIZ — "Day Trading for a Living" Strategies
    # ══════════════════════════════════════════════════════

    # ── ABCD Pattern ─────────────────────────────────────
    # A=spike, B=pullback on low vol, C=second push, D=second pullback → entry
    # Most important Aziz setup. ~65% win rate on strong momentum stocks.

    def check_abcd_pattern(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.ABCD_PATTERN, Side.LONG):
            return None
        if symbol in self.positions:
            return None
        # 9:45 AM - 11:30 AM ET prime time + 2:00-3:30 PM
        t = now.time()
        prime = time(9, 45) <= t <= time(11, 30)
        afternoon = time(14, 0) <= t <= time(15, 30)
        if not (prime or afternoon):
            return None
        if len(df) < 10:
            return None

        closes = df["close"].values
        volumes = df["volume"].values
        highs = df["high"].values
        lows = df["low"].values
        atr = float(df.iloc[-1].get("atr14", closes[-1] * 0.01))
        vwap_val = float(df.iloc[-1].get("vwap", closes[-1]))
        rvol = float(df.iloc[-1].get("rvol", 1.0))

        # Detect A: highest point in last 10 bars
        a_idx = int(df["high"].iloc[-10:].argmax()) + len(df) - 10
        a_price = float(df.iloc[a_idx]["high"])

        if a_idx >= len(df) - 2:
            return None  # too recent, no pattern yet

        # B: lowest point after A (pullback)
        b_slice = df.iloc[a_idx:]
        if len(b_slice) < 3:
            return None
        b_idx = a_idx + int(b_slice["low"].argmin())
        b_price = float(df.iloc[b_idx]["low"])

        # Pullback must be meaningful (20-62% of A move from base)
        a_move = a_price - float(df.iloc[max(0, a_idx-5)]["low"])
        pullback = a_price - b_price
        if a_move <= 0 or pullback / a_move < 0.20 or pullback / a_move > 0.70:
            return None

        # Volume on pullback must be LOW (drying up)
        if b_idx > a_idx:
            pullback_vol_avg = float(df.iloc[a_idx:b_idx+1]["volume"].mean())
            pre_a_vol_avg = float(df.iloc[max(0, a_idx-5):a_idx]["volume"].mean())
            if pre_a_vol_avg > 0 and pullback_vol_avg > pre_a_vol_avg * 0.8:
                return None  # volume not drying up — invalid pattern

        # C: second push up after B (must exceed A somewhat)
        if b_idx >= len(df) - 1:
            return None
        c_slice = df.iloc[b_idx:]
        c_idx = b_idx + int(c_slice["high"].argmax())
        c_price = float(df.iloc[c_idx]["high"])

        # C should be close to A (within 0.5x ATR — forming double top area)
        if abs(c_price - a_price) > atr * 2:
            return None

        # D: current pullback after C (higher low than B — bullish structure)
        current_close = float(df.iloc[-1]["close"])
        current_low = float(df.iloc[-1]["low"])
        if current_low <= b_price:
            return None  # lower low than B — structure broken

        # Entry: current close breaking above C with returning volume
        if current_close > c_price and rvol >= 1.5 and current_close > vwap_val:
            stop = current_low if current_low > b_price else b_price
            # Target = A-to-B distance projected from C (measured move)
            measured_move = a_price - b_price
            t1 = c_price + measured_move * 0.6
            t2 = c_price + measured_move * 1.0
            size = self.calc_size(current_close, stop)
            if size > 0:
                return Signal(symbol, Side.LONG, StrategyName.ABCD_PATTERN,
                              current_close, stop, t1, t2, size,
                              f"ABCD pattern A={a_price:.2f} B={b_price:.2f} C={c_price:.2f} rvol={rvol:.1f}x")
        return None

    # ── Red-to-Green Move ─────────────────────────────────
    # Stock opens red, crosses back above prev close → momentum entry

    def check_red_to_green(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.RED_TO_GREEN, Side.LONG):
            return None
        if symbol in self.positions:
            return None
        if symbol not in self.prev_closes:
            return None
        # 9:45 AM - 10:30 AM ET only
        t = now.time()
        if not (time(9, 45) <= t <= time(10, 30)):
            return None
        if len(df) < 3:
            return None

        prev_close = self.prev_closes[symbol]
        last = df.iloc[-1]
        prev_bar = df.iloc[-2]
        close = float(last["close"])
        prev_bar_close = float(prev_bar["close"])
        open_price = float(df.iloc[0]["open"])
        rvol = float(last.get("rvol", 1.0))
        atr = float(last.get("atr14", close * 0.01))
        vwap_val = float(last.get("vwap", close))

        # Stock opened RED (below prev close)
        opened_red = open_price < prev_close
        # Previous bar was still red (below prev_close)
        prev_still_red = prev_bar_close < prev_close
        # Current bar crosses GREEN (closes above prev close)
        now_green = close > prev_close
        # Volume surge on the cross
        volume_surge = rvol >= 1.8
        # Above VWAP confirms strength
        above_vwap = close > vwap_val

        if opened_red and prev_still_red and now_green and volume_surge and above_vwap:
            stop = prev_close - atr * 0.5  # stop just below the green-line
            t1 = close + atr * 1.5
            t2 = close + atr * 2.5
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.LONG, StrategyName.RED_TO_GREEN,
                              close, stop, t1, t2, size,
                              f"Red-to-green cross rvol={rvol:.1f}x prev_close={prev_close:.2f}")
        return None

    # ── Bottom Reversal ────────────────────────────────────
    # Capitulation bottom: big red candle + volume spike + hammer → long

    def check_bottom_reversal(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.BOTTOM_REVERSAL, Side.LONG):
            return None
        if symbol in self.positions:
            return None
        # Any time 9:45 AM - 3:00 PM
        t = now.time()
        if not (time(9, 45) <= t <= time(15, 0)):
            return None
        if len(df) < 6:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]
        close = float(last["close"])
        open_ = float(last["open"])
        low = float(last["low"])
        high = float(last["high"])
        prev_close = float(prev["close"])
        atr = float(last.get("atr14", close * 0.01))
        rvol = float(last.get("rvol", 1.0))
        vwap_val = float(last.get("vwap", close))

        # Require: stock is down 5%+ from open
        day_open = float(df.iloc[0]["open"])
        down_pct = (day_open - close) / day_open * 100
        if down_pct < 4.0:
            return None

        # Previous candle: big red (capitulation)
        prev_body = abs(float(prev["close"]) - float(prev["open"]))
        prev2_body = abs(float(prev2["close"]) - float(prev2["open"]))
        big_red = float(prev["close"]) < float(prev["open"]) and prev_body > atr * 0.5

        # Volume spike on previous candle (exhaustion)
        vol_spike = float(prev["volume"]) > float(df["volume"].rolling(10).mean().iloc[-1]) * 1.8

        # Current candle: hammer or doji (reversal candle)
        body = abs(close - open_)
        lower_wick = min(close, open_) - low
        upper_wick = high - max(close, open_)
        is_hammer = lower_wick > body * 1.5 and close > open_  # green hammer
        is_bullish = close > open_

        # Price reclaiming VWAP is the trigger
        reclaiming_vwap = close > vwap_val and prev_close < float(prev.get("vwap", prev_close))

        if big_red and vol_spike and (is_hammer or reclaiming_vwap) and is_bullish:
            stop = low - atr * 0.3  # below hammer low
            # Target: 38% retracement of the down move
            t1 = close + (day_open - close) * 0.38
            t2 = close + (day_open - close) * 0.61
            if t1 <= close:
                return None
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.LONG, StrategyName.BOTTOM_REVERSAL,
                              close, stop, t1, t2, size,
                              f"Bottom reversal down={down_pct:.1f}% vol_spike rvol={rvol:.1f}x")
        return None

    # ── Fallen Angel ──────────────────────────────────────
    # Stock gaps down on weak/no news, sector strong → gap fill long

    def check_fallen_angel(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.FALLEN_ANGEL, Side.LONG):
            return None
        if symbol in self.positions:
            return None
        if symbol not in self.prev_closes:
            return None
        # 9:45 AM - 10:30 AM only
        t = now.time()
        if not (time(9, 45) <= t <= time(10, 30)):
            return None
        if len(df) < 3:
            return None

        prev_close = self.prev_closes[symbol]
        open_price = float(df.iloc[0]["open"])
        last = df.iloc[-1]
        close = float(last["close"])
        rvol = float(last.get("rvol", 1.0))
        atr = float(last.get("atr14", close * 0.01))
        vwap_val = float(last.get("vwap", close))
        pre_market_high = float(df["high"].max())  # approximate

        gap_pct = (open_price - prev_close) / prev_close * 100

        # Gap down 2-8% (not catastrophic — no fundamental news)
        if not (-8.0 <= gap_pct <= -2.0):
            return None

        # Stock is now recovering — close above VWAP and pre-market high
        recovering = close > vwap_val and close > open_price
        volume_ok = rvol >= 1.5

        if recovering and volume_ok:
            stop = vwap_val - atr * 0.5
            t1 = prev_close * 0.99  # 99% of gap fill
            t2 = prev_close  # full gap fill
            if t1 <= close:
                return None
            rr = (t1 - close) / (close - stop) if close != stop else 0
            if rr < 1.2:
                return None
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.LONG, StrategyName.FALLEN_ANGEL,
                              close, stop, t1, t2, size,
                              f"Fallen angel gap={gap_pct:.1f}% recovering rvol={rvol:.1f}x")
        return None

    # ── Moving Average Trend (9/20 EMA Bounce) ───────────
    # 9 EMA above 20 EMA, price pulls back to 9 EMA → bounce entry

    def check_ma_trend(self, symbol: str, df: pd.DataFrame, now: datetime) -> Optional[Signal]:
        if not self.can_trade(StrategyName.MA_TREND, Side.LONG):
            return None
        if symbol in self.positions:
            return None
        # 9:45 AM - 3:30 PM
        t = now.time()
        if not (time(9, 45) <= t <= time(15, 30)):
            return None
        if len(df) < 5:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]
        close = float(last["close"])
        low = float(last["low"])
        ema9 = float(last.get("ema9", close))
        ema20 = float(last.get("ema20", close))
        prev_low = float(prev["low"])
        prev_ema9 = float(prev.get("ema9", close))
        atr = float(last.get("atr14", close * 0.01))
        rvol = float(last.get("rvol", 1.0))
        rsi_val = float(last.get("rsi14", 50))

        # Uptrend confirmed: 9 EMA above 20 EMA, both sloping up
        uptrend = ema9 > ema20
        ema9_sloping = ema9 > prev_ema9
        if not (uptrend and ema9_sloping):
            return None

        # Price pulled back to touch 9 EMA (previous bar touched or crossed below)
        prev_touched_ema9 = prev_low <= prev_ema9
        # Current bar bounced — closes above 9 EMA
        bounced = close > ema9 and low <= ema9 * 1.002  # within 0.2% of ema9

        # Volume returning (not dead)
        volume_ok = rvol >= 1.0
        # RSI not overbought
        rsi_ok = rsi_val < 72

        if (prev_touched_ema9 or bounced) and close > ema9 and volume_ok and rsi_ok:
            stop = ema20 - atr * 0.3  # stop below 20 EMA (Aziz rule)
            t1 = close + atr * 1.5
            t2 = close + atr * 2.5
            size = self.calc_size(close, stop)
            if size > 0:
                return Signal(symbol, Side.LONG, StrategyName.MA_TREND,
                              close, stop, t1, t2, size,
                              f"MA trend 9EMA bounce ema9={ema9:.2f} ema20={ema20:.2f} rsi={rsi_val:.0f}")
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
        # Cooldown check — skip if symbol exited within last 30 minutes
        COOLDOWN_MINUTES = 30
        last_exit = self._exit_cooldown.get(signal.symbol)
        if last_exit:
            elapsed = (datetime.now() - last_exit).total_seconds() / 60
            if elapsed < COOLDOWN_MINUTES:
                log.info(f"[COOLDOWN] Skipping {signal.symbol} — only {elapsed:.0f}min since last exit (cooldown={COOLDOWN_MINUTES}min)")
                return
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
        # Set cooldown — no re-entry for 30 min after any exit
        self._exit_cooldown[symbol] = datetime.now()
        # Auto-sync to DB immediately after each exit
        try:
            import subprocess, sys
            subprocess.Popen([sys.executable, "trade_logger.py"],
                             cwd=str(__import__('pathlib').Path(__file__).parent),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as _e:
            log.warning(f"trade_logger sync failed: {_e}")

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
