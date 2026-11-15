"""
Headless service API for KLineScan.

This is the non-Qt entry point used by scripts, tests, the conformance runner
and future integrations.  It owns exactly one transport/protocol pair and
returns structured errors from ``klinescan.core.errors``.
"""

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Type

from klinescan.core.errors import KLineScanError, ConfigurationError, classify_exception
from klinescan.core.models import (
    ActuatorDefinition,
    DiagnosticTroubleCode,
    EcuIdItem,
    EcuTarget,
    FreezeFrame,
    LivePid,
    ProtocolType,
    ReadinessMonitor,
)
from klinescan.core.registry import discover_plugins, validate_transport_for_target
from klinescan.core.safety import require_write_allowed
from klinescan.core.session_meta import build_session_metadata
from klinescan.core.trace import TraceRecorder
from klinescan.protocols import PROTOCOL_CLASSES
from klinescan.protocols.base import BaseProtocol
from klinescan.transport.base import BaseTransport
from klinescan.transport.doip import DoIPTransport
from klinescan.transport.elm327 import Elm327Transport
from klinescan.transport.fake_ecu import FakeEcuTransport
from klinescan.transport.ftdi_direct import FtdiDirectTransport
from klinescan.transport.j2534 import J2534Transport
from klinescan.transport.kcan import KCanTransport
from klinescan.transport.kl_serial import KLSerialTransport
from klinescan.transport.socketcan import SocketCanTransport

TRANSPORTS = {
    "fake": FakeEcuTransport,
    "mock": FakeEcuTransport,
    "kl_serial": KLSerialTransport,
    "kline": KLSerialTransport,
    "ftdi": FtdiDirectTransport,
    "ftdi_direct": FtdiDirectTransport,
    "elm327": Elm327Transport,
    "kcan": KCanTransport,
    "j2534": J2534Transport,
    "socketcan": SocketCanTransport,
    "doip": DoIPTransport,
}

PROTOCOLS = PROTOCOL_CLASSES


@dataclass
class ConnectionInfo:
    transport: str
    port: str
    baudrate: int
    target: EcuTarget
    protocol: str


log = logging.getLogger(__name__)


