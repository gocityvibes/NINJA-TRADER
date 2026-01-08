from __future__ import annotations
from typing import Tuple
import pandas as pd
from .strategy_logic import Strategy
from .config import (
    PAMM_MIN, PAMM_MAX, USE_VWAP, USE_REGIME_FILTER, USE_CANDLE_PATTERNS, USE_MULTI_TF_MACD,
    ATR_STOP_MULT, ATR_TARGET_MULT,
)
from .candles import load_frames

def _make_strategy() -> Strategy:
    return Strategy(
        pamm_min=PAMM_MIN,
        pamm_max=PAMM_MAX,
        use_vwap=USE_VWAP,
        use_regime_filter=USE_REGIME_FILTER,
        use_candle_patterns=USE_CANDLE_PATTERNS,
        use_multi_tf_macd=USE_MULTI_TF_MACD,
        atr_stop_mult=ATR_STOP_MULT,
        atr_target_mult=ATR_TARGET_MULT,
    )

def decide(symbol: str) -> Tuple[str, float, str, dict]:
    strat = _make_strategy()
    frames = load_frames(symbol)

    # Ensure all 4 frames have sufficient data before calling decide (Strategy also checks, but we want clean reasons)
    if any(len(frames[k]) < 50 for k in ("df1","df5","df15","df30")):
        return "FLAT", 0.0, "INSUFFICIENT_MULTI_TF_CANDLES", frames

    sig = strat.decide(frames)
    if sig.side == "flat":
        return "FLAT", 0.0, sig.reason, frames

    # Compute ATR stop based on "entry_price = last close" (pre-trade stop suggestion)
    last_close = float(frames["df5"].iloc[-1]["close"])
    direction = 1 if sig.side == "buy" else -1
    stop_loss, _target = strat.get_atr_stops_targets(frames, entry_price=last_close, direction=direction)

    if stop_loss is None:
        return "FLAT", 0.0, "ATR_INVALID_BLOCKING_TRADE", frames

    stop_price = float(stop_loss)

    return ("LONG" if sig.side == "buy" else "SHORT"), stop_price, sig.reason, frames
