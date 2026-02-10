from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

@dataclass
class PositionState:
    """Tracks one position per (machine_id, symbol)."""
    open: bool = False
    side: str = ""  # "LONG" | "SHORT"
    qty: int = 0
    entry_price: float = 0.0
    stop_price: float = 0.0
    entry_time_utc: Optional[datetime] = None
    last_sl_time_utc: Optional[datetime] = None


class StateStore:
    """In-memory state for all machines/symbols."""
    
    def __init__(self):
        self._positions: Dict[str, PositionState] = {}
        self.kill_switch: bool = False
        self.daily_realized_pnl_usd: float = 0.0
    
    def _key(self, machine_id: str, symbol: str) -> str:
        return f"{machine_id}:{symbol}"
    
    def get_position(self, machine_id: str, symbol: str) -> PositionState:
        k = self._key(machine_id, symbol)
        if k not in self._positions:
            self._positions[k] = PositionState()
        return self._positions[k]
    
    def set_position(self, machine_id: str, symbol: str, pos: PositionState):
        self._positions[self._key(machine_id, symbol)] = pos
    
    def clear_position(self, machine_id: str, symbol: str):
        k = self._key(machine_id, symbol)
        if k in self._positions:
            self._positions[k] = PositionState()


STORE = StateStore()
