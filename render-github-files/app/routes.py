from __future__ import annotations
from fastapi import APIRouter, Depends
from datetime import datetime, timezone
from dateutil import parser as dtparser
from .models import CandleIn, CandlesIn, PollResponse, HeartbeatIn, FillIn
from .security import require_api_key
from .state import STORE
from .config import BOT_MODE, KILL_SWITCH
from . import db
from .runtime_manager import decide_with_runtime
from .fingerprints import build_fingerprint
from .strategy_logic import Strategy
from .config import (
    PAMM_MIN, PAMM_MAX, USE_VWAP, USE_REGIME_FILTER, USE_CANDLE_PATTERNS, USE_MULTI_TF_MACD,
    ATR_STOP_MULT, ATR_TARGET_MULT,
    POINT_VALUE_USD,
)

router = APIRouter()

def _strategy_for_fp():
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

@router.post("/candles")
def post_candles(payload: CandleIn | CandlesIn, _=Depends(require_api_key)):
    # allow single candle or batch
    candles = payload.candles if isinstance(payload, CandlesIn) else [payload]
    for c in candles:
        # Normalize timestamp to ISO (keep as UTC)
        try:
            ts = dtparser.isoparse(c.ts).astimezone(timezone.utc).isoformat()
        except Exception:
            ts = c.ts
        db.insert_candle(c.machineId, c.symbol, c.timeframe, ts, c.open, c.high, c.low, c.close, c.volume)
    return {"ok": True, "count": len(candles)}

@router.get("/poll", response_model=PollResponse)
def poll(machineId: str, symbol: str = "MBT", _=Depends(require_api_key)):
    st = STORE.get()
    if KILL_SWITCH or st.kill_switch:
        STORE.set_decision("FLAT", 0.0, "KILL_SWITCH")
        db.insert_fingerprint(
            ts_utc=datetime.now(timezone.utc).isoformat(),
            machine_id=machineId,
            symbol=symbol,
            signal="FLAT",
            stop_price=0.0,
            reason="KILL_SWITCH",
            pamm_score=None,
            direction=None,
            ema9=None, ema21=None, ema50=None, rsi14=None, macdh=None, adx=None, relvol=None, vwap=None, atr=None, close=None
        )
        return PollResponse(mode=st.mode, signal="FLAT", stop_price=0.0, reason="KILL_SWITCH")

    rd, frames = decide_with_runtime(machineId, symbol)
    signal, stop_price, reason = rd.signal, rd.stop_price, rd.reason

    # Always write fingerprint (even FLAT)
    fp_strat = _strategy_for_fp()
    try:
        fp = build_fingerprint(machineId, symbol, frames, fp_strat, signal, stop_price, reason)
        db.insert_fingerprint(**fp)
    except Exception as e:
        # Still respond; just record reason
        reason = f"{reason} | FP_ERR {type(e).__name__}"

    STORE.set_decision(signal, stop_price, reason)

    # Include meta for debugging / richer Ninja logic (safe to ignore)
    try:
        meta = rd.meta
    except Exception:
        meta = None
    return PollResponse(mode=st.mode, signal=signal, stop_price=stop_price, reason=reason, meta=meta)

@router.post("/heartbeat")
def heartbeat(hb: HeartbeatIn, _=Depends(require_api_key)):
    STORE.heartbeat(hb.machineId)
    db.log_heartbeat(hb.machineId, hb.ts_utc)
    return {"ok": True}

@router.post("/fills")
def fills(fill: FillIn, _=Depends(require_api_key)):
    db.log_fill(fill.machineId, fill.symbol, fill.side, fill.qty, fill.price, fill.ts_utc, fill.notes)

    # Best-effort position state tracking.
    # Convention:
    # - First fill after FLAT is considered an entry.
    # - Next opposite-side fill that reduces/closes will be treated as exit.
    # This works well for 1-contract entry/exit workflows.
    pos = STORE.get_position(fill.machineId, fill.symbol)
    side = "long" if fill.side == "BUY" else "short"
    now_utc = datetime.now(timezone.utc)

    if not pos.open:
        # Entry
        pos.side = side
        pos.entry_price = float(fill.price)
        pos.qty = float(fill.qty)
        pos.entry_time_utc = now_utc
        # Keep existing suggested stop if any; Ninja can also set it locally.
        pos.open = True
        STORE.set_position(fill.machineId, fill.symbol, pos)
    else:
        # Potential exit or add-on.
        if pos.side and side != pos.side:
            # Treat as close
            d = 1 if pos.side == "long" else -1
            pnl_pts = (float(fill.price) - float(pos.entry_price)) * d
            pnl_usd_est = pnl_pts * float(pos.qty or 1.0) * float(POINT_VALUE_USD)
            STORE.add_realized_pnl(fill.machineId, pnl_usd_est)

            # Track consecutive losses (for 3-loss kill switch)
            if pnl_usd_est < 0:
                STORE.increment_consecutive_losses(fill.machineId)
            else:
                STORE.reset_consecutive_losses(fill.machineId)

            # If exit is likely a stop-out (price beyond stop), start cooldown timer
            if pos.stop_price > 0:
                if (d == 1 and float(fill.price) <= pos.stop_price) or (d == -1 and float(fill.price) >= pos.stop_price):
                    pos.last_sl_time_utc = now_utc
            
            # Check for manual exit flag - apply cooldown for manual exits too
            if fill.notes and any(x in fill.notes.upper() for x in ["MANUAL", "TIMEOUT", "REVERSAL"]):
                pos.last_sl_time_utc = now_utc

            pos.open = False
            STORE.set_position(fill.machineId, fill.symbol, pos)
        else:
            # Same direction fill; treat as add-on, update avg entry price (simple weighted)
            new_qty = float(pos.qty) + float(fill.qty)
            if new_qty > 0:
                pos.entry_price = (float(pos.entry_price) * float(pos.qty) + float(fill.price) * float(fill.qty)) / new_qty
                pos.qty = new_qty
            STORE.set_position(fill.machineId, fill.symbol, pos)
    return {"ok": True}

@router.get("/status")
def status(_=Depends(require_api_key)):
    st = STORE.get()
    hb = {k: v.isoformat() for k, v in st.last_heartbeat_utc_by_machine.items()}
    # Summarize open positions for quick debugging
    open_positions = {}
    for k, pos in st.positions.items():
        if pos.open:
            open_positions[k] = {
                "side": pos.side,
                "entry_price": pos.entry_price,
                "stop_price": pos.stop_price,
                "qty": pos.qty,
                "entry_time_utc": pos.entry_time_utc.isoformat() if pos.entry_time_utc else None,
                "last_sl_time_utc": pos.last_sl_time_utc.isoformat() if pos.last_sl_time_utc else None,
            }
    return {
        "mode": st.mode,
        "kill_switch": st.kill_switch,
        "last_signal": st.last_signal,
        "last_stop_price": st.last_stop_price,
        "last_reason": st.last_reason,
        "heartbeats": hb,
        "open_positions": open_positions,
        "daily_realized_pnl_by_machine": st.daily_realized_pnl_by_machine,
        "kill_switch_triggered_by_machine": st.kill_switch_triggered_by_machine,
        "consecutive_losses_by_machine": st.consecutive_losses_by_machine,
    }

@router.get("/trade-log")
def trade_log(limit: int = 50, _=Depends(require_api_key)):
    return {"fills": db.get_fills(limit)}

@router.get("/fingerprints")
def fingerprints(limit: int = 200, _=Depends(require_api_key)):
    return {"fingerprints": db.get_fingerprints(limit)}