class KLineScanService:
    def __init__(self):
        discover_plugins()
        self._transport: Optional[BaseTransport] = None
        self._protocol: Optional[BaseProtocol] = None
        self._info: Optional[ConnectionInfo] = None
        self._trace_recorder: Optional[TraceRecorder] = None
        self._trace_previous_cb = None

    @property
    def connected(self) -> bool:
        return self._protocol is not None

    @property
    def info(self) -> Optional[ConnectionInfo]:
        return self._info

    def connect(
        self,
        transport: str,
        port: str,
        target: EcuTarget,
        baudrate: Optional[int] = None,
        led_callback=None,
        frame_cb=None,
        trace_path: Optional[str] = None,
        trace_extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        self.disconnect()
        key = transport.strip().lower()
        transport_cls: Optional[Type[BaseTransport]] = TRANSPORTS.get(key)
        if transport_cls is None:
            raise ConfigurationError(f"Unknown transport '{transport}'")

        ok, reason = validate_transport_for_target(key, target)
        if not ok:
            raise ConfigurationError(reason)

        protocol_cls = PROTOCOLS.get(target.protocol)
        if protocol_cls is None:
            raise ConfigurationError(
                f"No protocol implementation for {target.protocol}"
            )

        effective_baud = baudrate if baudrate is not None else target.baudrate
        device = transport_cls(led_callback)
        recorder: Optional[TraceRecorder] = None
        active_frame_cb = frame_cb
        try:
            device.open(port, effective_baud)
            if trace_path:
                recorder = TraceRecorder()
                recorder.start(
                    trace_path,
                    build_session_metadata(
                        transport=key,
                        protocol=target.protocol.value,
                        port=port,
                        baudrate=effective_baud,
                        target=target,
                        transport_info=device.info(),
                        extra=trace_extra,
                    ),
                )
                previous_cb = active_frame_cb

                def _capture(frame, _rec=recorder, _prev=previous_cb):
                    _rec.record_frame(frame)
                    if _prev:
                        _prev(frame)

                active_frame_cb = _capture

            protocol = protocol_cls(device, target, active_frame_cb)
            if not protocol.connect():
                raise KLineScanError(f"ECU handshake failed for {target.name}")
        except Exception:
            if recorder is not None:
                recorder.stop()
            try:
                device.close()
            except Exception:
                pass
            raise

        self._transport = device
        self._trace_recorder = recorder
        self._trace_previous_cb = frame_cb
        self._protocol = protocol
        self._info = ConnectionInfo(
            transport=key,
            port=port,
            baudrate=effective_baud,
            target=target,
            protocol=target.protocol.value,
        )
        return True

    def disconnect(self) -> None:
        self.stop_trace()
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                log.warning("closing %s failed", self._transport.name, exc_info=True)
        self._transport = None
        self._protocol = None
        self._info = None

    def __enter__(self) -> "KLineScanService":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disconnect()

    def _require(self) -> BaseProtocol:
        if self._protocol is None:
            raise ConfigurationError("No ECU connection; call connect() first")
        return self._protocol

    def session_metadata(
        self, extra: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Return reproducible metadata for a capture or bug report."""
        self._require()
        assert self._info is not None and self._transport is not None
        return build_session_metadata(
            transport=self._info.transport,
            protocol=self._info.protocol,
            port=self._info.port,
            baudrate=self._info.baudrate,
            target=self._info.target,
            transport_info=self._transport.info(),
            extra=extra,
        )

    @property
    def trace_path(self) -> Optional[str]:
        return getattr(self._trace_recorder, "_path", None) if self._trace_recorder else None

    def start_trace(
        self, path: str, extra: Optional[Dict[str, Any]] = None
    ) -> None:
        """Start recording protocol frames with reproducible metadata."""
        self._require()
        if self._trace_recorder is not None and self._trace_recorder.is_recording:
            raise ConfigurationError("A trace is already being recorded")
        assert self._protocol is not None
        recorder = TraceRecorder()
        recorder.start(path, self.session_metadata(extra))
        previous_cb = self._protocol._frame_cb
        self._trace_previous_cb = previous_cb

        def _capture(frame, _rec=recorder, _prev=previous_cb):
            _rec.record_frame(frame)
            if _prev:
                _prev(frame)

        self._protocol._frame_cb = _capture
        self._trace_recorder = recorder

    def stop_trace(self) -> Optional[str]:
        if self._protocol is not None and self._trace_previous_cb is not None:
            self._protocol._frame_cb = self._trace_previous_cb
        self._trace_previous_cb = None
        recorder = self._trace_recorder
        self._trace_recorder = None
        if recorder is not None:
            return recorder.stop()
        return None

    def read_dtcs(self, status_filter: str = "all") -> List[DiagnosticTroubleCode]:
        return self._require().read_dtcs(status_filter)

    def clear_dtcs(self) -> bool:
        protocol = self._require()
        require_write_allowed(self._info.target if self._info else None, "clear_dtcs")
        return protocol.clear_dtcs()

    def read_live_pids(self) -> List[LivePid]:
        return self._require().read_live_pids()

    def read_ecu_id(self) -> List[EcuIdItem]:
        return self._require().read_ecu_id()

    def read_freeze_frame(self) -> Optional[FreezeFrame]:
        return self._require().read_freeze_frame()

    def read_readiness(self) -> List[ReadinessMonitor]:
        return self._require().read_readiness()

    def run_actuator(self, actuator: ActuatorDefinition, start: bool = True) -> bool:
        protocol = self._require()
        if start:  # stopping an actuator is always allowed
            require_write_allowed(self._info.target if self._info else None, "actuator",
                                  definition=actuator)
        return protocol.run_actuator_test(actuator, start)

    def run_special_function(self, func) -> bool:
        protocol = self._require()
        require_write_allowed(self._info.target if self._info else None, "special_function",
                              definition=func)
        return protocol.run_special_function(func)

    def send_raw(self, raw: bytes) -> bytes:
        return self._require().send_raw_hex(raw)

    def keep_alive(self) -> None:
        self._require().keep_alive()


@contextmanager
def connected(
    transport: str, port: str, target: EcuTarget, baudrate: Optional[int] = None
) -> Iterator[KLineScanService]:
    service = KLineScanService()
    try:
        service.connect(transport, port, target, baudrate)
        yield service
    finally:
        service.disconnect()
