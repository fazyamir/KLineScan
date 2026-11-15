"""
KWP2000 / ISO 14230 and ISO 9141-2 protocol implementation.

Covers:
  * KWP2000 fast init and ISO 9141-2 5-baud slow init
  * CARB Mode 01-0A OBD on K-line
  * KWP2000 0x18/0x14 DTCs, 0x1A identification, 0x21 local IDs
  * checksum validation and negative-response classification

All diagnostics are decoded from received bytes.  Missing or unsupported
services raise explicit errors instead of returning fabricated values.
"""

import time
from typing import List, Optional

from klinescan.core import kline_timing as KT
from klinescan.core.errors import (
    MalformedFrameError,
    NegativeResponseError,
    ScanTimeout,
    UnsupportedOperationError,
)
from klinescan.core.models import (
    ActuatorDefinition,
    DiagnosticTroubleCode,
    EcuIdItem,
    FreezeFrame,
    FreezeFramePid,
    LivePid,
    ProtocolType,
    ReadinessMonitor,
    SpecialFunctionDefinition,
)
from klinescan.data.dtc_db import lookup_dtc
from klinescan.protocols.base import BaseProtocol


def decode_dtc_pair(b1: int, b2: int) -> str:
    prefix = ("P", "C", "B", "U")[(b1 >> 6) & 0x03]
    digit2 = (b1 >> 4) & 0x03
    return f"{prefix}{digit2}{b1 & 0x0F:X}{b2:02X}"


