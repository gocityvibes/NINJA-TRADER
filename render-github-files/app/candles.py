from __future__ import annotations
from typing import Dict
import pandas as pd
from . import db

TF_MAP = {"1m":"df1", "5m":"df5", "15m":"df15", "30m":"df30"}

def load_frames(symbol: str, limit: int = 600) -> Dict[str, pd.DataFrame]:
    frames = {}
    for tf, key in TF_MAP.items():
        rows = db.get_recent_candles(symbol, tf, limit=limit)
        if not rows:
            frames[key] = pd.DataFrame(columns=["open","high","low","close","volume"])
            continue
        df = pd.DataFrame(rows, columns=["ts","open","high","low","close","volume"])
        # keep ts as string; strategy doesn't require datetime index
        df = df[["open","high","low","close","volume"]]
        frames[key] = df
    return frames
