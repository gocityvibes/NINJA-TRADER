from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
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

        # Global controls / status
        self.kill_switch: bool = False
        self.daily_realized_pnl_usd: float = 0.0
        self.mode: str = "PAPER"

        # PATCH: last decision snapshot (used by routes.py)
        self.last_signal: str = "FLAT"          # "LONG" | "SHORT" | "FLAT"
        self.last_stop_price: float = 0.0
        self.last_reason: str = ""
        self.last_decision_time_utc: Optional[datetime] = None

    def _key(self, machine_id: str, symbol: str) -> str:
        return f"{machine_id}:{symbol}"

    # ---------- position handling ----------

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

    # ---------- global state ----------

    def set_mode(self, mode: str):
        """Set bot mode (PAPER | LIVE)."""
        self.mode = mode.upper()

    def set_kill(self, enabled: bool):
        """Set kill switch."""
        self.kill_switch = bool(enabled)

    # ---------- PATCH: decision handling expected by routes.py ----------

    def set_decision(self, signal: str, stop_price: float, reason: str = ""):
        """
        PATCH: routes.py calls STORE.set_decision(signal, stop_price, reason)

        Stores the most recent decision so /poll can return it and/or other
        parts of the app can reference it.
        """
        self.last_signal = (signal or "FLAT").upper()
        try:
            self.last_stop_price = float(stop_price or 0.0)
        except Exception:
            self.last_stop_price = 0.0
        self.last_reason = reason or ""
        self.last_decision_time_utc = datetime.utcnow()

    # ---------- compatibility layer ----------

    def get(self):
        """
        routes.py expects STORE.get() to return an object with attributes
        like st.kill_switch (NOT a dict).
        """
        return self

    def snapshot(self, machine_id: Optional[str] = None, symbol: Optional[str] = None) -> dict:
        """Optional: JSON-safe snapshot."""
        out = {
            "kill_switch": self.kill_switch,
            "daily_realized_pnl_usd": self.daily_realized_pnl_usd,
            "mode": self.mode,
            "last_signal": self.last_signal,
            "last_stop_price": self.last_stop_price,
            "last_reason": self.last_reason,
            "last_decision_time_utc": self.last_decision_time_utc.isoformat() if self.last_decision_time_utc else None,
        }
        if machine_id and symbol:
            p = self.get_position(machine_id, symbol)
            out["position"] = {
                "open": p.open,
                "side": p.side,
                "qty": p.qty,
                "entry_price": p.entry_price,
                "stop_price": p.stop_price,
                "entry_time_utc": p.entry_time_utc.isoformat() if p.entry_time_utc else None,
                "last_sl_time_utc": p.last_sl_time_utc.isoformat() if p.last_sl_time_utc else None,
            }
        return out


STORE = StateStore()
