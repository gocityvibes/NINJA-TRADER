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


def _position_dir(pos: PositionState) -> int:
    return 1 if (pos.side or "").lower() == "long" else -1


def _current_dir_from_frames(frames: Dict) -> int:
    strat = _make_strategy()
    f5 = strat._prep(frames["df5"])
    return 1 if float(f5.iloc[-1]["ema9"]) >= float(f5.iloc[-1]["ema21"]) else -1


def _pnl_points(pos: PositionState, price: float) -> float:
    if not pos.open or pos.entry_price <= 0:
        return 0.0
    d = _position_dir(pos)
    return (price - pos.entry_price) * d


def _calc_trailing_stop(pos: PositionState, price: float) -> Tuple[float, str] | Tuple[None, str]:
    """Return (new_stop, ladder_reason) or (None, reason) if no update.

    4-Level Ladder (FINAL CLEAN SPEC):
    +50 pts  → stop at +20  (30 pts trail)
    +75 pts  → stop at +40  (35 pts trail)
    +100 pts → stop at +60  (40 pts trail)
    +150 pts → stop at +100 (50 pts trail)
    """
    pnl_pts = _pnl_points(pos, price)

    # No trailing until +50 pts
    if pnl_pts < 50.0:
        return None, f"NO_TRAIL_YET pnl_pts={pnl_pts:.2f}"

    # Pick ladder offset based on gain
    if 50.0 <= pnl_pts < 75.0:
        offset = 30.0  # +50 → stop at +20
        lvl = "L1"
    elif 75.0 <= pnl_pts < 100.0:
        offset = 35.0  # +75 → stop at +40
        lvl = "L2"
    elif 100.0 <= pnl_pts < 150.0:
        offset = 40.0  # +100 → stop at +60
        lvl = "L3"
    else:  # >= 150.0
        offset = 50.0  # +150 → stop at +100
        lvl = "L4"

    d = _position_dir(pos)
    # Trail behind current price by offset
    candidate = price - (offset * d)

    # For longs, stop can only move up; for shorts, only move down
    if pos.stop_price > 0:
        if d == 1:
            candidate = max(candidate, pos.stop_price)
        else:
            candidate = min(candidate, pos.stop_price)

    # Require minimum move before updating
    if pos.stop_price > 0:
        move = abs(candidate - pos.stop_price)
        if move < MIN_STOP_MOVE_PTS:
            return None, f"TRAIL_{lvl}_SKIP small_move={move:.4f}"

        # Clamp tightening per poll
        if move > MAX_STOP_TIGHTEN_PER_POLL_PTS:
            return None, f"TRAIL_{lvl}_SKIP huge_move={move:.2f}"

    return float(candidate), f"TRAIL_{lvl} offset_pts={offset} pnl_pts={pnl_pts:.2f}"


def decide_with_runtime(machine_id: str, symbol: str) -> Tuple[RuntimeDecision, Dict]:
    """Main decision function used by /poll.

    Returns (RuntimeDecision, frames).
    """
    # Kill switch (hard stop)
    if STORE.kill_switch:
        return RuntimeDecision("FLAT", 0.0, "KILL_SWITCH", {"runtime": True}), {}

    frames = load_frames(symbol)
    pos = STORE.get_position(machine_id, symbol)

    # If runtime manager disabled, fall back to base engine decision
    strat = _make_strategy()
    sig = strat.decide(frames)

    # If we are flat, consider new entry
    if not pos.open:
        # Cooldown after stop hit
        if COOLDOWN_SECONDS > 0 and pos.last_sl_time_utc:
            if datetime.now(timezone.utc) - pos.last_sl_time_utc < timedelta(seconds=COOLDOWN_SECONDS):
                remaining = int((timedelta(seconds=COOLDOWN_SECONDS) - (datetime.now(timezone.utc) - pos.last_sl_time_utc)).total_seconds())
                return RuntimeDecision("FLAT", 0.0, f"COOLDOWN remaining_s={remaining}", {"runtime": True}), frames

        if sig.side == "flat":
            return RuntimeDecision("FLAT", 0.0, sig.reason, {"runtime": True}), frames

        # Compute suggested stop from ATR
        last_close = float(frames["df5"].iloc[-1]["close"])
        direction = 1 if sig.side == "buy" else -1
        stop_loss, _target = strat.get_atr_stops_targets(frames, entry_price=last_close, direction=direction)
        if stop_loss is None:
            return RuntimeDecision("FLAT", 0.0, "ATR_INVALID_BLOCKING_TRADE", {"runtime": True}), frames

        return RuntimeDecision(
            "LONG" if sig.side == "buy" else "SHORT",
            float(stop_loss),
            sig.reason,
            {"runtime": True, "pamm": float(compute_pamm_now(frames)), "pending_entry": True}
        ), frames

    # In-position: HOLD unless trailing/early-exit triggers
    # Current market price proxy
    price = float(frames["df5"].iloc[-1]["close"])
    pnl_pts = _pnl_points(pos, price)
    pnl_usd_est = pnl_pts * (POINT_VALUE_USD / 1.0)

    # Auto kill switch check (optional)
    if ENABLE_AUTO_KILL_SWITCH:
        if STORE.daily_realized_pnl_usd <= -abs(MAX_DAILY_LOSS_USD):
            STORE.kill_switch = True
            return RuntimeDecision("FLAT", 0.0, "KILL_SWITCH_AUTO", {"runtime": True}), frames

    # Trailing ladder
    new_stop, ladder_reason = _calc_trailing_stop(pos, price)
    if new_stop is not None:
        return RuntimeDecision(
            pos.side.upper(),
            float(new_stop),
            "HOLD",
            {"runtime": True, "entry_price": pos.entry_price, "qty": pos.qty,
             "pnl_pts": float(pnl_pts), "pnl_usd_est": float(pnl_usd_est),
             "pamm": float(compute_pamm_now(frames)), "trail_reason": ladder_reason}
        ), frames

    # Default hold (no stop update)
    return RuntimeDecision(
        pos.side.upper(),
        float(pos.stop_price) if pos.stop_price else 0.0,
        "HOLD",
        {"runtime": True, "entry_price": pos.entry_price, "qty": pos.qty,
         "pnl_pts": float(pnl_pts), "pnl_usd_est": float(pnl_usd_est),
         "pamm": float(compute_pamm_now(frames)), "trail_reason": ladder_reason}
    ), frames
