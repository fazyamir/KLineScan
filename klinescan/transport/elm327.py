"""ELM327 / STN11xx / OBDLink transport over USB, Bluetooth (SPP or BLE) and Wi-Fi.

Every command below is taken from the ELM327 datasheet (Elm Electronics,
ELM327DSH, https://www.scantool.net/scantool/downloads/103/elm327dsh.pdf):

* ``AT SP h``: 3 ISO 9141-2 (5 baud init), 4 ISO 14230-4 (5 baud init),
  5 ISO 14230-4 (fast init), 6/7 CAN 11/29-bit 500k, 8/9 CAN 11/29-bit 250k,
  B/C user CAN (``AT PB xx yy``: xx = PP 2C options, rate = 500 / yy kbps).
* ``AT SH xx yy zz``: for KWP a non-zero second digit of xx means a 3-byte
  header with the length in xx; ``80`` means a 4th length byte (v1.2+).
* ``AT IIA hh`` slow-init address ("used exactly as provided"),
  ``AT FI`` / ``AT SI`` explicit fast / slow initiation, ``AT KW0`` relaxes
  key-word checking for non-OBD ECUs, ``AT SW hh`` wake-up interval
  (hh x 20 ms, default about 3 s), ``AT WM`` wake-up message (checksum added).
* CAN: ``AT CRA`` receive filter, ``AT FC SH/SD/SM`` custom flow control,
  ``AT CEA hh`` extended (first-byte) addressing, ``AT CAF1`` auto ISO-TP.
* Errors/alerts: ``NO DATA``, ``CAN ERROR``, ``BUS INIT: ...ERROR``,
  ``UNABLE TO CONNECT``, ``BUS ERROR``, ``BUS BUSY``, ``FB ERROR``,
  ``DATA ERROR``, ``BUFFER FULL``, ``STOPPED``, ``LV RESET``, ``ERRxx``, ``?``.
* Host link: 9600 or 38400 baud by default; Bluetooth/Wi-Fi bridges ignore it.
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple

from klinescan.core.errors import (
    AdapterError,
    BusError,
    NegativeResponseError,
    ScanTimeout,
    UnsupportedOperationError,
)
from klinescan.core.models import EcuTarget, ProtocolType
from klinescan.core.registry import TransportCapabilities, register_transport
from klinescan.transport.base import BaseTransport, TransportError
from klinescan.transport.links import ByteLink, open_link

ELM_CAPABILITIES = TransportCapabilities(
    supports_can=True,
    supports_kline=True,
    supports_j1850_pwm=True,
    supports_j1850_vpw=True,
    supports_fast_init=True,
    supports_5baud=True,
    supports_raw_frames=False,
    supports_obd_passthrough=True,
    supports_extended_can=True,
    max_baudrate=500000,
    description="ELM327 / STN / OBDLink over USB, Bluetooth (SPP/BLE) or Wi-Fi",
)

HOST_BAUD_CANDIDATES = (38400, 9600, 115200, 57600, 230400, 500000)
CAN_BITRATE_PROTOCOLS = {  # (11-bit, 29-bit)
    500000: ("6", "7"),
    250000: ("8", "9"),
}
ERROR_MARKERS = {
    "CAN ERROR": BusError, "BUS ERROR": BusError, "BUS BUSY": BusError, "FB ERROR": BusError,
    "LV RESET": AdapterError, "BUFFER FULL": AdapterError, "STOPPED": AdapterError,
    "UNABLE TO CONNECT": ScanTimeout, "DATA ERROR": BusError,
}
_HEX_LINE = re.compile(r"^[0-9A-F ]+$")


def _hex(value: int, width: int = 2) -> str:
    return f"{value:0{width}X}"


@register_transport("elm327", ELM_CAPABILITIES)
class Elm327Transport(BaseTransport):
    name = "elm327"
    PROTOCOL_MAP = {
        "AUTO": "0", "SAE_J1850_PWM": "1", "SAE_J1850_VPW": "2", "ISO_9141_2": "3",
        "ISO_14230_SLOW": "4", "ISO_14230_FAST": "5", "CAN_11B_500K": "6", "CAN_29B_500K": "7",
        "CAN_11B_250K": "8", "CAN_29B_250K": "9", "J1939_CAN": "A",
    }
    supports_obd_passthrough = True

    def __init__(self, led_callback=None, link: Optional[ByteLink] = None):
        super().__init__(led_callback)
        self._link: Optional[ByteLink] = link
        self._pending_rx = bytearray()
        self.device_version = "Unknown"
        self.battery_voltage = ""
        self.is_stn = False
        self.protocol = "0"
        self.protocol_name = ""
        self._target: Optional[EcuTarget] = None
        self._kline_header_len = 3
        self.can_bitrate = 0
        self.can_rx_id: Optional[int] = None
        self.last_raw = ""

    # ------------------------------------------------------------- lifecycle
    def open(self, port: str, baudrate: int = 38400) -> None:
        self.port = port
        if self._link is None:
            self._link = open_link(port, baudrate or 38400)
        if self._link.kind == "serial":
            self._sync_host_baud(baudrate or 38400)
        else:
            self._reset()
        self.requested_baud = self.actual_baud = baudrate or 38400
        for cmd in ("ATE0", "ATL0", "ATS1", "ATH1", "ATAL", "ATAT1", "ATCAF1"):
            self.at(cmd)
        self.device_version = self.at("ATI")
        self.is_stn = "?" not in self.at("STI", check=False) and bool(self.at("STI", check=False))
        self.battery_voltage = self.at("ATRV", check=False)
        self.at("ATSP0")
        self._mark_open()

    def _reset(self) -> str:
        self._link.write(b"\r")
        time.sleep(0.1)
        self._link.reset_input()
        return self._command("ATZ", timeout=3.0)

    def _sync_host_baud(self, first: int) -> None:
        """Wired adapters default to 9600 or 38400 (datasheet); try common rates."""
        tried = [first] + [b for b in HOST_BAUD_CANDIDATES if b != first]
        for baud in tried:
            self._link.set_baudrate(baud)
            reply = self._reset()
            if "ELM" in reply.upper() or "STN" in reply.upper() or "OK" in reply.upper():
                self.actual_baud = baud
                return
        raise AdapterError(f"no ELM327 prompt on {self.port} at {', '.join(map(str, tried))} baud")

    def close(self) -> None:
        if self._link:
            try:
                self.at("ATPC", check=False)
            except Exception:
                pass
            self._link.close()
        self._link = None
        self._mark_closed()

    def set_baudrate(self, baudrate: int) -> None:
        self.requested_baud = baudrate

    def flush(self) -> None:
        self._pending_rx.clear()
        if self._link:
            self._link.reset_input()

    def capabilities(self) -> TransportCapabilities:
        return ELM_CAPABILITIES

    # ---------------------------------------------------------- raw commands
    def _interrupt(self) -> None:
        """A character received while the ELM327 works stops it ("STOPPED"); wait for the prompt."""
        try:
            self._link.write(b"\r")
            end = time.monotonic() + 0.5
            while time.monotonic() < end:
                if b">" in self._link.read(64, 0.05):
                    break
        except Exception:
            pass

    def _command(self, text: str, timeout: float = 2.0) -> str:
        if not self._link:
            raise TransportError("ELM327 not open")
        self._link.write((text + "\r").encode("ascii"))
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while time.monotonic() < deadline:
            if self.cancel_token.cancelled:
                self._interrupt()
                from klinescan.core.errors import OperationCancelled

                raise OperationCancelled(f"ELM327 command '{text}' cancelled")
            chunk = self._link.read(64, 0.05)
            if not chunk:
                continue
            buf.extend(chunk)
            if b">" in chunk:
                break
        else:
            raise ScanTimeout(f"ELM327 did not return a prompt for '{text}'")
        reply = buf.decode("ascii", errors="ignore").replace(">", "")
        # strip echoed command if echo is still on
        lines = [l.strip() for l in reply.replace("\r", "\n").split("\n") if l.strip()]
        if lines and lines[0].replace(" ", "").upper() == text.replace(" ", "").upper():
            lines = lines[1:]
        self.last_raw = "\n".join(lines)
        return self.last_raw

    def at(self, cmd: str, check: bool = True, timeout: float = 2.0) -> str:
        reply = self._command(cmd, timeout)
        if check and reply.strip() == "?":
            raise UnsupportedOperationError(f"adapter rejected '{cmd}' (older ELM327 or clone)")
        return reply

    def set_protocol(self, protocol_key: str) -> bool:
        return "OK" in self.at(f"ATSP{self.PROTOCOL_MAP.get(protocol_key, '0')}", check=False)

    # ---------------------------------------------------- session configuration
    DPN_NAMES = {"1": "SAE J1850 PWM", "2": "SAE J1850 VPW", "3": "ISO 9141-2", "4": "ISO 14230-4 KWP (5 baud)",
                 "5": "ISO 14230-4 KWP (fast)", "6": "CAN 11-bit 500k", "7": "CAN 29-bit 500k",
                 "8": "CAN 11-bit 250k", "9": "CAN 29-bit 250k", "A": "SAE J1939", "B": "USER1 CAN", "C": "USER2 CAN"}
    AUTO_OBD = "obd:auto"

    def auto_protocol(self) -> str:
        """Let the adapter search the OBD protocols (AT SP 0) and report the one found (AT DPN)."""
        self._target = None
        self.at("ATSP0")
        reply = self._command("0100", timeout=12.0)  # the search can take several seconds
        self._raise_for_errors(reply, allow_no_data=False)
        dpn = self.at("ATDPN", check=False).strip().upper().lstrip("A")[:1]
        self.protocol = dpn or "0"
        self.protocol_name = self.DPN_NAMES.get(dpn, f"protocol {dpn}")
        return self.protocol_name

    def configure_target(self, target: EcuTarget) -> None:
        """Program protocol, headers, filters and wake-up for one ECU."""
        if target.source_ref == self.AUTO_OBD:
            self._target = None   # keep the protocol found by auto_protocol(); default OBD headers
            return
        self._target = target
        if target.protocol in (ProtocolType.CAN_OBD, ProtocolType.UDS_CAN):
            self._configure_can(target)
        elif target.protocol == ProtocolType.ISO9141:
            self._configure_kline(target, slow=True)
        else:
            self._configure_kline(target, slow=False)

    def _configure_kline(self, target: EcuTarget, slow: bool) -> None:
        if target.use_obd_header:
            proto = "3" if slow else "5"
            header = (0x68, 0x6A, target.tester_addr)
            self._kline_header_len = 3
        else:
            proto = "4" if slow else "5"
            # Datasheet: second digit of the first byte != 0 -> 3-byte header with length;
            # "80" -> separate length byte (4-byte header).
            fmt = 0x80 if target.explicit_length else 0x81
            header = (fmt, target.ecu_addr, target.tester_addr)
            self._kline_header_len = 4 if target.explicit_length else 3
        self.at(f"ATSP{proto}")
        self.protocol = proto
        self.at("ATSH" + "".join(_hex(b) for b in header))
        if slow:
            init = target.slow_init_addr
            self.at(f"ATIIA{_hex(init)}", check=False)
        if not target.use_obd_header:
            self.at("ATKW0", check=False)  # manufacturer ECUs often send non-OBD key words
            # keep-alive: TesterPresent with the session header, every keep_alive_ms (x20 ms units)
            self.at("ATWM" + "".join(_hex(b) for b in header) + "3E", check=False)
        interval = max(1, min(0xFF, (target.keep_alive_ms or 2000) // 20))
        self.at(f"ATSW{_hex(interval)}", check=False)

    def start_kline_session(self) -> bool:
        """Run the bus initiation explicitly so failures are reported, not hidden."""
        if self.protocol == "5":
            reply = self.at("ATFI", check=False, timeout=5.0)
        else:
            reply = self.at("ATSI", check=False, timeout=8.0)
        self._raise_for_errors(reply, allow_no_data=False)
        return "OK" in reply.upper()

    def _configure_can(self, target: EcuTarget) -> None:
        rates = [target.can_bitrate] if target.can_bitrate else [500000, 250000, 125000]
        last_error: Exception | None = None
        for rate in rates:
            try:
                self._select_can_rate(rate, target.can_extended_id)
                self._program_can_ids(target)
                self.can_bitrate = rate
                return
            except BusError as exc:
                last_error = exc
                continue
        raise BusError(f"no CAN response at {', '.join(str(r // 1000) + 'k' for r in rates)}: {last_error}")

    def _select_can_rate(self, rate: int, extended: bool) -> None:
        self.at("ATPC", check=False)
        if rate in CAN_BITRATE_PROTOCOLS:
            proto = CAN_BITRATE_PROTOCOLS[rate][1 if extended else 0]
            self.at(f"ATSP{proto}")
        else:
            divisor = 500000 // rate
            # PP 2C bits: b7 1 = 11-bit TX, b0 = ISO 15765-4 formatting (datasheet PP table)
            options = 0x81 if not extended else 0x01
            self.at(f"ATPB{_hex(options)}{_hex(divisor)}")
            self.at("ATSPB")
            proto = "B"
        self.protocol = proto

    def _program_can_ids(self, target: EcuTarget) -> None:
        width = 8 if target.can_extended_id else 3
        tx = target.can_tx_id
        self.at(f"ATSH{_hex(tx, width)}")
        if target.can_ext_address is not None:
            self.at(f"ATCEA{_hex(target.can_ext_address)}")
        else:
            self.at("ATCEA", check=False)
        rx = target.can_rx_id or None
        if rx is None:
            rx = self.discover_responder(target)
        self.can_rx_id = rx
        self.at(f"ATCRA{_hex(rx, width)}")
        if target.can_ext_address is not None:
            # Flow control must carry the target address first (datasheet, CAN extended addressing)
            self.at(f"ATFCSH{_hex(tx, width)}")
            self.at(f"ATFCSD{_hex(target.can_ext_address)}300000")
            self.at("ATFCSM1")
        else:
            self.at("ATFCSM0", check=False)

    def discover_responder(self, target: EcuTarget) -> int:
        """Send TesterPresent / DiagnosticSessionControl and learn the responding CAN ID."""
        self.at("ATCRA", check=False)
        for probe in ("3E00", "1001", "0100"):
            reply = self._command(probe, timeout=1.5)
            if any(m in reply.upper() for m in ("CAN ERROR", "BUS ERROR")):
                raise BusError(reply)
            for line in reply.splitlines():
                parts = line.split()
                if len(parts) >= 2 and _HEX_LINE.match(line.upper()):
                    head = parts[0]
                    if len(head) in (3, 8):
                        return int(head, 16)
        raise ScanTimeout(f"no ECU answered on CAN ID 0x{target.can_tx_id:X}")

    # -------------------------------------------------------------- traffic
    def _raise_for_errors(self, reply: str, allow_no_data: bool = True) -> None:
        upper = reply.upper()
        if upper.strip() == "?":
            raise UnsupportedOperationError("adapter did not understand the request")
        if "NO DATA" in upper and not allow_no_data:
            raise ScanTimeout("NO DATA (ECU did not answer within AT ST timeout)")
        if "BUS INIT" in upper and "ERROR" in upper:
            raise BusError(f"bus initiation failed: {reply}")
        if re.search(r"\bERR\d\d\b", upper):
            raise AdapterError(f"adapter internal error: {reply}")
        for marker, exc in ERROR_MARKERS.items():
            if marker in upper:
                raise exc(f"{marker}: {reply}")

    def request(self, payload: bytes, timeout: float = 2.0) -> List[Tuple[str, bytes]]:
        """Send one service payload; return [(header, payload_bytes)] per responder."""
        self._set_led("tx", True)
        try:
            reply = self._command(payload.hex().upper(), timeout=timeout)
        finally:
            self._set_led("tx", False)
        self._raise_for_errors(reply)
        if "NO DATA" in reply.upper():
            return []
        responses = self._parse(reply)
        if responses:
            self._set_led("rx", True)
            self._set_led("rx", False)
        return responses

    def _parse(self, reply: str) -> List[Tuple[str, bytes]]:
        lines = [l.strip().upper() for l in reply.splitlines() if l.strip()]
        lines = [l for l in lines if not l.startswith(("SEARCHING", "BUS INIT")) and l != "OK"]
        if self.protocol in ("6", "7", "8", "9", "B", "C") or (self.protocol == "0" and lines and len(lines[0].split()[0]) in (3, 8)):
            return self._parse_can(lines)
        return self._parse_kline(lines)

    def _parse_can(self, lines: List[str]) -> List[Tuple[str, bytes]]:
        frames: Dict[str, bytearray] = {}
        expected: Dict[str, int] = {}
        order: List[str] = []
        ext = self._target.can_rx_ext_address if self._target else None
        cea = self._target is not None and self._target.can_ext_address is not None
        for line in lines:
            parts = line.split()
            if len(parts) < 2 or not all(len(p) in (2, 3, 8) for p in parts):
                continue
            header, data = parts[0], [int(p, 16) for p in parts[1:] if len(p) == 2]
            if cea and data:
                data = data[1:]  # first byte is the extended (tester) address
            if not data:
                continue
            if header not in frames:
                frames[header] = bytearray()
                order.append(header)
            pci = data[0] >> 4
            if pci == 0:
                frames[header].extend(data[1:1 + (data[0] & 0x0F)])
            elif pci == 1:
                expected[header] = ((data[0] & 0x0F) << 8) | data[1]
                frames[header].extend(data[2:])
            elif pci == 2:
                frames[header].extend(data[1:])
        out = []
        for header in order:
            body = bytes(frames[header])
            if header in expected:
                body = body[:expected[header]]
            out.append((header, body))
        return out

    def _parse_kline(self, lines: List[str]) -> List[Tuple[str, bytes]]:
        out = []
        for line in lines:
            if not _HEX_LINE.match(line):
                continue
            data = [int(p, 16) for p in line.split()]
            if len(data) < 4:
                continue
            fmt = data[0]
            if fmt in (0x48, 0x68, 0x41):           # ISO 9141-2 header: 3 bytes + checksum
                head, body = data[:3], data[3:-1]
            elif fmt & 0x3F == 0 and len(data) > 4:  # KWP with separate length byte
                head, body = data[:4], data[4:4 + data[3]]
            else:                                     # KWP length in format byte
                head, body = data[:3], data[3:3 + (fmt & 0x3F)]
            out.append(("".join(_hex(b) for b in head), bytes(body)))
        return out

    # Shared ISO-TP / K-line passthrough contract used by the protocol layers.
    def send_obd_payload(self, payload: bytes, timeout: float = 2.0) -> bytes:
        responses = self.request(payload, timeout=timeout)
        if not responses:
            return b""
        # prefer the configured responder; otherwise the first positive answer
        for header, body in responses:
            if self.can_rx_id is not None and header and int(header, 16) == self.can_rx_id:
                return body
        for _, body in responses:
            if body and body[0] != 0x7F:
                return body
        return responses[0][1]

    send_obd_command = send_obd_payload

    def all_responses(self, payload: bytes, timeout: float = 2.0) -> List[Tuple[str, bytes]]:
        """Functional requests (e.g. 01 00 on 7DF) answered by several ECUs."""
        return self.request(payload, timeout)

    # Raw byte API kept for compatibility with older callers.
    def write(self, data: bytes) -> None:
        self._pending_rx.extend(self.send_obd_payload(data))

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        out = bytes(self._pending_rx[:size])
        del self._pending_rx[:size]
        return out

    def get_capabilities(self) -> Dict[str, bool]:
        return ELM_CAPABILITIES.as_dict()
