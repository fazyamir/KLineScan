"""Nissan Consult I (pre-OBD, grey 14-pin connector, ~1989-2000).

Serial: 9600 baud 8N1 over a Consult cable (separate TX/RX lines, no K-line echo).
Protocol facts, from the open-source OpenConsult project
(https://github.com/jonsim/openconsult, consult_interface.cpp / prototype.py):

* init: tester sends ``FF FF EF``; the ECU answers ``10``
* every command byte is echoed complemented (``5A`` -> ``A5``, ``D0`` -> ``2F``,
  ``D1`` -> ``2E``) and every parameter byte echoed unchanged; the tester then
  sends the go-ahead ``F0``
* the ECU streams frames ``FF <len> <data...>`` until the tester sends the stop
  command ``30``, answered by ``CF``
* ``5A <reg>`` reads one register per pair, one data byte per register, in order
* ``D1`` self-diagnosis: pairs of (code, starts since last occurrence); code 55 = no fault
* ``D0`` ECU part number: 22-byte frame

Register scalings are those documented there.  Clearing codes is not
implemented: its command is not in the sources we could verify.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from klinescan.core.errors import MalformedFrameError, ProtocolError, ScanTimeout, UnsupportedOperationError
from klinescan.core.models import DiagnosticTroubleCode, EcuIdItem, LivePid
from klinescan.protocols.base import BaseProtocol

INIT = bytes([0xFF, 0xFF, 0xEF])
INIT_OK = 0x10
GO = 0xF0
STOP = 0x30
STOP_OK = 0xCF
READ_REGISTER = 0x5A
SELF_DIAG = 0xD1
PART_NUMBER = 0xD0
FRAME_START = 0xFF


@dataclass(frozen=True)
class Register:
    name: str
    unit: str
    addresses: tuple           # (msb, lsb) for 16-bit values
    decode: Callable[[int], float]
    category: str = "Engine"


REGISTERS: List[Register] = [
    Register("Engine speed", "rpm", (0x00, 0x01), lambda v: v * 12.5),
    Register("MAF voltage (LH)", "V", (0x04, 0x05), lambda v: v * 5 / 1000),
    Register("MAF voltage (RH)", "V", (0x06, 0x07), lambda v: v * 5 / 1000),
    Register("Coolant temperature", "°C", (0x08,), lambda v: v - 50),
    Register("O2 sensor (LH)", "V", (0x09,), lambda v: v * 10 / 1000),
    Register("O2 sensor (RH)", "V", (0x0A,), lambda v: v * 10 / 1000),
    Register("Vehicle speed", "km/h", (0x0B,), lambda v: v * 2),
    Register("Battery voltage", "V", (0x0C,), lambda v: v * 80 / 1000),
    Register("Throttle position sensor", "V", (0x0D,), lambda v: v * 20 / 1000),
    Register("Fuel temperature", "°C", (0x0F,), lambda v: v - 50),
    Register("Intake air temperature", "°C", (0x11,), lambda v: v - 50),
    Register("Exhaust gas temperature sensor", "V", (0x12,), lambda v: v * 20 / 1000),
    Register("Injection time (LH)", "ms", (0x14, 0x15), lambda v: v * 0.01),
    Register("Ignition timing", "° BTDC", (0x16,), lambda v: 110 - v),
    Register("AAC valve", "%", (0x17,), lambda v: v / 2.0),
    Register("A/F alpha (LH)", "%", (0x1A,), lambda v: v),
    Register("A/F alpha (RH)", "%", (0x1B,), lambda v: v),
    Register("A/F alpha self-learn (LH)", "%", (0x1C,), lambda v: v),
    Register("A/F alpha self-learn (RH)", "%", (0x1D,), lambda v: v),
    Register("Injection time (RH)", "ms", (0x22, 0x23), lambda v: v * 0.01),
    Register("Tank fuel temperature", "°C", (0x26,), lambda v: v - 50),
]

# Fault codes confirmed in OpenConsult's prototype table.  Others are shown by number.
FAULT_CODES: Dict[int, str] = {
    0x11: "Crankshaft position sensor signal",
    0x12: "Mass air flow sensor",
    0x13: "Engine coolant temperature sensor",
    0x14: "Vehicle speed sensor",
    0x21: "Ignition signal",
    0x33: "Heated oxygen sensor",
    0x43: "Throttle position sensor",
}
NO_FAULT = 0x55


class ConsultProtocol(BaseProtocol):
    poll_batch = 0

    def __init__(self, transport, target, frame_cb=None):
        super().__init__(transport, target, frame_cb)
        self._supported: Optional[List[Register]] = None
        if hasattr(transport, "echo_enabled"):
            transport.echo_enabled = False  # Consult cables have separate RX/TX lines

    # -- link ------------------------------------------------------------------
    def _read_exact(self, n: int, timeout: float) -> bytes:
        out = bytearray()
        deadline = time.monotonic() + timeout
        while len(out) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            out += self.transport.read(n - len(out), timeout=remaining)
        return bytes(out)

    def connect(self) -> bool:
        if hasattr(self.transport, "start_kline_session") or getattr(self.transport, "supports_obd_passthrough", False):
            from klinescan.core.errors import ConfigurationError

            raise ConfigurationError(
                "Nissan Consult needs a Consult cable on a plain serial port (KL Serial / FTDI "
                "transport at 9600 baud); ELM327-type adapters cannot speak it.")
        self._halt(quiet=True)
        self.transport.flush()
        self.log("TX", INIT, "Consult init")
        self.transport.write(INIT)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            byte = self.transport.read(1, timeout=0.2)
            if byte == bytes([INIT_OK]):
                self.log("RX", byte, "Consult ECU ready")
                return True
        self.log("ERR", b"", "No 0x10 answer to Consult init")
        return False

    def _send_command(self, command: bytes) -> None:
        """Send command/parameter bytes, verify the echo, then send the go-ahead."""
        self.log("TX", command, "Consult command")
        self.transport.write(command)
        expected = bytearray()
        for index, byte in enumerate(command):
            is_command = command[0] != READ_REGISTER or index % 2 == 0
            expected.append((~byte) & 0xFF if is_command else byte)
        echo = bytearray()
        for want in expected:  # stop at the first wrong byte instead of waiting out the timeout
            got = self._read_exact(1, timeout=1.0)
            echo += got
            if got != bytes([want]):
                break
        if echo != bytes(expected):
            self._halt(quiet=True)
            raise ProtocolError(
                f"Consult echo mismatch: sent {command.hex(' ').upper()}, "
                f"got {echo.hex(' ').upper() or 'nothing'} (expected {bytes(expected).hex(' ').upper()})")
        self.transport.write(bytes([GO]))

    def _read_frame(self, timeout: float = 1.0) -> bytes:
        start = self._read_exact(1, timeout)
        if not start:
            raise ScanTimeout("no Consult frame")
        if start[0] != FRAME_START:
            raise MalformedFrameError(f"Consult frame starts with 0x{start[0]:02X}, expected 0xFF")
        length = self._read_exact(1, 0.3)
        if not length:
            raise MalformedFrameError("Consult frame without length byte")
        data = self._read_exact(length[0], 0.5)
        if len(data) != length[0]:
            raise MalformedFrameError("truncated Consult frame")
        self.log("RX", start + length + data, "Consult frame")
        return data

    def _halt(self, quiet: bool = False) -> None:
        self.transport.write(bytes([STOP]))
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            byte = self.transport.read(1, timeout=0.2)
            if byte == bytes([STOP_OK]):
                return
            if not byte and quiet:
                return
        if not quiet:
            raise ScanTimeout("Consult ECU did not acknowledge stop (0xCF)")

    def _request(self, command: bytes) -> bytes:
        self._send_command(command)
        try:
            return self._read_frame()
        finally:
            self._halt(quiet=True)

    def keep_alive(self) -> None:
        return None  # the ECU stays in diagnostic mode between commands

    # -- data ------------------------------------------------------------------
    def supported_registers(self) -> List[Register]:
        """Registers the ECU echoes correctly (OpenConsult's capability scan)."""
        if self._supported is None:
            supported = []
            for reg in REGISTERS:
                try:
                    self._send_command(bytes(b for a in reg.addresses for b in (READ_REGISTER, a)))
                    self._read_frame(timeout=0.6)
                    supported.append(reg)
                except (ProtocolError, ScanTimeout, MalformedFrameError):
                    pass
                finally:
                    self._halt(quiet=True)
            self._supported = supported
        return self._supported

    def read_live_pids(self) -> List[LivePid]:
        registers = self.supported_registers()
        if not registers:
            return []
        command = bytes(b for reg in registers for a in reg.addresses for b in (READ_REGISTER, a))
        data = self._request(command)
        expected = sum(len(reg.addresses) for reg in registers)
        if len(data) != expected:
            raise MalformedFrameError(f"Consult returned {len(data)} bytes for {expected} registers")
        out: List[LivePid] = []
        index = 0
        for reg in registers:
            raw = data[index:index + len(reg.addresses)]
            index += len(reg.addresses)
            value = float(reg.decode(int.from_bytes(raw, "big")))
            out.append(LivePid(pid=reg.addresses[0], name=reg.name,
                               value=f"{value:.2f}".rstrip("0").rstrip("."), unit=reg.unit,
                               raw_hex=raw.hex(" ").upper(), numeric_value=value, category=reg.category))
        return out

    def read_dtcs(self, status_filter: str = "all") -> List[DiagnosticTroubleCode]:
        data = self._request(bytes([SELF_DIAG]))
        if len(data) % 2:
            raise MalformedFrameError("Consult self-diagnosis frame has odd length")
        dtcs = []
        for index in range(0, len(data), 2):
            code, starts = data[index], data[index + 1]
            if code == NO_FAULT:
                continue
            dtcs.append(DiagnosticTroubleCode(
                code=f"NISSAN {code:02X}", raw_hex=f"{code:02X} {starts:02X}",
                status=f"Stored ({starts} starts since last seen)",
                description=FAULT_CODES.get(code, f"Nissan Consult code {code:02X}"),
                severity="Warning"))
        return dtcs

    def clear_dtcs(self) -> bool:
        raise UnsupportedOperationError(
            "Clearing Consult codes is not implemented: the clear command could not be "
            "verified from public sources")

    def read_ecu_id(self) -> List[EcuIdItem]:
        data = self._request(bytes([PART_NUMBER]))
        if len(data) < 22:
            raise MalformedFrameError(f"Consult part-number frame is {len(data)} bytes, expected 22")
        part = f"{data[2]:02X}{data[3]:02X} 23710-{data[19]:X}{data[20]:02X}{data[21]:02X}"
        return [EcuIdItem("ECU part number", part, data.hex())]

    def send_raw_hex(self, raw: bytes) -> bytes:
        try:
            return self._request(raw)
        except ScanTimeout:
            return b""
