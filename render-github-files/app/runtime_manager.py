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
    
    Ladder Stop Specification:
    +150 pts → stop at +50  (100 pts trail)
    +200 pts → stop at +100 (100 pts trail)
    +300 pts → stop at +150 (150 pts trail)
    +300+    → trails 150 pts behind price
    """
    pnl_pts = _pnl_points(pos, price)
    d = _position_dir(pos)

    # No trailing until +150 pts
    if pnl_pts < 150.0:
        return None, f"NO_TRAIL_YET pnl_pts={pnl_pts:.2f}"

    # Determine stop level based on profit
    if 150.0 <= pnl_pts < 200.0:
        # Lock in +50 pts profit
        stop_level = pos.entry_price + (50.0 * d)
        lvl = "L1_150"
    elif 200.0 <= pnl_pts < 300.0:
        # Lock in +100 pts profit
        stop_level = pos.entry_price + (100.0 * d)
        lvl = "L2_200"
    else:  # >= 300.0
        # Trail 150 pts behind current price
        stop_level = price - (150.0 * d)
        lvl = "L3_300_TRAIL"

    # For longs, stop can only move up; for shorts, only move down
    if pos.stop_price > 0:
        if d == 1:
            stop_level = max(stop_level, pos.stop_price)
        else:
            stop_level = min(stop_level, pos.stop_price)

    # Require minimum move before updating
    if pos.stop_price > 0:
        move = abs(stop_level - pos.stop_price)
        if move < MIN_STOP_MOVE_PTS:
            return None, f"TRAIL_{lvl}_SKIP small_move={move:.4f}"

        # Clamp tightening per poll
        if move > MAX_STOP_TIGHTEN_PER_POLL_PTS:
            return None, f"TRAIL_{lvl}_SKIP huge_move={move:.2f}"

    return float(stop_level), f"TRAIL_{lvl} pnl_pts={pnl_pts:.2f}"


def decide_with_runtime(machine_id: str, symbol: str) -> Tuple[RuntimeDecision, Dict]:
    """Main decision function used by /poll.

    Returns (RuntimeDecision, frames).

    Behavior:
    - If no open position, behaves like entry engine (LONG/SHORT/FLAT + initial stop)
    - If open position, manages:
        - early exit on PAMM drop
        - reversal detection (FLIP)
        - trailing stop ladder (stop_price updates)
        - stop-hit detection (best effort)
        - cooldown after stop-hit (blocks entries)
        - automatic kill switch (requires fills)
    """

    # If disabled, fall back to entry-only behavior (original /poll)
    if not RUNTIME_MANAGER_ENABLED:
        frames = load_frames(symbol)
        # Not enough candles -> FLAT
        if any(len(frames[k]) < 50 for k in ("df1", "df5", "df15", "df30")):
            return RuntimeDecision("FLAT", 0.0, "INSUFFICIENT_MULTI_TF_CANDLES", {"runtime": False}), frames

        strat = _make_strategy()
        sig = strat.decide(frames)
        if sig.side == "flat":
            return RuntimeDecision("FLAT", 0.0, sig.reason, {"runtime": False}), frames

        price = float(frames["df5"].iloc[-1]["close"])
        direction = 1 if sig.side == "buy" else -1
        stop_loss, _target = strat.get_atr_stops_targets(frames, entry_price=price, direction=direction)
        if stop_loss is None:
            return RuntimeDecision("FLAT", 0.0, "ATR_INVALID_BLOCKING_TRADE", {"runtime": False}), frames

        return RuntimeDecision("LONG" if sig.side == "buy" else "SHORT", float(stop_loss), sig.reason, {"runtime": False}), frames

    frames = load_frames(symbol)

    # Not enough candles -> FLAT
    if any(len(frames[k]) < 50 for k in ("df1", "df5", "df15", "df30")):
        return RuntimeDecision("FLAT", 0.0, "INSUFFICIENT_MULTI_TF_CANDLES", {"runtime": False}), frames

    st = STORE.get()
    pos = STORE.get_position(machine_id, symbol)

    # Automatic kill switch (daily realized P&L)
    if ENABLE_AUTO_KILL_SWITCH:
        realized = STORE.get_realized_pnl(machine_id)
        if realized <= -abs(MAX_DAILY_LOSS_USD):
            STORE.set_kill_triggered(machine_id, True)
    
    # Check consecutive losses kill switch (3 losses → stop)
    if STORE.get_consecutive_losses(machine_id) >= 3:
        STORE.set_kill_triggered(machine_id, True)
        return RuntimeDecision("FLAT", 0.0, "KILL_SWITCH_3_LOSSES", {"runtime": True, "consecutive_losses": 3}), frames

    if STORE.is_kill_triggered(machine_id) or st.kill_switch:
        return RuntimeDecision("FLAT", 0.0, "KILL_SWITCH_AUTO", {"runtime": True}), frames

    # Cooldown after stop hit
    if COOLDOWN_SECONDS > 0 and pos.last_sl_time_utc:
        if datetime.now(timezone.utc) - pos.last_sl_time_utc < timedelta(seconds=COOLDOWN_SECONDS):
            remaining = int((timedelta(seconds=COOLDOWN_SECONDS) - (datetime.now(timezone.utc) - pos.last_sl_time_utc)).total_seconds())
            return RuntimeDecision("FLAT", 0.0, f"COOLDOWN remaining_s={remaining}", {"runtime": True, "cooldown_remaining_s": remaining}), frames

    strat = _make_strategy()
    price = float(frames["df5"].iloc[-1]["close"])

    # If no open position -> entry
    if not pos.open:
        sig = strat.decide(frames)
        if sig.side == "flat":
            return RuntimeDecision("FLAT", 0.0, sig.reason, {"runtime": True, "pamm": compute_pamm_now(strat, frames)}), frames

        direction = 1 if sig.side == "buy" else -1
        stop_loss, _target = strat.get_atr_stops_targets(frames, entry_price=price, direction=direction)
        if stop_loss is None:
            return RuntimeDecision("FLAT", 0.0, "ATR_INVALID_BLOCKING_TRADE", {"runtime": True}), frames

        # Save suggested position state (will be confirmed by fill)
        pos.side = "long" if sig.side == "buy" else "short"
        pos.entry_price = price  # Suggested - will be updated by actual fill
        pos.stop_price = float(stop_loss)
        pos.initial_stop = float(stop_loss)
        pos.qty = pos.qty or 1.0
        pos.entry_time_utc = datetime.now(timezone.utc)
        pos.open = False  # NOT open until fill confirms (FIX)
        pos.last_stop_update_utc = datetime.now(timezone.utc)
        STORE.set_position(machine_id, symbol, pos)

        return RuntimeDecision("LONG" if sig.side == "buy" else "SHORT", float(stop_loss), sig.reason, {"runtime": True, "pamm": compute_pamm_now(strat, frames), "pending_entry": True}), frames

    # ----------------
    # Position management
    # ----------------

    meta: Dict = {"runtime": True}
    meta["entry_price"] = pos.entry_price
    meta["qty"] = pos.qty
    meta["pnl_pts"] = _pnl_points(pos, price)
    meta["pnl_usd_est"] = meta["pnl_pts"] * float(POINT_VALUE_USD) * float(pos.qty or 1.0)

    # 0) Catastrophic single-trade loss check (ANY trade -$5.00 → kill immediately)
    if meta["pnl_usd_est"] <= -5.00:
        pos.open = False
        STORE.set_position(machine_id, symbol, pos)
        STORE.set_kill_triggered(machine_id, True)
        return RuntimeDecision("FLAT", 0.0, f"KILL_SWITCH_SINGLE_LOSS pnl=${meta['pnl_usd_est']:.2f}", meta), frames

    # 1) Stop hit detection (best-effort; Ninja's server-side stop should still be primary)
    # 1) Stop hit detection (best-effort; Ninja's server-side stop is primary)
    # Don't start cooldown here - let fill confirmation handle it
    d = _position_dir(pos)
    if pos.stop_price > 0:
        if (d == 1 and price <= pos.stop_price) or (d == -1 and price >= pos.stop_price):
            pos.open = False
            STORE.set_position(machine_id, symbol, pos)
            return RuntimeDecision("FLAT", 0.0, f"STOP_HIT_RENDER price={price:.2f} stop={pos.stop_price:.2f}", meta), frames

    # 2) PAMM-based EARLY EXIT (ONLY before +50 pts - PF booster)
    # After +50 pts, PAMM is IGNORED (strong trends have PAMM dips)
    pamm_now = compute_pamm_now(strat, frames)
    meta["pamm"] = pamm_now
    
    if meta["pnl_pts"] < 150.0:  # Only check PAMM before +150 pts (ladder activation)
        # Rule 1: PAMM failed to reach 90 within 4 bars (20 minutes on 5m)
        bars_in_trade = 0
        if pos.entry_time_utc:
            bars_in_trade = int((datetime.now(timezone.utc) - pos.entry_time_utc).total_seconds() / 300)  # 5min bars
        
        if bars_in_trade >= 4 and pamm_now < 90:
            pos.open = False
            STORE.set_position(machine_id, symbol, pos)
            return RuntimeDecision("FLAT", 0.0, f"EARLY_EXIT_PAMM_WEAK {pamm_now:.1f}<90 after {bars_in_trade} bars", meta), frames
        
        # Rule 2: PAMM drops below 70 at any time
        if pamm_now < 70:
            pos.open = False
            STORE.set_position(machine_id, symbol, pos)
            return RuntimeDecision("FLAT", 0.0, f"EARLY_EXIT_PAMM_FAIL {pamm_now:.1f}<70", meta), frames

    # 3) Reversal detection - FLAT first, let Ninja close
    current_dir = _current_dir_from_frames(frames)
    if current_dir != d and pamm_now >= REVERSAL_PAMM_THRESHOLD:
        pos.open = False
        # Don't start cooldown here - wait for fill confirmation
        STORE.set_position(machine_id, symbol, pos)
        # Return FLAT to close current position (Ninja will re-enter opposite next poll)
        return RuntimeDecision("FLAT", 0.0, f"REVERSAL_CLOSE PAMM={pamm_now:.1f} dir_flip", meta), frames

    # 4) Trailing stop ladder
    new_stop, trail_reason = _calc_trailing_stop(pos, price)
    meta["trail_reason"] = trail_reason
    if new_stop is not None and new_stop != pos.stop_price:
        pos.stop_price = float(new_stop)
        pos.last_stop_update_utc = datetime.now(timezone.utc)
        STORE.set_position(machine_id, symbol, pos)
        # Keep signal as current direction, but return updated stop.
        return RuntimeDecision("LONG" if d == 1 else "SHORT", float(new_stop), trail_reason, meta), frames

    # Otherwise hold
    return RuntimeDecision("LONG" if d == 1 else "SHORT", float(pos.stop_price or 0.0), "HOLD", meta), frames
