from __future__ import annotations

from typing import Literal, Optional, Any
from pydantic import BaseModel, Field, field_validator, AliasChoices, ConfigDict

# Keep server-side enums stable
Signal = Literal["LONG", "SHORT", "FLAT"]
Mode = Literal["PAPER", "LIVE"]


class CandleIn(BaseModel):
    """
    Incoming candle payload from Ninja.
    This is intentionally permissive to support different Ninja scripts.
    """
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    machineId: str = Field(
        ...,
        description="Unique Ninja machine id",
        validation_alias=AliasChoices("machineId", "machine_id", "machine", "machineID"),
    )
    symbol: str = Field(
        ...,
        description="e.g., MBT",
        validation_alias=AliasChoices("symbol", "Symbol"),
    )
    timeframe: str = Field(
        ...,
        description='Accepts "1m","5m","15m","30m" and also 1/5/15/30 or "1"/"5"/"15"/"30"',
        validation_alias=AliasChoices("timeframe", "tf", "Timeframe", "barsPeriod"),
    )
    ts: str = Field(
        ...,
        description="UTC ISO timestamp, e.g. 2025-01-01T14:35:00Z",
        validation_alias=AliasChoices("ts", "ts_utc", "timestamp", "time", "Time", "barTime"),
    )

    open: float = Field(..., validation_alias=AliasChoices("open", "Open"))
    high: float = Field(..., validation_alias=AliasChoices("high", "High"))
    low: float = Field(..., validation_alias=AliasChoices("low", "Low"))
    close: float = Field(..., validation_alias=AliasChoices("close", "Close", "last"))
    volume: float = Field(..., validation_alias=AliasChoices("volume", "Volume", "vol"))

    @field_validator("timeframe", mode="before")
    @classmethod
    def _coerce_timeframe(cls, v: Any) -> str:
        if v is None:
            return v
        if isinstance(v, (int, float)):
            v = str(int(v))
        v = str(v).strip().lower()

        mapping = {
            "1": "1m", "1m": "1m", "1min": "1m",
            "5": "5m", "5m": "5m", "5min": "5m",
            "15": "15m", "15m": "15m", "15min": "15m",
            "30": "30m", "30m": "30m", "30min": "30m",
        }
        v2 = mapping.get(v, v)
        if v2 not in {"1m", "5m", "15m", "30m"}:
            raise ValueError(f"Invalid timeframe '{v}'. Expected 1m/5m/15m/30m.")
        return v2


class CandlesIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    candles: list[CandleIn] = Field(..., validation_alias=AliasChoices("candles", "Candles", "data"))


class PollResponse(BaseModel):
    mode: Mode
    signal: Signal
    stop_price: float
    reason: str
    meta: Optional[dict] = None


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    machineId: str = Field(..., validation_alias=AliasChoices("machineId", "machine_id", "machine", "machineID"))
    ts_utc: Optional[str] = Field(None, validation_alias=AliasChoices("ts_utc", "ts", "timestamp", "time"))


class FillIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    machineId: str = Field(..., validation_alias=AliasChoices("machineId", "machine_id", "machine", "machineID"))
    symbol: str = Field(..., validation_alias=AliasChoices("symbol", "Symbol"))
    side: Literal["BUY", "SELL"] = Field(..., validation_alias=AliasChoices("side", "Side"))
    qty: float = Field(..., validation_alias=AliasChoices("qty", "quantity", "Qty", "Quantity"))
    price: float = Field(..., validation_alias=AliasChoices("price", "Price"))
    ts_utc: Optional[str] = Field(None, validation_alias=AliasChoices("ts_utc", "ts", "timestamp", "time"))
    notes: str = Field("", validation_alias=AliasChoices("notes", "Notes"))
