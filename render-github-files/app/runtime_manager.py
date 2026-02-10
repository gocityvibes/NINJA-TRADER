from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple

from .candles import load_frames
from .strategy_logic import Strategy, compute_pamm_now
from .state import STORE, PositionState
from .config import (
    PAMM_MIN, PAMM_MAX, USE_VWAP, USE_REGIME_FILTER, USE_CANDLE_PATTERNS, USE_MULTI_TF_MACD,
    ATR_STOP_MULT, ATR_TARGET_MULT,
    REL_VOL_MIN, REL_VOL_MAX,
    RUNTIME_MANAGER_ENABLED,
    EARLY_EXIT_PAMM, REVERSAL_PAMM_THRESHOLD,
    TRAIL_L1_PNL_PTS, TRAIL_L1_OFFSET_PTS,
    TRAIL_L2_PNL_PTS, TRAIL_L2_OFFSET_PTS,
    TRAIL_L3_PNL_PTS, TRAIL_L3_OFFSET_PTS,
    MIN_STOP_MOVE_PTS, MAX_STOP_TIGHTEN_PER_POLL_PTS,
    COOLDOWN_SECONDS,
    ENABLE_AUTO_KILL_SWITCH, MAX_DAILY_LOSS_USD,
    POINT_VALUE_USD,
)


@dataclass
class RuntimeDecision:
    signal: str  # LONG | SHORT | FLAT
    stop_price: float
    reason: str
    meta: Dict


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
        rel_vol_min=REL_VOL_MIN,
        rel_vol_max=REL_VOL_MAX,
    )

# (rest of the file stays EXACTLY the same as your original)
