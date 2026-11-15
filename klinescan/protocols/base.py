"""Diagnostic Protocol Base Class."""

from abc import ABC, abstractmethod
import time
from typing import Callable, Dict, List, Optional
from klinescan.core.models import (
    ActuatorDefinition,
    BusFrame,
    DiagnosticTroubleCode,
    EcuIdItem,
    EcuTarget,
    FreezeFrame,
    LivePid,
    ReadinessMonitor,
    SpecialFunctionDefinition,
)
from klinescan.transport.base import BaseTransport


class BaseProtocol(ABC):
    def __init__(
        self,
        transport: BaseTransport,
        target: EcuTarget,
        frame_cb: Optional[Callable[[BusFrame], None]] = None,
    ):
        self.transport = transport
        self.target = target
        self._frame_cb = frame_cb
        self._last_frame_time = time.time()

    def log(self, direction: str, data: bytes, note: str = "") -> None:
        now = time.time()
        delta_ms = (now - self._last_frame_time) * 1000.0 if self._last_frame_time else 0.0
        self._last_frame_time = now
        if self._frame_cb:
            self._frame_cb(
                BusFrame(
                    direction=direction,
                    data=data,
                    note=note,
                    timestamp=now,
                    delta_ms=delta_ms,
                )
            )

    @abstractmethod
    def connect(self) -> bool:
        pass

    @abstractmethod
    def keep_alive(self) -> None:
        pass

    @abstractmethod
    def read_dtcs(self, status_filter: str = "all") -> List[DiagnosticTroubleCode]:
        pass

    @abstractmethod
    def clear_dtcs(self) -> bool:
        pass

    @abstractmethod
    def read_live_pids(self) -> List[LivePid]:
        pass

    # -- standard live data (SAE J1979 Service 01 / J1979-2 DIDs F4xx) -------
    def query_pid(self, pid: int) -> Optional[bytes]:
        """Return the data bytes for Service 01 ``pid`` (after ``41 pid``), or None."""
        return None

    def discover_supported_pids(self, force: bool = False) -> List[int]:
        """Read the supported-PID bitmaps once per session and cache the result."""
        from klinescan.core.obd_pids import discover_supported, pollable

        if force or getattr(self, "_supported_pids", None) is None:
            def safe_query(pid: int):
                try:
                    return self.query_pid(pid)
                except Exception:
                    return None
            self._supported_pids = pollable(discover_supported(safe_query))
            self.log("INFO", bytes(self._supported_pids),
                     f"{len(self._supported_pids)} live PIDs supported by this ECU")
        return list(self._supported_pids)

    # PIDs requested per poll tick; 0 = all.  Slow K-line links rotate through the
    # supported list so the UI keeps updating instead of blocking for a full sweep.
    poll_batch: int = 0

    def poll_standard_pids(self) -> List[LivePid]:
        """Poll supported, decodable PIDs (one request per PID), round-robin by ``poll_batch``."""
        from klinescan.core.obd_pids import PIDS, format_value

        supported = self.discover_supported_pids()
        if self.poll_batch and len(supported) > self.poll_batch:
            start = getattr(self, "_poll_cursor", 0) % len(supported)
            batch = (supported[start:] + supported[:start])[: self.poll_batch]
            self._poll_cursor = start + self.poll_batch
        else:
            batch = supported
        results: List[LivePid] = []
        for pid in batch:
            definition = PIDS[pid]
            try:
                data = self.query_pid(pid)
            except Exception:
                continue
            if not data or len(data) < definition.length:
                continue
            try:
                value = float(definition.decode(data[:definition.length]))
            except (IndexError, ZeroDivisionError, ValueError):
                continue
            results.append(LivePid(
                pid=pid, name=definition.name, value=format_value(definition, value),
                unit=definition.unit, raw_hex=data[:definition.length].hex(" ").upper(),
                numeric_value=value, category=definition.category))
        return results

    def read_freeze_frame(self) -> Optional[FreezeFrame]:
        return None

    def read_ecu_id(self) -> List[EcuIdItem]:
        return []

    def read_readiness(self) -> List[ReadinessMonitor]:
        return []

    # -- security access (0x27) ------------------------------------------------
    def security_exchange(self, payload: bytes) -> bytes:
        """Send one SecurityAccess request and return the response payload."""
        from klinescan.core.errors import UnsupportedOperationError

        raise UnsupportedOperationError(f"{type(self).__name__} has no SecurityAccess service")

    def unlock(self, level: int = 0) -> bool:
        """Run seed/key with the target's registered algorithm.  ``level`` 0 = target default."""
        from klinescan.core.security import get_algorithm, security_access

        level = level or getattr(self.target, "security_level", 0)
        if not level:
            return True  # nothing to unlock
        algorithm = get_algorithm(getattr(self.target, "security_algorithm", "") or "")
        ok = security_access(self.security_exchange, level, algorithm)
        self.log("INFO", bytes([level]), f"Security level 0x{level:02X} unlocked")
        return ok

    def run_actuator_test(self, actuator: ActuatorDefinition, start: bool = True) -> bool:
        return False

    def run_special_function(self, func: SpecialFunctionDefinition) -> bool:
        return False

    @abstractmethod
    def send_raw_hex(self, raw: bytes) -> bytes:
        pass