class Kwp2000Protocol(BaseProtocol):
    poll_batch = 6  # K-line: ~50-100 ms per request (P2/P3 timing)

    def _build_frame(self, payload: bytes) -> bytes:
        if self.target.use_obd_header:
            header = bytes([0x68, 0x6A, self.target.tester_addr])
        elif self.target.explicit_length:
            header = bytes([0x80, self.target.ecu_addr, self.target.tester_addr, len(payload)])
        else:
            # header_format 0xC0 selects functional addressing (OBD KWP: C1 33 F1 ...)
            mode = 0xC0 if (self.target.header_format & 0xC0) == 0xC0 else 0x80
            header = bytes([mode | len(payload), self.target.ecu_addr, self.target.tester_addr])
        frame = header + payload
        return frame + bytes([sum(frame) & 0xFF])

    @property
    def _adapter_framed(self) -> bool:
        """ELM327-class adapters build headers/checksums and run the bus init themselves."""
        return hasattr(self.transport, "start_kline_session")

    def _exchange(self, payload: bytes, note: str = "") -> bytes:
        if self._adapter_framed:
            self.log("TX", payload, f"{note} (adapter framed)")
            body = self.transport.send_obd_payload(payload)
            if not body:
                return b""
            frame = self._build_frame(body)  # rebuild a full frame for the shared parsers
            self.log("RX", body, note)
            if len(body) >= 3 and body[0] == 0x7F:
                raise NegativeResponseError(body[1], body[2])
            return frame
        tx = self._build_frame(payload)
        self.log("TX", tx, note)
        self.transport.write(tx)
        rx = self._read_frame()
        while rx:
            self.log("RX", rx, note)
            body = self._extract_payload(rx)
            if len(body) >= 3 and body[0] == 0x7F and body[2] == 0x78:
                rx = self._read_frame(timeout=KT.P3_MAX / 1000)  # response pending
                continue
            if len(body) >= 3 and body[0] == 0x7F:
                raise NegativeResponseError(body[1], body[2])
            break
        return rx

    def _rx(self, size: int, timeout: float) -> bytes:
        """Read from bytes left over by frame splitting first, then the transport."""
        pending = getattr(self, "_pending_rx", b"")
        if pending:
            out, self._pending_rx = pending[:size], pending[size:]
            if len(out) < size:
                out += self.transport.read(size - len(out), timeout=timeout)
            return out
        return self.transport.read(size, timeout=timeout)

    @staticmethod
    def _split_iso9141(stream: bytes) -> int:
        """End of the first ISO 9141-2 frame, or 0 if no prefix has a valid checksum.

        ISO 9141-2 frames carry no length byte.  A prefix ends a frame when its
        additive checksum matches and the next bytes start a new frame with the same
        header (or the stream ends).
        """
        for end in range(5, len(stream) + 1):
            frame = stream[:end]
            if (sum(frame[:-1]) & 0xFF) == frame[-1] and (end == len(stream) or stream[end:end + 2] == stream[:2]):
                return end
        return 0

    # Inter-byte gap that ends a frame: ECU inter-byte time P1max (20 ms) plus
    # USB-serial packet latency.  Messages are separated by at least P2min (25 ms).
    FRAME_GAP_S = (KT.P1_MAX + 15) / 1000

    def _read_until_gap(self, gap: float, limit: int = 260) -> bytes:
        out = bytearray()
        while len(out) < limit:
            chunk = self._rx(1, timeout=gap)
            if not chunk:
                break
            out += chunk
        return bytes(out)

    def _read_frame(self, timeout: float = 0.6) -> bytes:
        """Read one K-line frame and verify its checksum (raises MalformedFrameError)."""
        first = self._rx(1, timeout=timeout)
        if not first:
            return b""
        first += self._rx(2, timeout=self.FRAME_GAP_S * 3)
        if len(first) < 3:
            raise MalformedFrameError(f"truncated K-line frame {first.hex(' ').upper()}")

        if first[0] in (0x48, 0x68):
            stream = first + self._read_until_gap(self.FRAME_GAP_S)
            end = self._split_iso9141(stream)
            if not end:
                raise MalformedFrameError(
                    f"ISO 9141-2 checksum error in {stream.hex(' ').upper()}")
            self._pending_rx = getattr(self, "_pending_rx", b"") + stream[end:]
            return stream[:end]

        fmt = first[0]
        length = fmt & 0x3F
        frame = first
        if length == 0:
            len_byte = self._rx(1, timeout=0.2)
            if not len_byte:
                raise MalformedFrameError("KWP2000 frame missing length byte")
            length = len_byte[0]
            frame += len_byte
        frame += self._rx(length + 1, timeout=0.3)
        if not self._frame_checksum_ok(frame):
            self._read_until_gap(self.FRAME_GAP_S)  # resynchronise on the next gap
            raise MalformedFrameError(f"KWP2000 checksum error in {frame.hex(' ').upper()}")
        return frame

    def _extract_payload(self, frame: bytes) -> bytes:
        if len(frame) < 5:
            return b""
        if frame[0] in (0x48, 0x68):
            return frame[3:-1]
        if (frame[0] & 0x3F) == 0:
            return frame[4:-1]
        return frame[3:-1]

    @staticmethod
    def _frame_checksum_ok(frame: bytes) -> bool:
        """Additive 8-bit checksum over header + data (ISO 9141-2 and ISO 14230-2)."""
        if len(frame) < 4:
            return False
        if frame[0] not in (0x48, 0x68):
            length = frame[0] & 0x3F
            header = 4 if length == 0 else 3
            if length == 0:
                length = frame[3]
            if len(frame) != header + length + 1:
                return False
        return (sum(frame[:-1]) & 0xFF) == frame[-1]

    def connect(self) -> bool:
        if self._adapter_framed:
            self.transport.configure_target(self.target)
            self.log("INFO", b"", "Adapter bus initiation (AT FI / AT SI)")
            if not self.transport.start_kline_session():
                return False
            probe = bytes([0x01, 0x00]) if self.target.use_obd_header else bytes([0x3E])
            try:
                return bool(self._extract_payload(self._exchange(probe, "Link check")))
            except NegativeResponseError:
                return True  # ECU is talking, it just rejected the probe service
        if self.target.protocol == ProtocolType.ISO9141:
            parities = [self.target.five_baud_parity or "odd"]
            parities.append("none" if parities[0] == "odd" else "odd")
            for parity in parities:
                if self._slow_init(parity):
                    return True
            return False

        self.log("INFO", b"", "25ms KWP2000 fast-init pulse")
        self.transport.fast_init_pulse()
        rx = self._exchange(bytes([0x81]), "StartCommunication")
        payload = self._extract_payload(rx)
        return len(payload) >= 1 and payload[0] == 0xC1

    def _slow_init(self, parity: str) -> bool:
        address = self.target.slow_init_addr
        self.log("INFO", bytes([address]), f"5-baud slow init ({parity} parity)")
        try:
            keybytes = self.transport.slow_init_5baud(address, parity=parity)
        except TypeError:  # transports without the parity option
            keybytes = self.transport.slow_init_5baud(address)
        if len(keybytes) < 3 or keybytes[0] != 0x55:
            self.log("ERR", keybytes, "Slow init failed (expected 0x55 KW1 KW2)")
            return False
        self.log("RX", keybytes, f"Keybytes: {keybytes[1]:02X} {keybytes[2]:02X}")
        time.sleep(0.030)  # W4 min 25 ms before the tester's inverted KW2
        self.transport.write(bytes([(~keybytes[2]) & 0xFF]))
        ack = self.transport.read(1, timeout=0.3)
        from klinescan.core.kline_timing import odd_parity_address
        expected = (~address) & 0xFF
        accepted = {expected, (~odd_parity_address(address)) & 0xFF}
        if ack and ack[0] in accepted:
            self.log("RX", ack, "ECU confirmed with inverted address")
        else:
            self.log("RX", ack, f"Inverted address not confirmed (expected {expected:02X})")
        time.sleep(0.060)  # P3 min 55 ms before the first request
        return True

    def keep_alive(self) -> None:
        if self.target.use_obd_header:
            self._exchange(bytes([0x01, 0x01]), "KeepAlive (Mode 01 PID 01)")
        else:
            self._exchange(bytes([0x3E]), "TesterPresent (0x3E)")

    def _obd_dtcs(self, mode: int, status: str) -> List[DiagnosticTroubleCode]:
        response = self._exchange(bytes([mode]), f"Read {status} DTCs (Mode 0{mode:02X})")
        payload = self._extract_payload(response)
        positive = mode + 0x40
        if not payload or payload[0] != positive:
            return []
        dtcs: List[DiagnosticTroubleCode] = []
        for index in range(1, len(payload) - 1, 2):
            b1, b2 = payload[index], payload[index + 1]
            if b1 == 0 and b2 == 0:
                continue
            code = decode_dtc_pair(b1, b2)
            desc, sev = lookup_dtc(code)
            dtcs.append(DiagnosticTroubleCode(code, f"{b1:02X} {b2:02X}", status, desc, sev))
        return dtcs

    def read_dtcs(self, status_filter: str = "all") -> List[DiagnosticTroubleCode]:
        if self.target.use_obd_header:
            dtcs: List[DiagnosticTroubleCode] = []
            if status_filter in ("all", "stored"):
                dtcs.extend(self._obd_dtcs(0x03, "Stored"))
            if status_filter in ("all", "pending"):
                dtcs.extend(self._obd_dtcs(0x07, "Pending"))
            if status_filter in ("all", "permanent"):
                dtcs.extend(self._obd_dtcs(0x0A, "Permanent"))
            return dtcs

        response = self._exchange(bytes([0x18, 0x00, 0xFF, 0x00]), "KWP ReadDTCByStatus")
        payload = self._extract_payload(response)
        if not payload or payload[0] != 0x58:
            return []
        raw = payload[2:]
        dtcs = []
        for index in range(0, len(raw) - 2, 3):
            b1, b2, status = raw[index], raw[index + 1], raw[index + 2]
            if b1 == 0 and b2 == 0:
                continue
            code = decode_dtc_pair(b1, b2)
            desc, sev = lookup_dtc(code)
            status_text = (
                "Stored" if status & 0x20 else ("Pending" if status & 0x04 else f"0x{status:02X}")
            )
            dtcs.append(
                DiagnosticTroubleCode(code, f"{b1:02X} {b2:02X}", status_text, desc, sev)
            )
        return dtcs

    def clear_dtcs(self) -> bool:
        if self.target.use_obd_header:
            response = self._exchange(bytes([0x04]), "Clear OBD-II DTCs (Mode 04)")
            payload = self._extract_payload(response)
            return len(payload) >= 1 and payload[0] == 0x44
        response = self._exchange(bytes([0x14, 0xFF, 0x00]), "KWP ClearDiagnosticInformation")
        payload = self._extract_payload(response)
        return len(payload) >= 1 and payload[0] == 0x54

    def read_live_pids(self) -> List[LivePid]:
        if getattr(self.target, "live_params", None):
            return self.read_defined_params()
        # No manufacturer definition: try SAE J1979 PIDs on this session (EOBD-era
        # KWP2000 engine ECUs answer Service 01 on physical addressing too).
        return self.poll_standard_pids()

    def read_defined_params(self) -> List[LivePid]:
        """Pack-defined live values: one request per distinct ``request`` bytes."""
        from klinescan.core.pack_schema import evaluate_pid_bytes

        responses = {}
        results: List[LivePid] = []
        for index, param in enumerate(self.target.live_params):
            key = bytes(param.request)
            if key not in responses:
                try:
                    responses[key] = self._extract_payload(
                        self._exchange(key, f"Live data {key.hex(' ').upper()}"))
                except (ScanTimeout, NegativeResponseError, MalformedFrameError) as exc:
                    self.log("WARN", key, f"Live data request failed: {exc}")
                    responses[key] = b""
            payload = responses[key]
            if not payload or payload[0] != (key[0] + 0x40) & 0xFF:
                continue
            raw = payload[param.offset:param.offset + param.length]
            if len(raw) < param.length:
                continue
            try:
                value = evaluate_pid_bytes(param.formula, raw)
            except ValueError as exc:
                self.log("WARN", raw, f"{param.name}: {exc}")
                continue
            results.append(LivePid(
                pid=param.pid or (0x10000 + index), name=param.name,
                value=f"{value:.2f}".rstrip("0").rstrip("."), unit=param.unit,
                raw_hex=raw.hex(" ").upper(), numeric_value=value, category=param.category))
        return results

    def query_pid(self, pid: int):
        payload = self._extract_payload(self._exchange(bytes([0x01, pid]), f"PID 0x{pid:02X}"))
        if len(payload) >= 2 and payload[0] == 0x41 and payload[1] == pid:
            return payload[2:]
        return None

    def read_freeze_frame(self) -> Optional[FreezeFrame]:
        if not self.target.use_obd_header:
            raise UnsupportedOperationError(
                "KWP2000 freeze-frame reading is not implemented for this ECU"
            )
        base = self._exchange(bytes([0x02, 0x02, 0x00]), "Read Freeze Frame (Mode 02)")
        payload = self._extract_payload(base)
        if len(payload) < 4 or payload[0] != 0x42 or payload[1] != 0x02:
            return None
        dtc_trigger = decode_dtc_pair(payload[3], payload[4]) if len(payload) >= 5 else ""
        pids: List[FreezeFramePid] = []
        if dtc_trigger:
            pids.append(
                FreezeFramePid(0x02, "DTC Trigger", dtc_trigger, "", payload[3:5].hex(" ").upper())
            )
        for pid, name, unit, decoder in (
            (0x04, "Calculated Load", "%", lambda d: f"{d[0] * 100 / 255:.1f}"),
            (0x05, "Coolant Temp", "°C", lambda d: f"{d[0] - 40}"),
            (0x0C, "Engine RPM", "rpm", lambda d: f"{((d[0] << 8) | d[1]) / 4:.0f}"),
            (0x0D, "Vehicle Speed", "km/h", lambda d: f"{d[0]}"),
        ):
            response = self._exchange(bytes([0x02, pid, 0x00]), f"Freeze PID 0x{pid:02X}")
            frame = self._extract_payload(response)
            if len(frame) >= 5 and frame[0] == 0x42 and frame[1] == pid:
                value_bytes = frame[3:]
                try:
                    value = decoder(value_bytes)
                except (IndexError, ZeroDivisionError):
                    continue
                pids.append(
                    FreezeFramePid(pid, name, value, unit, value_bytes.hex(" ").upper())
                )
        if not pids:
            return None
        return FreezeFrame(dtc_trigger=dtc_trigger or "Unknown", pids=pids)

    def _exchange_all(self, payload: bytes, note: str = "", quiet_s: float = 0.25) -> List[bytes]:
        """One request, every response frame (multi-message replies such as the K-line VIN)."""
        if self._adapter_framed:
            self.log("TX", payload, f"{note} (adapter framed)")
            bodies = [body for _, body in self.transport.request(payload)]
            for body in bodies:
                self.log("RX", body, note)
            return bodies
        tx = self._build_frame(payload)
        self.log("TX", tx, note)
        self.transport.write(tx)
        bodies = []
        frame = self._read_frame()
        while frame:
            self.log("RX", frame, note)
            bodies.append(self._extract_payload(frame))
            frame = self._read_frame(timeout=quiet_s)
        return bodies

    def _read_info_type(self, info: int, note: str) -> bytes:
        """Service 09: K-line replies are numbered messages ``49 info seq data...``."""
        parts = {}
        for body in self._exchange_all(bytes([0x09, info]), note):
            if len(body) >= 3 and body[0] == 0x49 and body[1] == info:
                parts[body[2]] = body[3:]
        return b"".join(parts[k] for k in sorted(parts))

    def read_ecu_id(self) -> List[EcuIdItem]:
        items: List[EcuIdItem] = []
        if self.target.use_obd_header or (self.target.header_format & 0xC0) == 0xC0:
            for info, label in ((0x02, "Vehicle VIN"), (0x04, "Calibration ID"), (0x0A, "ECU name")):
                try:
                    raw = self._read_info_type(info, f"Read {label} (Service 09 {info:02X})")
                except Exception:
                    continue
                text = raw.decode("ascii", errors="ignore").strip("\x00 ")
                if text:
                    items.append(EcuIdItem(label, text, raw.hex()))
            return items

        for identifier, label in (
            (0x90, "ECU System ID"),
            (0x91, "ECU Hardware Number"),
            (0x92, "ECU Software Number"),
            (0x9A, "ECU Supplier Number"),
        ):
            response = self._exchange(bytes([0x1A, identifier]), f"KWP ReadEcuIdentification 0x{identifier:02X}")
            payload = self._extract_payload(response)
            if len(payload) >= 3 and payload[0] == 0x5A:
                text = bytes(payload[2:]).decode("ascii", errors="ignore").strip("\x00 ")
                if text:
                    items.append(EcuIdItem(label, text, payload[2:].hex()))
        return items

    def read_readiness(self) -> List[ReadinessMonitor]:
        if not self.target.use_obd_header:
            return []
        response = self._exchange(bytes([0x01, 0x01]), "Read Monitor Readiness (PID 01)")
        payload = self._extract_payload(response)
        if len(payload) < 6 or payload[0] != 0x41 or payload[1] != 0x01:
            return []
        b2, b3 = payload[3], payload[4]
        return [
            ReadinessMonitor("Misfire", bool(b2 & 0x01), not bool(b2 & 0x10)),
            ReadinessMonitor("Fuel System", bool(b2 & 0x02), not bool(b2 & 0x20)),
            ReadinessMonitor("Components", bool(b2 & 0x04), not bool(b2 & 0x40)),
            ReadinessMonitor("Catalyst", bool(b3 & 0x01), not bool(b3 & 0x10)),
            ReadinessMonitor("Evaporative", bool(b3 & 0x04), not bool(b3 & 0x40)),
            ReadinessMonitor("O2 Sensor", bool(b3 & 0x20), not bool(b3 & 0x02)),
        ]

    def security_exchange(self, payload: bytes) -> bytes:
        return self._extract_payload(self._exchange(payload, f"SecurityAccess {payload[:2].hex(' ').upper()}"))

    def run_actuator_test(self, actuator: ActuatorDefinition, start: bool = True) -> bool:
        command = actuator.payload_start if start else actuator.payload_stop
        if not command:
            raise UnsupportedOperationError(
                f"No KWP actuator payload defined for {actuator.name}"
            )
        response = self._exchange(command, f"Actuator {actuator.name} {'START' if start else 'STOP'}")
        payload = self._extract_payload(response)
        return len(payload) >= 1 and payload[0] in (0x70, 0x6F, 0x48)

    def run_special_function(self, func: SpecialFunctionDefinition) -> bool:
        if not func.command:
            raise UnsupportedOperationError(
                f"No KWP routine command defined for {func.name}"
            )
        self.unlock(func.security_level)
        response = self._exchange(func.command, f"Special Function {func.name}")
        payload = self._extract_payload(response)
        return len(payload) >= 1 and payload[0] in (0x71, 0x50, 0x67)

    def send_raw_hex(self, raw: bytes) -> bytes:
        self.log("TX", raw, "Manual raw transmit")
        self.transport.write(raw)
        response = self._read_frame(timeout=0.8)
        if response:
            self.log("RX", response, "Manual raw response")
        return response
