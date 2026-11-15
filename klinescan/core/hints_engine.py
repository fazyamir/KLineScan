"""Contextual hints engine: short, actionable messages for common link states."""

import time
from typing import Callable, List, Optional


class HintEvent:
    def __init__(self, text: str, level: str = "info", action_tip: str = "", timeout_s: float = 6.0):
        self.text = text
        self.level = level  # "info", "warning", "error", "success"
        self.action_tip = action_tip
        self.timestamp = time.time()
        self.timeout_s = timeout_s


class HintsEngine:
    def __init__(self, callback: Optional[Callable[[HintEvent], None]] = None):
        self._cb = callback
        self._consecutive_timeouts = 0
        self._consecutive_naks = 0
        self._last_baud = 0
        self._l_line_active = False

    def set_callback(self, cb: Callable[[HintEvent], None]) -> None:
        self._cb = cb

    def _emit(self, text: str, level: str = "info", action_tip: str = "", timeout_s: float = 6.0) -> None:
        if self._cb:
            self._cb(HintEvent(text=text, level=level, action_tip=action_tip, timeout_s=timeout_s))

    def on_adapter_missing(self) -> None:
        self._emit(
            text="No diagnostic interface detected.",
            level="error",
            action_tip="Check USB cable & drivers, or select 'Fake ECU (Offline)' mode.",
            timeout_s=8.0,
        )

    def on_connect_attempt(self, target_name: str, protocol: str, baud: int, pin: int) -> None:
        self._consecutive_timeouts = 0
        self._consecutive_naks = 0
        self._emit(
            text=f"Initiating handshake with {target_name} on Pin {pin} ({baud} baud)...",
            level="info",
            action_tip="Ensure vehicle ignition is ON (Engine running or Key-On-Engine-Off).",
            timeout_s=4.0,
        )

    def on_connect_success(self, target_name: str) -> None:
        self._consecutive_timeouts = 0
        self._consecutive_naks = 0
        self._emit(
            text=f"Connected to {target_name}. Diagnostic session active.",
            level="success",
            action_tip="TesterPresent (0x3E) keep-alive is maintaining ECU session.",
            timeout_s=5.0,
        )

    def on_timeout(self) -> None:
        self._consecutive_timeouts += 1
        if self._consecutive_timeouts == 1:
            self._emit(
                text="ECU response timeout.",
                level="warning",
                action_tip="Check connection pins or retry initial fast-init / slow-init.",
                timeout_s=5.0,
            )
        elif self._consecutive_timeouts >= 3:
            self._emit(
                text="Communications breakdown — multiple timeouts.",
                level="error",
                action_tip="Cycle vehicle ignition OFF for 10 seconds, then turn back ON.",
                timeout_s=8.0,
            )

    def on_nak_received(self, reason: str = "") -> None:
        self._consecutive_naks += 1
        detail = f" ({reason})" if reason else ""
        if self._consecutive_naks >= 2:
            self._emit(
                text=f"ECU returned Negative Response (NAK){detail}.",
                level="warning",
                action_tip="Function may not be supported by this specific ECU variant or security access required.",
                timeout_s=6.0,
            )

    def on_baud_changed(self, old_baud: int, new_baud: int) -> None:
        self._emit(
            text=f"Baud rate changed from {old_baud} to {new_baud} bps.",
            level="info",
            action_tip="If ECU fails to sync, cycle ignition to reset vehicle transceiver.",
            timeout_s=5.0,
        )

    def on_l_line_toggle(self, active: bool) -> None:
        self._l_line_active = active
        if active:
            self._emit(
                text="L-Line (RTS / Pin 15 / Pin 1) asserted HIGH.",
                level="warning",
                action_tip="For Mitsubishi vehicles, ensure Pin 1 (SCS / Diag) is linked to Ground if reading ABS/SRS.",
                timeout_s=7.0,
            )
        else:
            self._emit(
                text="L-Line released to idle state.",
                level="info",
                timeout_s=3.0,
            )

    def on_bus_break_sent(self) -> None:
        self._emit(
            text="Manual K-Line Break pulse generated.",
            level="info",
            action_tip="Forced bus idle reset.",
            timeout_s=3.0,
        )
