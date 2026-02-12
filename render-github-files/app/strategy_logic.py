from dataclasses import dataclass
from typing import Dict, Tuple
import numpy as np
import pandas as pd
from .indicators import (
    ema, rsi, macd_hist, adx, vwap, rel_volume, atr,
    detect_bullish_rejection, detect_bearish_rejection,
    detect_bullish_engulfing, detect_bearish_engulfing,
    detect_hammer, detect_inverted_hammer
)

@dataclass
class Signal:
    side: str  # 'buy' or 'sell' or 'flat'
    reason: str = ""  # Why signal was generated or blocked

def compute_pamm_now(strategy, frames):
    """Returns current PAMM score using Strategy internals on the latest bars - 4 TIMEFRAMES"""
    f1 = strategy._prep(frames["df1"])
    f5 = strategy._prep(frames["df5"])
    f15 = strategy._prep(frames["df15"])
    f30 = strategy._prep(frames["df30"])
    
    if any(len(x) < 30 for x in (f1, f5, f15, f30)):
        return 0.0
    
    s, _ = strategy._score_pamm(
        f5.iloc[-1],
        f1.iloc[-1],
        f15.iloc[-1],
        f30.iloc[-1]
    )
    return s

class Strategy:
    def __init__(self, 
                 pamm_min: float,
                 pamm_max: float, 
                 adx_min: float = 22.0,  # Increased from 18 to 22
                 rel_vol_min: float = 1.2,  # Increased from 1.1 to 1.2
                 rel_vol_max: float = 2.0,
                 rsi_long_min: float = 52.0,
                 rsi_short_max: float = 48.0,
                 use_vwap: bool = True,
                 use_regime_filter: bool = True,
                 use_candle_patterns: bool = True,
                 use_multi_tf_macd: bool = True,
                 atr_stop_mult: float = 2.0,  # ATR multiplier for stops
                 atr_target_mult: float = 3.0):  # ATR multiplier for targets
        self.pamm_min = pamm_min
        self.pamm_max = pamm_max
        self.adx_min = adx_min
        self.rel_vol_min = rel_vol_min
        self.rel_vol_max = rel_vol_max
        self.rsi_long_min = rsi_long_min
        self.rsi_short_max = rsi_short_max
        self.use_vwap = use_vwap
        # Elite filters
        self.use_regime_filter = use_regime_filter
        self.use_candle_patterns = use_candle_patterns
        self.use_multi_tf_macd = use_multi_tf_macd
        # ATR-based risk management
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult

    def _prep(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all technical indicators"""
        out = df.copy()
        
        # Ensure we have required columns
        if not {"open", "high", "low", "close", "volume"}.issubset(out.columns):
            raise ValueError("DataFrame must have OHLCV columns")
        
        # Calculate indicators
        out["ema9"] = ema(out["close"], 9)
        out["ema21"] = ema(out["close"], 21)
        out["ema50"] = ema(out["close"], 50)  # For regime filter
        out["rsi14"] = rsi(out["close"], 14)
        out["macdh"] = macd_hist(out["close"])
        out["ADX"] = adx(out["high"], out["low"], out["close"], 14)
        out["vwap"] = vwap(out)
        out["relvol"] = rel_volume(out["volume"], 20)
        
        return out

    def _score_pamm(self, 
                    row5: pd.Series,
                    row1: pd.Series,
                    row15: pd.Series,
                    row30: pd.Series) -> Tuple[float, int]:
        """
        PAMM Score calculation - 4 TIMEFRAMES
        - Primary timeframe: 5min
        - Confirmation: 1min, 15min, 30min
        """
        score = 0.0

        # Get EMA values from each timeframe
        e9_5, e21_5 = float(row5["ema9"]), float(row5["ema21"])
        e9_1, e21_1 = float(row1["ema9"]), float(row1["ema21"])
        e9_15, e21_15 = float(row15["ema9"]), float(row15["ema21"])
        e9_30, e21_30 = float(row30["ema9"]), float(row30["ema21"])

        # Determine direction for each timeframe (1 = bullish, -1 = bearish)
        dir5 = 1 if e9_5 >= e21_5 else -1
        dir1 = 1 if e9_1 >= e21_1 else -1
        dir15 = 1 if e9_15 >= e21_15 else -1
        dir30 = 1 if e9_30 >= e21_30 else -1

        # Timeframe agreement (1m + 5m + 15m + 30m)
        agreements = (
            int(dir1 == dir5) +
            int(dir15 == dir5) +
            int(dir30 == dir5)
        )
        score += agreements * 10.0  # 10 points per agreement (max 30)

        # Momentum via RSI deviation and MACD hist across all 4 timeframes
        for r in (row1, row5, row15, row30):
            # RSI deviation from 50 (max 10 points)
            rsi_pts = max(0.0, min(10.0, (abs(float(r["rsi14"]) - 50.0) / 20.0) * 10.0))
            # MACD histogram (10 points if non-zero)
            macd_pts = 10.0 if abs(float(r["macdh"])) > 0 else 0.0
            score += rsi_pts + macd_pts

        # ADX contribution (from 5min frame, max 10 points)
        adx_val = float(row5.get("ADX", 0.0))
        if np.isfinite(adx_val):
            score += max(0.0, min(10.0, (adx_val / 25.0) * 10.0))

        return float(score), dir5

    # ============================================================
    # ELITE FILTERS (5 Filters)
    # ============================================================

    def _check_regime_filter(self, f5: pd.DataFrame, f30: pd.DataFrame) -> Tuple[bool, int, str]:
            """
            FILTER 1: Market Regime Filter (Trend Gate) - 4 TIMEFRAMES

            Uses EMAs to determine market regime:
            - 5m EMA9 vs EMA50
            - 30m EMA9 vs EMA50

            Returns:
                (pass, direction, reason)
                direction: 1 = uptrend (long only), -1 = downtrend (short only), 0 = mixed (no trades)
            """
            if not self.use_regime_filter:
                return True, 0, "Regime filter disabled"

            # Check if we have enough data
            if any(len(df) < 50 for df in [f5, f30]):
                return False, 0, "Insufficient data for regime filter"

            # Get latest EMAs
            ema9_5 = float(f5.iloc[-1]["ema9"])
            ema50_5 = float(f5.iloc[-1]["ema50"])

            ema9_30 = float(f30.iloc[-1]["ema9"])
            ema50_30 = float(f30.iloc[-1]["ema50"])

            # Determine direction for each timeframe
            dir5 = 1 if ema9_5 > ema50_5 else -1
            dir30 = 1 if ema9_30 > ema50_30 else -1

            # Require 5m and 30m to agree
            if dir5 == dir30:
                return True, dir5, f"{'Uptrend' if dir5 == 1 else 'Downtrend'} (5m+30m aligned)"
            else:
                # Mixed regime - no trades
                return False, 0, "Mixed regime (5m and 30m disagree)"


    def _check_volume_confirmation(self, row5: pd.Series) -> Tuple[bool, str]:
        """
        FILTER 2: Volume Confirmation (RelVol)
        
        Only trade when volume > 1.2x average
        """
        relvol = float(row5.get("relvol", 0.0))
        
        if not np.isfinite(relvol):
            return False, "Invalid RelVol"
        
        if relvol < self.rel_vol_min:
            return False, f"Low volume (RelVol {relvol:.2f} < {self.rel_vol_min})"
        
        if relvol > self.rel_vol_max:
            return False, f"Volume spike (RelVol {relvol:.2f} > {self.rel_vol_max})"
        
        return True, f"Volume OK (RelVol {relvol:.2f})"
    
    def _check_adx_gate(self, row5: pd.Series) -> Tuple[bool, str]:
        """
        FILTER 3: ADX Gate
        
        Only trade when ADX > 22 (configurable)
        """
        adx_val = float(row5.get("ADX", 0.0))
        
        if not np.isfinite(adx_val):
            return False, "Invalid ADX"
        
        if adx_val < self.adx_min:
            return False, f"Weak trend (ADX {adx_val:.1f} < {self.adx_min})"
        
        return True, f"Strong trend (ADX {adx_val:.1f})"
    
    def _check_multi_tf_macd(self, f5: pd.DataFrame, f15: pd.DataFrame, 
                             f30: pd.DataFrame, direction: int) -> Tuple[bool, str]:
        """
        FILTER 4: Multi-TF MACD Agreement - 4 TIMEFRAMES
        
        - 5m MACD must match direction
        - 15m MACD must NOT contradict
        - 30m MACD ideally supports same direction
        
        Args:
            direction: 1 for long, -1 for short
        """
        if not self.use_multi_tf_macd:
            return True, "MACD filter disabled"
        
        macd_5 = float(f5.iloc[-1]["macdh"])
        macd_15 = float(f15.iloc[-1]["macdh"])
        macd_30 = float(f30.iloc[-1]["macdh"])
        
        # 5m MACD must match direction
        if direction == 1 and macd_5 <= 0:
            return False, "5m MACD bearish (blocking long)"
        if direction == -1 and macd_5 >= 0:
            return False, "5m MACD bullish (blocking short)"
        
        # 15m MACD must not contradict strongly
        if direction == 1 and macd_15 < -50:  # Strong bearish on 15m
            return False, "15m MACD strongly bearish (blocking long)"
        if direction == -1 and macd_15 > 50:  # Strong bullish on 15m
            return False, "15m MACD strongly bullish (blocking short)"
        
        # 30m MACD should support (preferred but not required)
        macd_30_supports = (direction == 1 and macd_30 > 0) or (direction == -1 and macd_30 < 0)
        
        if macd_30_supports:
            return True, "MACD 5m+30m aligned"
        else:
            return True, "MACD acceptable (5m+15m OK)"
    
    def _check_candle_pattern(self, df: pd.DataFrame, direction: int) -> Tuple[bool, str]:
        """
        FILTER 5: Candle Pattern Filter
        
        Long only on bullish patterns:
        - Bullish rejection
        - Bullish engulfing
        - Hammer
        
        Short only on bearish patterns:
        - Bearish rejection
        - Bearish engulfing  
        - Inverted hammer
        
        Args:
            df: DataFrame with OHLC data
            direction: 1 for long, -1 for short
        """
        if not self.use_candle_patterns:
            return True, "Candle filter disabled"
        
        if len(df) < 2:
            return False, "Insufficient candles"
        
        # Get last 2 candles
        last_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]
        
        if direction == 1:
            # Check for bullish patterns
            if detect_bullish_rejection(last_candle):
                return True, "Bullish rejection"
            if detect_bullish_engulfing(prev_candle, last_candle):
                return True, "Bullish engulfing"
            if detect_hammer(last_candle):
                return True, "Hammer"
            return False, "No bullish pattern"
        
        else:  # direction == -1
            # Check for bearish patterns (EXACT MIRROR)
            if detect_bearish_rejection(last_candle):
                return True, "Bearish rejection"
            if detect_bearish_engulfing(prev_candle, last_candle):
                return True, "Bearish engulfing"
            if detect_inverted_hammer(last_candle):
                return True, "Inverted hammer"
            return False, "No bearish pattern"

    def decide(self, frames: Dict[str, pd.DataFrame]) -> Signal:
        """
        Decision logic with ALL ELITE FILTERS applied - 4 TIMEFRAMES
        
        Entry timeframe: 5min ONLY
        Confirmation timeframes: 1min, 15min, 30min
        
        Filter sequence:
        1. Data validation
        2. Market Regime Filter (trend gate)
        3. PAMM score range
        4. ADX Gate (trend strength)
        5. Volume Confirmation (RelVol)
        6. RSI directional filter
        7. VWAP alignment
        8. Multi-TF MACD agreement
        9. Candle pattern confirmation
        
        Returns:
            Signal with side ('buy', 'sell', 'flat') and reason
        """
        # Build indicators on each frame - 4 TIMEFRAMES
        f1 = self._prep(frames["df1"])
        f5 = self._prep(frames["df5"])
        f15 = self._prep(frames["df15"])
        f30 = self._prep(frames["df30"])

        # Ensure sufficient history
        if any(len(x) < 50 for x in (f1, f5, f15, f30)):
            return Signal(side="flat", reason="Insufficient data")

        row5 = f5.iloc[-1]
        
        # ============================================================
        # FILTER 1: Market Regime Filter
        # ============================================================
        regime_pass, regime_dir, regime_reason = self._check_regime_filter(f5, f30)
        if not regime_pass:
            return Signal(side="flat", reason=regime_reason)
        
        # ============================================================
        # Calculate PAMM score (PRIMARY ENTRY SIGNAL)
        # ============================================================
        pamm_score, dir5 = self._score_pamm(
            row5,
            f1.iloc[-1],
            f15.iloc[-1],
            f30.iloc[-1]
        )
        
        # If regime filter is on, direction must match regime
        if self.use_regime_filter and regime_dir != 0:
            if dir5 != regime_dir:
                return Signal(side="flat", reason=f"PAMM direction conflicts with regime")
        
        # FILTER 2: PAMM range check
        if pamm_score < self.pamm_min or pamm_score > self.pamm_max:
            return Signal(side="flat", reason=f"PAMM {pamm_score:.1f} outside range [{self.pamm_min}, {self.pamm_max}]")
        
        # ============================================================
        # FILTER 3: ADX Gate (trend strength)
        # ============================================================
        adx_pass, adx_reason = self._check_adx_gate(row5)
        if not adx_pass:
            return Signal(side="flat", reason=adx_reason)
        
        # ============================================================
        # FILTER 4: Volume Confirmation
        # ============================================================
        vol_pass, vol_reason = self._check_volume_confirmation(row5)
        if not vol_pass:
            return Signal(side="flat", reason=vol_reason)
        
        # ============================================================
        # FILTER 5: RSI directional filter (5min frame)
        # ============================================================
        rsi5 = float(row5["rsi14"])
        use_long = (dir5 == 1)
        
        if use_long and rsi5 < self.rsi_long_min:
            return Signal(side="flat", reason=f"RSI {rsi5:.1f} too low for long")
        if (not use_long) and rsi5 > self.rsi_short_max:
            return Signal(side="flat", reason=f"RSI {rsi5:.1f} too high for short")
        
        # ============================================================
        # FILTER 6: VWAP alignment check (5min frame)
        # ============================================================
        if self.use_vwap and "vwap" in row5:
            vwap_val = float(row5["vwap"])
            close_val = float(row5["close"])
            ema9_val = float(row5["ema9"])
            
            if use_long:
                # For longs: price AND EMA9 must be above VWAP
                if not (close_val >= vwap_val and ema9_val >= vwap_val):
                    return Signal(side="flat", reason="Price/EMA9 below VWAP (blocking long)")
            else:
                # For shorts: price AND EMA9 must be below VWAP (EXACT MIRROR)
                if not (close_val <= vwap_val and ema9_val <= vwap_val):
                    return Signal(side="flat", reason="Price/EMA9 above VWAP (blocking short)")
        
        # ============================================================
        # FILTER 7: Multi-TF MACD Agreement
        # ============================================================
        macd_pass, macd_reason = self._check_multi_tf_macd(f5, f15, f30, dir5)
        if not macd_pass:
            return Signal(side="flat", reason=macd_reason)
        
        # ============================================================
        # FILTER 8: Candle Pattern Confirmation
        # ============================================================
        pattern_pass, pattern_reason = self._check_candle_pattern(f5, dir5)
        if not pattern_pass:
            return Signal(side="flat", reason=pattern_reason)
        
        # ============================================================
        # ALL FILTERS PASSED - GENERATE SIGNAL
        # ============================================================
        side = "buy" if dir5 == 1 else "sell"
        
        # Build comprehensive reason
        reason = (
            f"PAMM {pamm_score:.1f} | {regime_reason} | {adx_reason} | "
            f"{vol_reason} | RSI {rsi5:.1f} | {macd_reason} | {pattern_reason}"
        )
        
        return Signal(side=side, reason=reason)
    
    def get_atr_stops_targets(self, frames: Dict[str, pd.DataFrame], 
                              entry_price: float, direction: int) -> Tuple[float, float]:
        """Calculate fixed 50-point stop loss. No target - ladder stops handle exits.

        Rules:
        - Fixed 50-point stop loss for both LONG and SHORT
        - No profit target (ladder stops manage exits)
        """
        # Fixed 50-point stop loss
        if direction == 1:
            # Long position: stop 50 points below entry
            stop_loss = entry_price - 50.0
        else:
            # Short position: stop 50 points above entry
            stop_loss = entry_price + 50.0

        # No target - return 0 (ladder stops handle profit-taking)
        return stop_loss, 0.0

