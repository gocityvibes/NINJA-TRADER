from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from threading import Lock
from typing import Dict, Optional


@dataclass
class PositionState:
    """Render-side runtime position state.

    IMPORTANT: This is "best effort" state derived from Ninja fill notifications.
    If Render restarts, state resets to FLAT unless Ninja re-sends fills.
    """

    side: Optional[str] = None           # "long" or "short"
    entry_price: float = 0.0
    stop_price: float = 0.0
    qty: float = 0.0
    open: bool = False
    entry_time_utc: Optional[datetime] = None
    initial_stop: float = 0.0

    # Runtime controls
    last_stop_update_utc: Optional[datetime] = None
    last_sl_time_utc: Optional[datetime] = None

    # For dedup (optional)
    last_processed_ts_by_tf: Dict[str, str] = field(default_factory=dict)

@dataclass
class BotState:
    mode: str = "PAPER"
    kill_switch: bool = False
    last_signal: str = "FLAT"
    last_reason: str = ""
    last_stop_price: float = 0.0

    # Runtime position state: per machineId+symbol
    positions: Dict[str, PositionState] = field(default_factory=dict)

    # Daily realized P&L per machineId (USD). Requires fills to be posted.
    daily_realized_pnl_by_machine: Dict[str, float] = field(default_factory=dict)
    current_day_utc: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    kill_switch_triggered_by_machine: Dict[str, bool] = field(default_factory=dict)
    
    # Consecutive losses tracking (for 3-loss kill switch)
    consecutive_losses_by_machine: Dict[str, int] = field(default_factory=dict)

    # last heartbeat per machine
    last_heartbeat_utc_by_machine: Dict[str, datetime] = field(default_factory=dict)

class StateStore:
    def __init__(self):
        self._lock = Lock()
        self._state = BotState()

    def get(self) -> BotState:
        with self._lock:
            return self._state

    def set_mode(self, mode: str):
        with self._lock:
            self._state.mode = mode

    def set_kill(self, enabled: bool):
        with self._lock:
            self._state.kill_switch = enabled

    def set_decision(self, signal: str, stop_price: float, reason: str):
        with self._lock:
            self._state.last_signal = signal
            self._state.last_stop_price = stop_price
            self._state.last_reason = reason

    def _day_rollover_if_needed(self):
        today = datetime.now(timezone.utc).date()
        if today != self._state.current_day_utc:
            self._state.current_day_utc = today
            self._state.daily_realized_pnl_by_machine = {}
            self._state.kill_switch_triggered_by_machine = {}
            self._state.consecutive_losses_by_machine = {}

    def get_position(self, machine_id: str, symbol: str) -> PositionState:
        key = f"{machine_id}:{symbol}"
        with self._lock:
            self._day_rollover_if_needed()
            if key not in self._state.positions:
                self._state.positions[key] = PositionState()
            return self._state.positions[key]

    def set_position(self, machine_id: str, symbol: str, pos: PositionState):
        key = f"{machine_id}:{symbol}"
        with self._lock:
            self._day_rollover_if_needed()
            self._state.positions[key] = pos

    def add_realized_pnl(self, machine_id: str, pnl_usd: float):
        with self._lock:
            self._day_rollover_if_needed()
            self._state.daily_realized_pnl_by_machine[machine_id] = float(
                self._state.daily_realized_pnl_by_machine.get(machine_id, 0.0) + pnl_usd
            )

    def get_realized_pnl(self, machine_id: str) -> float:
        with self._lock:
            self._day_rollover_if_needed()
            return float(self._state.daily_realized_pnl_by_machine.get(machine_id, 0.0))

    def set_kill_triggered(self, machine_id: str, enabled: bool):
        with self._lock:
            self._day_rollover_if_needed()
            self._state.kill_switch_triggered_by_machine[machine_id] = bool(enabled)

    def is_kill_triggered(self, machine_id: str) -> bool:
        with self._lock:
            self._day_rollover_if_needed()
            return bool(self._state.kill_switch_triggered_by_machine.get(machine_id, False))

    def increment_consecutive_losses(self, machine_id: str):
        with self._lock:
            self._day_rollover_if_needed()
            self._state.consecutive_losses_by_machine[machine_id] = \
                self._state.consecutive_losses_by_machine.get(machine_id, 0) + 1

    def reset_consecutive_losses(self, machine_id: str):
        with self._lock:
            self._day_rollover_if_needed()
            self._state.consecutive_losses_by_machine[machine_id] = 0

    def get_consecutive_losses(self, machine_id: str) -> int:
        with self._lock:
            self._day_rollover_if_needed()
            return self._state.consecutive_losses_by_machine.get(machine_id, 0)

    def heartbeat(self, machine_id: str):
        with self._lock:
            self._state.last_heartbeat_utc_by_machine[machine_id] = datetime.now(timezone.utc)

STORE = StateStore()
