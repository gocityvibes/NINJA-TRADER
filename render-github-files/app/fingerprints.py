from __future__ import annotations

"""
fingerprints.py (patched)

What changed vs your original:
- Adds decision_id, mode, timeframe to the returned fingerprint dict so you can trace a full lifecycle.
- Keeps your PAMM scoring path consistent with Strategy.decide (still calls strat._prep + strat._score_pamm).
- Adds a safe fallback if df30 is missing (uses df15) to avoid crashing when you run NO30_60 configs.
"""

from datetime import datetime, timezone
from typing import Dict, Any
import pandas as pd
from .strategy_logic import Strategy

def build_fingerprint(
    machine_id: str,
    symbol: str,
    frames: Dict[str, pd.DataFrame],
    strat: Strategy,
    signal: str,
    stop_price: float,
    reason: str,
    *,
    decision_id: str,
    mode: str = "LIVE",          # LIVE / BACKTEST / PAPER
    timeframe: str = "5m",       # your decision timeframe label
) -> Dict[str, Any]:
    ts = datetime.now(timezone.utc).isoformat()

    # Compute PAMM score + direction exactly the same way as Strategy.decide uses internally
    f1 = strat._prep(frames["df1"])
    f5 = strat._prep(frames["df5"])
    f15 = strat._prep(frames["df15"])
    df30 = frames.get("df30") or frames.get("df15")  # fallback if NO30_60
    f30 = strat._prep(df30)

    row5 = f5.iloc[-1]
    pamm_score, direction = strat._score_pamm(row5, f1.iloc[-1], f15.iloc[-1], f30.iloc[-1])

    def f(x):
        try:
            v = float(x)
            return v
        except Exception:
            return None

    return {
        "ts_utc": ts,
        "machine_id": machine_id,
        "symbol": symbol,
        "signal": signal,
        "stop_price": float(stop_price),
        "reason": reason,
        "pamm_score": float(pamm_score) if pamm_score is not None else None,
        "direction": int(direction) if direction is not None else None,
        "ema9": f(row5.get("ema9")),
        "ema21": f(row5.get("ema21")),
        "ema50": f(row5.get("ema50")),
        "rsi14": f(row5.get("rsi14")),
        "macdh": f(row5.get("macdh")),
        "adx": f(row5.get("ADX")),
        "relvol": f(row5.get("relvol")),
        "vwap": f(row5.get("vwap")),
        "atr": f(row5.get("atr")),
        "close": f(row5.get("close")),
        "decision_id": decision_id,
        "mode": mode,
        "timeframe": timeframe,
    }
