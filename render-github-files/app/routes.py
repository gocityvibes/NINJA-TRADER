from __future__ import annotations

from fastapi import APIRouter, Depends, Request
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
async def post_candles(request: Request, _=Depends(require_api_key)):
    """
    Accept candles from Ninja in many shapes to avoid 422 validation failures.
    Supported bodies:
      - single candle object
      - {"candles":[...]} / {"bars":[...]} / {"data":[...]}
      - raw list of candle objects
    """
    body = await request.json()

    # Determine candle list
    if isinstance(body, list):
        items = body
        top = {}
    elif isinstance(body, dict):
        top = body
        if isinstance(body.get("candles"), list):
            items = body["candles"]
        elif isinstance(body.get("bars"), list):
            items = body["bars"]
        elif isinstance(body.get("data"), list):
            items = body["data"]
        else:
            items = [body]
    else:
        return {"ok": False, "count": 0, "failed": 1, "error": "Body must be JSON object or array."}

    def pick(d, *keys, default=None):
        for k in keys:
            if isinstance(d, dict) and k in d and d[k] is not None:
                return d[k]
        return default

    def norm_tf(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            v = str(int(v))
        v = str(v).strip().lower()
        mp = {
            "1": "1m", "1m": "1m", "1min": "1m",
            "5": "5m", "5m": "5m", "5min": "5m",
            "15": "15m", "15m": "15m", "15min": "15m",
            "30": "30m", "30m": "30m", "30min": "30m",
        }
        v2 = mp.get(v, v)
        return v2 if v2 in {"1m", "5m", "15m", "30m"} else None

    def norm_ts(v):
        if v is None:
            return None
        # unix ms / sec support
        if isinstance(v, (int, float)):
            ts = float(v)
            if ts > 1e12:  # likely ms
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        s = str(v).strip()
        try:
            return dtparser.isoparse(s).astimezone(timezone.utc).isoformat()
        except Exception:
            return s

    ok = 0
    failed = 0
    first_error = None

    for it in items:
        if not isinstance(it, dict):
            failed += 1
            first_error = first_error or f"Invalid candle element type: {type(it)}"
            continue

        machineId = pick(it, "machineId", "machine_id", "machine", "machineID") or pick(top, "machineId", "machine_id", "machine", "machineID")
        symbol = pick(it, "symbol", "Symbol") or pick(top, "symbol", "Symbol")
        timeframe = norm_tf(pick(it, "timeframe", "tf", "Timeframe", "barsPeriod") or pick(top, "timeframe", "tf", "Timeframe", "barsPeriod"))
        ts_raw = pick(it, "ts", "ts_utc", "timestamp", "time", "Time", "barTime") or pick(top, "ts", "ts_utc", "timestamp", "time", "Time", "barTime")
        ts = norm_ts(ts_raw)

        o = pick(it, "open", "Open")
        h = pick(it, "high", "High")
        l = pick(it, "low", "Low")
        c = pick(it, "close", "Close", "last")
        v = pick(it, "volume", "Volume", "vol")

        missing = [k for k, val in [
            ("machineId", machineId),
            ("symbol", symbol),
            ("timeframe", timeframe),
            ("ts", ts),
            ("open", o),
            ("high", h),
            ("low", l),
            ("close", c),
            ("volume", v),
        ] if val is None]

        if missing:
            failed += 1
            first_error = first_error or f"Missing fields: {', '.join(missing)}"
            continue

        try:
            db.insert_candle(
                str(machineId), str(symbol), str(timeframe), str(ts),
                float(o), float(h), float(l), float(c), float(v)
            )
            ok += 1
        except Exception as e:
            failed += 1
            first_error = first_error or f"DB insert failed: {e}"

    return {"ok": failed == 0, "count": ok, "failed": failed, "error": first_error}


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
            ema9=None, ema21=None, ema50=None, rsi14=None, macdh=None,
            adx=None, relvol=None, vwap=None, atr=None, close=None
        )
        return PollResponse(mode=st.mode, signal="FLAT", stop_price=0.0, reason="KILL_SWITCH")

    rd, frames = decide_with_runtime(machineId, symbol)
    signal, stop_price, reason = rd.signal, rd.stop_price, rd.reason

    # Always write fingerprint (even FLAT)
    fp_strat = _strategy_for_fp()
    try:
        fp = build_fingerprint(machineId, symbol, frames, fp_strat, signal, stop_price, reason)
        db.insert_fingerprint(**fp)
    except Exception:
        # never block /poll on fingerprint logging
        pass

    STORE.set_decision(signal, stop_price, reason)

    meta = None
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
    pos = STORE.get_position(fill.machineId, fill.symbol)
    side = "long" if fill.side == "BUY" else "short"
    now_utc = datetime.now(timezone.utc)

    if not pos.open:
        # Entry
        pos.side = side
        pos.entry_price = float(fill.price)
        pos.qty = float(fill.qty)
        pos.entry_time_utc = now_utc
        pos.open = True
        STORE.set_position(fill.machineId, fill.symbol, pos)
    else:
        # Potential exit or add-on.
        if pos.side and side != pos.side:
            # Treat opposite fill as exit
            pos.open = False
            pos.exit_price = float(fill.price)
            pos.exit_time_utc = now_utc
            STORE.set_position(fill.machineId, fill.symbol, pos)

    return {"ok": True}


@router.get("/status")
def status(_=Depends(require_api_key)):
    st = STORE.get()
    return {
        "mode": st.mode,
        "kill_switch": st.kill_switch,
        "last_signal": st.last_signal,
        "last_stop_price": st.last_stop_price,
        "last_reason": st.last_reason,
    }


@router.post("/reset-kill-switch")
def reset_kill_switch(machineId: str, _=Depends(require_api_key)):
    """Reset the auto kill switch for a specific machine."""
    STORE.set_kill_triggered(machineId, False)
    STORE.reset_consecutive_losses(machineId)
    return {"ok": True, "message": f"Kill switch reset for {machineId}"}
