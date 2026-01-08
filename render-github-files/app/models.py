from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Literal, Optional

Timeframe = Literal["1m","5m","15m","30m"]
Signal = Literal["LONG","SHORT","FLAT"]
Mode = Literal["PAPER","LIVE"]

class CandleIn(BaseModel):
    machineId: str = Field(..., description="Unique Ninja machine id")
    symbol: str = Field(..., description="e.g., MBT")
    timeframe: Timeframe
    ts: str = Field(..., description="UTC ISO timestamp, e.g. 2025-01-01T14:35:00Z")
    open: float
    high: float
    low: float
    close: float
    volume: float

class CandlesIn(BaseModel):
    candles: list[CandleIn]

class PollResponse(BaseModel):
    mode: Mode
    signal: Signal
    stop_price: float
    reason: str
    # Optional runtime metadata (ignored by older Ninja scripts)
    meta: Optional[dict] = None

class HeartbeatIn(BaseModel):
    machineId: str
    ts_utc: Optional[str] = None

class FillIn(BaseModel):
    machineId: str
    symbol: str
    side: Literal["BUY","SELL"]
    qty: float
    price: float
    ts_utc: Optional[str] = None
    notes: str = ""
