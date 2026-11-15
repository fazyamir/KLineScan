"""
Scanner Worker: Lives in a dedicated QThread; owns transport, protocol,
hints engine, polling timers, and session recording/replaying.
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from klinescan.core.hints_engine import HintEvent, HintsEngine
from klinescan.core.models import (
    ActuatorDefinition,
    BaudMode,
    BusFrame,
    DiagnosticTroubleCode,
    EcuIdItem,
    EcuTarget,
    FreezeFrame,
    LivePid,
    ProtocolType,
    ReadinessMonitor,
    SpecialFunctionDefinition,
    TransportType,
)
from klinescan.core.errors import KLineScanError, OperationCancelled, classify_exception
from klinescan.core.safety import require_write_allowed
from klinescan.core.session_recorder import SessionPlayer, SessionRecorder
from klinescan.protocols import make_protocol
from klinescan.protocols.base import BaseProtocol
from klinescan.transport.base import BaseTransport, TransportError
from klinescan.transport.doip import DoIPTransport
from klinescan.transport.elm327 import Elm327Transport
from klinescan.transport.fake_ecu import FakeEcuTransport
from klinescan.transport.ftdi_direct import FtdiDirectTransport
from klinescan.transport.j2534 import J2534Transport
from klinescan.transport.kcan import KCanTransport
from klinescan.transport.kl_serial import KLSerialTransport
from klinescan.transport.socketcan import SocketCanTransport


log = logging.getLogger(__name__)

# Pins a plain K-line cable reaches without rewiring (K on 7; CAN on 6/14 for CAN adapters).
_KLINE_TRANSPORTS = {TransportType.KL_SERIAL, TransportType.FTDI_DIRECT}


def describe_error(exc: BaseException) -> str:
    """User-facing text that keeps the error class (timeout, bus fault, NRC...)."""
    err = exc if isinstance(exc, KLineScanError) else classify_exception(exc)
    label = err.error_class.value.replace("_", " ")
    return f"[{label}] {err}"


class ScannerWorker(QObject):
    connected_changed = pyqtSignal(bool, str)
    frame_logged = pyqtSignal(object)              # BusFrame
    dtc_results = pyqtSignal(list)                 # List[DiagnosticTroubleCode]
    freeze_frame_result = pyqtSignal(object)       # Optional[FreezeFrame]
    ecu_id_result = pyqtSignal(list)               # List[EcuIdItem]
    readiness_result = pyqtSignal(list)            # List[ReadinessMonitor]
    pid_results = pyqtSignal(list)                 # List[LivePid]
    actuator_result = pyqtSignal(str, bool)        # name, success
    special_func_result = pyqtSignal(str, bool)    # name, success
    led_changed = pyqtSignal(str, bool)            # led_name, state
    baud_changed = pyqtSignal(int, int)            # requested, actual
    hint_emitted = pyqtSignal(object)              # HintEvent
    playback_progress = pyqtSignal(float, float, float)  # pct, current_s, total_s
    playback_state_changed = pyqtSignal(bool, bool)      # is_playing, is_paused
    identify_result = pyqtSignal(object)           # IdentifyResult
    error_occurred = pyqtSignal(str)
    status_message = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._transport: Optional[BaseTransport] = None
        self._protocol: Optional[BaseProtocol] = None
        self._poll_timer: Optional[QTimer] = None
        self._keepalive_timer: Optional[QTimer] = None
        self._polling_active = False

        self._hints = HintsEngine(self._on_hint)
        self._recorder = SessionRecorder()
        self._player: Optional[SessionPlayer] = None

        self._target: Optional[EcuTarget] = None
        self._current_transport_type: Optional[TransportType] = None
        self._current_port: str = ""
        self._baud_mode: BaudMode = BaudMode.AUTOMATIC
        self._custom_baud: int = 10400
        self._incremental_rates = [10400, 9600, 4800, 15625]
        self._last_traffic = 0.0
        self._base_poll_ms = 250

    # -- cancellation (safe to call from the GUI thread) -------------------------
    def cancel_current(self) -> None:
        """Abort the blocking I/O the worker is doing right now.

        Called directly (not queued) from the GUI thread: it only sets a flag that
        transport read loops check, so the worker returns within ~100 ms.
        """
        transport = self._transport
        if transport is not None:
            transport.cancel()

    @pyqtSlot()
    def shutdown(self) -> None:
        """Stop timers, end the diagnostic session politely and release the port."""
        self.stop_session_recording()
        self.disconnect_ecu()

    def _begin_io(self) -> None:
        if self._transport is not None:
            self._transport.reset_cancel()

    def _traffic(self) -> None:
        self._last_traffic = time.monotonic()

    def _write_guard(self, operation: str, definition: object = None) -> bool:
        try:
            require_write_allowed(self._target, operation, replaying=self._player is not None,
                                  definition=definition)
            return True
        except KLineScanError as exc:
            self.error_occurred.emit(describe_error(exc))
            return False

    @pyqtSlot()
    def initialize(self) -> None:
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(250)
        self._poll_timer.timeout.connect(self._on_poll_tick)

        self._keepalive_timer = QTimer(self)
        self._keepalive_timer.setInterval(2000)
        self._keepalive_timer.timeout.connect(self._on_keepalive_tick)

    def _on_hint(self, hint: HintEvent) -> None:
        self.hint_emitted.emit(hint)

    def _make_transport(self, t_type: TransportType) -> BaseTransport:
        cb = lambda name, state: self.led_changed.emit(name, state)
        if t_type == TransportType.FAKE:
            return FakeEcuTransport(cb)
        if t_type == TransportType.KL_SERIAL:
            return KLSerialTransport(cb)
        if t_type == TransportType.FTDI_DIRECT:
            return FtdiDirectTransport(cb)
        if t_type == TransportType.ELM327:
            return Elm327Transport(cb)
        if t_type == TransportType.KCAN:
            return KCanTransport(cb)
        if t_type == TransportType.J2534:
            return J2534Transport(cb)
        if t_type == TransportType.SOCKETCAN:
            return SocketCanTransport(cb)
        if t_type == TransportType.DOIP:
            return DoIPTransport(cb)
        raise TransportError(f"Unsupported transport: {t_type}")

    @pyqtSlot(object, str, object, object, int)
    def connect_ecu(
        self,
        t_type: TransportType,
        port: str,
        target: EcuTarget,
        baud_mode: BaudMode = BaudMode.AUTOMATIC,
        custom_baud: int = 10400,
    ) -> None:
        self.disconnect_ecu()
        self._target = target
        self._current_transport_type = t_type
        self._current_port = port
        self._baud_mode = baud_mode
        self._custom_baud = custom_baud

        # Determine effective baud rate
        baud = target.baudrate
        if baud_mode == BaudMode.FIXED:
            baud = custom_baud

        rates_to_try = [baud]
        if baud_mode == BaudMode.INCREMENTAL and target.baudrate < 250000:
            rates_to_try = [baud] + [r for r in self._incremental_rates if r != baud]

        success = False
        last_error: Optional[BaseException] = None
        self._pin_hint(t_type, target)

        for current_baud in rates_to_try:
            try:
                self.baud_changed.emit(current_baud, current_baud)
                self._hints.on_connect_attempt(target.name, target.protocol.value, current_baud, target.pin)
                self._transport = self._make_transport(t_type)
                self._transport.pin = target.pin
                if target.echo_mode and hasattr(self._transport, "echo"):
                    self._transport.echo.set_mode(target.echo_mode)
                self._transport.open(port, current_baud)
                self.baud_changed.emit(self._transport.requested_baud, self._transport.actual_baud)

                self._protocol = self._make_protocol(self._transport, target)

                self.status_message.emit(f"Handshaking {target.name} ({current_baud} baud)...")
                ok = self._protocol.connect()
                if ok:
                    self._hints.on_connect_success(target.name)
                    self.led_changed.emit("connected", True)
                    self.connected_changed.emit(True, f"Connected: {target.name}")
                    self._traffic()
                    if self._keepalive_timer:
                        interval = getattr(self._protocol, "keep_alive_interval_ms", 0) or target.keep_alive_ms or 2000
                        self._keepalive_timer.setInterval(min(interval, target.keep_alive_ms or interval))
                        self._keepalive_timer.start()
                    success = True
                    break
                else:
                    self._hints.on_timeout()
                    self._transport.close()
                    self._transport = None
                    self._protocol = None
            except Exception as exc:
                last_error = exc
                log.warning("connect %s at %d baud failed", target.name, current_baud, exc_info=True)
                if self._transport:
                    try:
                        self._transport.close()
                    except Exception:
                        log.debug("close after failed connect", exc_info=True)
                self._transport = None
                self._protocol = None
                if isinstance(exc, OperationCancelled):
                    break

        if not success:
            self.disconnect_ecu()
            if last_error is not None:
                err_msg = describe_error(last_error)
            else:
                err_msg = (f"[timeout] No valid handshake from {target.name} "
                           f"({target.protocol.value}). Check ignition, pin {target.pin} and the address.")
            self.error_occurred.emit(err_msg)

    def _pin_hint(self, t_type: TransportType, target: EcuTarget) -> None:
        """Plain K-line cables only reach pin 7: say so when a module sits elsewhere."""
        if t_type in _KLINE_TRANSPORTS and target.pin not in (7, 0) and target.protocol not in (
                ProtocolType.CAN_OBD, ProtocolType.UDS_CAN):
            self.status_message.emit(
                f"{target.name} uses pin {target.pin}. A standard KKL cable only drives pin 7: "
                f"move the cable's K-line to pin {target.pin} (switch, jumper or adapter lead).")

    def _make_protocol(self, transport: BaseTransport, target: EcuTarget) -> BaseProtocol:
        return make_protocol(transport, target, self._on_frame_received)

    @pyqtSlot(object, str)
    def identify_vehicle(self, t_type: TransportType, port: str) -> None:
        """Generic OBD connect + VIN / ECU identification."""
        from klinescan.core.identify import IdentifyResult, identify

        self.disconnect_ecu()
        kinds = {TransportType.KL_SERIAL: ("kline",), TransportType.FTDI_DIRECT: ("kline",),
                 TransportType.SOCKETCAN: ("can",), TransportType.KCAN: ("can",),
                 TransportType.J2534: ("can",), TransportType.DOIP: ("can",)}.get(t_type, ("can", "kline"))
        self.status_message.emit("Identifying vehicle: searching for an OBD-II response...")
        try:
            transport = self._make_transport(t_type)
            transport.open(port, 38400 if t_type == TransportType.ELM327 else 10400)
        except Exception as exc:
            self.identify_result.emit(IdentifyResult(log=[f"adapter open failed: {exc}"]))
            return
        result = identify(transport, self._make_protocol, None, kinds)
        if result.connected and result.target is not None:
            # keep the generic OBD session so the user can scan immediately
            self._transport = transport
            self._target = result.target
            self._protocol = self._make_protocol(transport, result.target)
            self._protocol._supported_pids = result.supported_pids
            self._current_transport_type, self._current_port = t_type, port
            self.led_changed.emit("connected", True)
            self.connected_changed.emit(True, f"Connected: {result.protocol}")
        else:
            try:
                transport.close()
            except Exception:
                pass
        self.identify_result.emit(result)

    def _on_frame_received(self, frame: BusFrame) -> None:
        if frame.direction in ("TX", "RX"):
            self._traffic()
        self.frame_logged.emit(frame)
        if self._recorder.is_recording:
            self._recorder.record_frame(frame)

    @pyqtSlot()
    def disconnect_ecu(self) -> None:
        self._polling_active = False
        if self._poll_timer:
            self._poll_timer.stop()
        if self._keepalive_timer:
            self._keepalive_timer.stop()
        if self._protocol is not None and hasattr(self._protocol, "disconnect"):
            try:
                if self._transport is not None:
                    self._transport.reset_cancel()
                self._protocol.disconnect()
            except Exception:
                log.debug("protocol disconnect failed", exc_info=True)
        if self._transport:
            try:
                self._transport.close()
            except Exception:
                log.warning("closing transport failed", exc_info=True)
        self._transport = None
        self._protocol = None
        self.led_changed.emit("connected", False)
        self.connected_changed.emit(False, "Disconnected")

    @pyqtSlot(str)
    def read_dtcs(self, status_filter: str = "all") -> None:
        if not self._protocol:
            return
        self._begin_io()
        try:
            dtcs = self._protocol.read_dtcs(status_filter)
            self.dtc_results.emit(dtcs)
            if self._recorder.is_recording:
                self._recorder.record_dtcs(dtcs)
        except Exception as exc:
            self._hints.on_timeout()
            self.error_occurred.emit(f"Read DTC error: {describe_error(exc)}")

    @pyqtSlot()
    def run_full_scan(self) -> None:
        """DTCs + identification + readiness, queued from the GUI as one request."""
        self.read_dtcs("all")
        self.read_ecu_id()
        self.read_readiness()

    @pyqtSlot()
    def clear_dtcs(self) -> None:
        if not self._protocol:
            return
        if not self._write_guard("clear_dtcs"):
            return
        self._begin_io()
        try:
            ok = self._protocol.clear_dtcs()
            self.status_message.emit("DTCs cleared successfully." if ok else "Clear DTC request rejected by ECU.")
            if ok:
                self.dtc_results.emit([])
                if self._recorder.is_recording:
                    self._recorder.record_dtcs([])
        except Exception as exc:
            self.error_occurred.emit(f"Clear DTC error: {describe_error(exc)}")

    @pyqtSlot()
    def read_freeze_frame(self) -> None:
        if not self._protocol:
            return
        self._begin_io()
        try:
            ff = self._protocol.read_freeze_frame()
            self.freeze_frame_result.emit(ff)
        except Exception as exc:
            self.error_occurred.emit(f"Freeze Frame error: {describe_error(exc)}")

    @pyqtSlot()
    def read_ecu_id(self) -> None:
        if not self._protocol:
            return
        self._begin_io()
        try:
            ids = self._protocol.read_ecu_id()
            self.ecu_id_result.emit(ids)
        except Exception as exc:
            self.error_occurred.emit(f"ECU ID error: {describe_error(exc)}")

    @pyqtSlot()
    def read_readiness(self) -> None:
        if not self._protocol:
            return
        self._begin_io()
        try:
            monitors = self._protocol.read_readiness()
            self.readiness_result.emit(monitors)
        except Exception as exc:
            self.error_occurred.emit(f"Readiness error: {describe_error(exc)}")

    @pyqtSlot(bool)
    def set_polling(self, enabled: bool) -> None:
        self._polling_active = enabled
        if not self._poll_timer:
            return
        if enabled and self._protocol:
            self._poll_timer.start()
        else:
            self._poll_timer.stop()

    @pyqtSlot(int)
    def set_poll_interval(self, interval_ms: int) -> None:
        self._base_poll_ms = max(50, interval_ms)
        if self._poll_timer:
            self._poll_timer.setInterval(self._base_poll_ms)

    @pyqtSlot()
    def _on_poll_tick(self) -> None:
        if not self._protocol or not self._polling_active:
            return
        self._begin_io()
        started = time.monotonic()
        try:
            pids = self._protocol.read_live_pids()
            if pids:
                self.pid_results.emit(pids)
                if self._recorder.is_recording:
                    self._recorder.record_pids(pids)
        except OperationCancelled:
            self.set_polling(False)
            return
        except Exception as exc:
            self._hints.on_timeout()
            self.set_polling(False)
            self.error_occurred.emit(f"Live data error: {describe_error(exc)}")
            return
        # Slow links (K-line ~50-100 ms per request): stretch the interval to the
        # measured sweep time so ticks never queue up behind each other.
        took_ms = int((time.monotonic() - started) * 1000)
        if self._poll_timer:
            self._poll_timer.setInterval(max(self._base_poll_ms, int(took_ms * 1.2)))

    @pyqtSlot()
    def _on_keepalive_tick(self) -> None:
        """Keep the session open when nothing else has been sent recently.

        Live polling counts as traffic, so the tester-present message is only sent
        on idle links: it never competes with polling, and polling can no longer
        starve it (the check runs on every tick, polling or not).
        """
        if not self._protocol or not self._keepalive_timer:
            return
        idle_ms = (time.monotonic() - self._last_traffic) * 1000
        if idle_ms < self._keepalive_timer.interval() * 0.8:
            return
        self._begin_io()
        try:
            self._protocol.keep_alive()
            self._traffic()
        except OperationCancelled:
            pass
        except Exception as exc:
            log.warning("keep-alive failed: %s", exc)
            self.status_message.emit(f"Keep-alive failed: {describe_error(exc)}")

    @pyqtSlot(object, bool)
    def run_actuator(self, actuator: ActuatorDefinition, start: bool) -> None:
        if not self._protocol:
            self.error_occurred.emit("Not connected to ECU.")
            return
        if start and not self._write_guard("actuator", actuator):
            return
        self._begin_io()
        try:
            ok = self._protocol.run_actuator_test(actuator, start)
            self.actuator_result.emit(actuator.name, ok)
            action = "started" if start else "stopped"
            self.status_message.emit(f"Actuator {actuator.name} {action}: {'Success' if ok else 'Rejected'}")
        except Exception as exc:
            self.error_occurred.emit(f"Actuator test error: {describe_error(exc)}")

    @pyqtSlot(object)
    def run_special_function(self, func: SpecialFunctionDefinition) -> None:
        if not self._protocol:
            self.error_occurred.emit("Not connected to ECU.")
            return
        if not self._write_guard("special_function", func):
            return
        self._begin_io()
        try:
            ok = self._protocol.run_special_function(func)
            self.special_func_result.emit(func.name, ok)
            self.status_message.emit(f"Function {func.name}: {'Completed' if ok else 'Rejected'}")
        except Exception as exc:
            self.error_occurred.emit(f"Special function error: {describe_error(exc)}")

    @pyqtSlot(bytes)
    def send_raw_bytes(self, raw: bytes) -> None:
        if not self._protocol:
            self.error_occurred.emit("Connect to an interface before sending raw bytes.")
            return
        if not self._write_guard("raw"):
            return
        self._begin_io()
        try:
            self._protocol.send_raw_hex(raw)
        except Exception as exc:
            self.error_occurred.emit(f"Raw transmit error: {describe_error(exc)}")

    @pyqtSlot(bool)
    def toggle_l_line(self, high: bool) -> None:
        if self._transport:
            self._transport.set_l_line(high)
        self._hints.on_l_line_toggle(high)

    @pyqtSlot(bool)
    def toggle_echo_cancel(self, enabled: bool) -> None:
        if self._transport is not None:
            self._transport.echo_enabled = enabled

    @pyqtSlot(int)
    def send_break_pulse(self, duration_ms: int = 25) -> None:
        if self._transport:
            self._transport.send_break(duration_ms)
        self._hints.on_bus_break_sent()

    # Session Recording & Replay
    @pyqtSlot(str, dict)
    def start_session_recording(self, file_path: str, meta: Dict[str, Any]) -> None:
        if self._target is not None:
            meta = {"target": self._target.name, "protocol": self._target.protocol.value, **meta}
        self._recorder.start_recording(file_path, meta)
        self.led_changed.emit("rec", True)
        self.status_message.emit(f"Recording session to {os.path.basename(file_path)}")

    @pyqtSlot()
    def stop_session_recording(self) -> None:
        if not self._recorder.is_recording:
            return
        out = self._recorder.stop_recording()
        self.led_changed.emit("rec", False)
        if out:
            self.status_message.emit(f"Session saved: {os.path.basename(out)}")

    @pyqtSlot(str)
    def start_session_playback(self, file_path: str) -> None:
        self.disconnect_ecu()
        self._player = SessionPlayer(
            frame_cb=lambda f: self.frame_logged.emit(f),
            pid_cb=lambda p: self.pid_results.emit(p),
            dtc_cb=lambda d: self.dtc_results.emit(d),
            progress_cb=lambda pct, cur, tot: self.playback_progress.emit(pct, cur, tot),
            state_cb=lambda k, v: None,
        )
        if self._player.load(file_path):
            self._player.play()
            self.led_changed.emit("play", True)
            self.playback_state_changed.emit(True, False)
            self.status_message.emit(f"Playing session {os.path.basename(file_path)}")
        else:
            self.error_occurred.emit(f"Failed to load recording file: {file_path}")

    @pyqtSlot()
    def pause_session_playback(self) -> None:
        if self._player:
            self._player.pause()
            self.playback_state_changed.emit(False, True)

    @pyqtSlot()
    def resume_session_playback(self) -> None:
        if self._player:
            self._player.play()
            self.playback_state_changed.emit(True, False)

    @pyqtSlot()
    def stop_session_playback(self) -> None:
        if self._player:
            self._player.stop()
            self._player = None
        self.led_changed.emit("play", False)
        self.playback_state_changed.emit(False, False)

    @pyqtSlot(float)
    def seek_playback(self, pct: float) -> None:
        if self._player:
            self._player.seek_percent(pct)

    @pyqtSlot(float)
    def set_playback_speed(self, speed: float) -> None:
        if self._player:
            self._player.set_speed(speed)
