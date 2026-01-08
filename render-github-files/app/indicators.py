import pandas as pd
import numpy as np

def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average"""
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0.0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0.0, np.nan)
    rsi_val = 100.0 - (100.0 / (1.0 + rs))
    return rsi_val.fillna(50.0)

def macd_hist(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """MACD Histogram"""
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd = fast_ema - slow_ema
    sig = ema(macd, signal)
    hist = macd - sig
    return hist

def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index (Wilder's ADX)"""
    plus_dm = (high.diff()).clip(lower=0.0)
    minus_dm = (-low.diff()).clip(lower=0.0)
    plus_dm[plus_dm < minus_dm] = 0.0
    minus_dm[minus_dm <= plus_dm] = 0.0

    tr1 = (high - low)
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()
    plus_di = 100.0 * (plus_dm.rolling(period).mean() / atr.replace(0.0, np.nan))
    minus_di = 100.0 * (minus_dm.rolling(period).mean() / atr.replace(0.0, np.nan))
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)) * 100.0
    adx_val = dx.rolling(period).mean()
    return adx_val.bfill().fillna(0.0)

def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range"""
    tr1 = (high - low)
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def vwap(df: pd.DataFrame) -> pd.Series:
    """Volume Weighted Average Price"""
    if "volume" not in df.columns:
        return pd.Series(index=df.index, dtype=float).fillna(0.0)
    
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    volume = df["volume"].replace(0, np.nan).fillna(0.0)
    
    cum_pv = (typical_price * volume).cumsum()
    cum_vol = volume.cumsum().replace(0, np.nan)
    
    vwap_val = cum_pv / (cum_vol + 1e-12)
    return vwap_val.ffill().fillna(0.0)

def rel_volume(volume: pd.Series, window: int = 20) -> pd.Series:
    """Relative Volume (current / average)"""
    avg_vol = volume.rolling(window, min_periods=1).mean()
    rel_vol = volume / (avg_vol.replace(0, np.nan) + 1e-12)
    return rel_vol.fillna(1.0)

# ============================================================
# CANDLE PATTERN DETECTION
# ============================================================

def detect_bullish_rejection(candle: pd.Series) -> bool:
    """
    Bullish rejection: Long lower wick, small body, closes near high
    - Lower wick > 2x body
    - Closes in upper 33% of range
    """
    o, h, l, c = float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"])
    
    body = abs(c - o)
    lower_wick = min(o, c) - l
    total_range = h - l
    
    if total_range < 1e-8:
        return False
    
    # Long lower wick
    if lower_wick < body * 2:
        return False
    
    # Closes in upper 33%
    close_position = (c - l) / total_range
    if close_position < 0.67:
        return False
    
    return True

def detect_bearish_rejection(candle: pd.Series) -> bool:
    """
    Bearish rejection: Long upper wick, small body, closes near low (EXACT MIRROR)
    - Upper wick > 2x body
    - Closes in lower 33% of range
    """
    o, h, l, c = float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"])
    
    body = abs(c - o)
    upper_wick = h - max(o, c)
    total_range = h - l
    
    if total_range < 1e-8:
        return False
    
    # Long upper wick
    if upper_wick < body * 2:
        return False
    
    # Closes in lower 33%
    close_position = (c - l) / total_range
    if close_position > 0.33:
        return False
    
    return True

def detect_bullish_engulfing(prev: pd.Series, curr: pd.Series) -> bool:
    """
    Bullish engulfing: Current green candle fully engulfs previous red candle
    - Previous candle red
    - Current candle green
    - Current body engulfs previous body
    """
    prev_o, prev_c = float(prev["open"]), float(prev["close"])
    curr_o, curr_c = float(curr["open"]), float(curr["close"])
    
    # Previous red, current green
    if prev_c >= prev_o:
        return False
    if curr_c <= curr_o:
        return False
    
    # Current engulfs previous
    if curr_o >= prev_c or curr_c <= prev_o:
        return False
    
    return True

def detect_bearish_engulfing(prev: pd.Series, curr: pd.Series) -> bool:
    """
    Bearish engulfing: Current red candle fully engulfs previous green candle (EXACT MIRROR)
    - Previous candle green
    - Current candle red
    - Current body engulfs previous body
    """
    prev_o, prev_c = float(prev["open"]), float(prev["close"])
    curr_o, curr_c = float(curr["open"]), float(curr["close"])
    
    # Previous green, current red
    if prev_c <= prev_o:
        return False
    if curr_c >= curr_o:
        return False
    
    # Current engulfs previous
    if curr_o <= prev_c or curr_c >= prev_o:
        return False
    
    return True

def detect_hammer(candle: pd.Series) -> bool:
    """
    Hammer: Small body at top, long lower wick
    - Lower wick > 2x body
    - Upper wick < 0.3x body
    - Body in upper half
    """
    o, h, l, c = float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"])
    
    body = abs(c - o)
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    
    if body < 1e-8:
        return False
    
    # Long lower wick
    if lower_wick < body * 2:
        return False
    
    # Small upper wick
    if upper_wick > body * 0.3:
        return False
    
    return True

def detect_inverted_hammer(candle: pd.Series) -> bool:
    """
    Inverted hammer: Small body at bottom, long upper wick (EXACT MIRROR)
    - Upper wick > 2x body
    - Lower wick < 0.3x body
    - Body in lower half
    """
    o, h, l, c = float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"])
    
    body = abs(c - o)
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    
    if body < 1e-8:
        return False
    
    # Long upper wick
    if upper_wick < body * 2:
        return False
    
    # Small lower wick
    if lower_wick > body * 0.3:
        return False
    
    return True
