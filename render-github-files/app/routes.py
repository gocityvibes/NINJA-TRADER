from __future__ import annotations
from fastapi import APIRouter, Depends
from datetime import datetime, timezone
import uuid
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
        point_value_usd=POINT_VALUE_USD,
    )

@router.post("/candles")
def ingest_candles(payload: CandlesIn, _=Depends(require_api_key)):
    candles = payload.candles or []
    for c in candles:
        ts = dtparser.isoparse(c.ts_utc).astimezone(timezone.utc).isoformat()
        db.insert_candle(c.machine_id, c.symbol, c.timeframe, ts, c.open, c.high, c.low, c.close, c.volume)
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
        fp = build_fingerprint(machineId, symbol, frames, fp_strat, signal, stop_price, reason, decision_id=uuid.uuid4().hex)
        db.insert_fingerprint(**fp)
    except Exception as e:
        # Still respond; just record reason
        reason = f"{reason} | FP_ERR {type(e).__name__}"

    STORE.set_decision(signal, stop_price, reason)

    # Include meta for debugging / richer Ninja logic (safe to ignore)
    try:
        meta = rd.meta
    except Exception:
        meta = {}

    return PollResponse(
        mode=BOT_MODE,
        signal=signal,
        stop_price=stop_price,
        reason=reason,
        meta=meta,
    )

@router.post("/heartbeat")
def heartbeat(payload: HeartbeatIn, _=Depends(require_api_key)):
    STORE.heartbeat(payload.machine_id, payload.mode)
    return {"ok": True}

@router.post("/fills")
def fills(payload: FillIn, _=Depends(require_api_key)):
    # record fill
    db.insert_fill(
        ts_utc=payload.ts_utc,
        machine_id=payload.machine_id,
        symbol=payload.symbol,
        side=payload.side,
        qty=payload.qty,
        price=payload.price,
        order_id=payload.order_id,
        client_order_id=payload.client_order_id,
        meta=payload.meta or {},
    )
    # update in-memory position state
    STORE.apply_fill(payload)
    return {"ok": True}

@router.get("/status")
def status(_=Depends(require_api_key)):
    st = STORE.get()
    return {
        "mode": st.mode,
        "signal": st.last_signal,
        "stop_price": st.last_stop_price,
        "reason": st.last_reason,
        "kill_switch": st.kill_switch,
        "kill_switch_triggered_by_machine": st.kill_switch_triggered_by_machine,
        "consecutive_losses_by_machine": st.consecutive_losses_by_machine,
    }

@router.get("/trade-log")
def trade_log(limit: int = 50, _=Depends(require_api_key)):
    return {"fills": db.get_fills(limit)}

@router.get("/fingerprints")
def fingerprints(limit: int = 200, _=Depends(require_api_key)):
    return {"fingerprints": db.get_fingerprints(limit)}
