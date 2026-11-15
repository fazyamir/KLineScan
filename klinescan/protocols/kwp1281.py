"""
VAG KWP1281 protocol.

KWP1281 is a byte-handshake protocol, not a packet protocol:

* length = number of following bytes (counter + title + data + end)
* counter increments for every block from either side
* every byte except the final 0x03 is answered with its bitwise complement
* there is no checksum

This implementation drives the handshake byte by byte, tracks the shared block
counter, drains the identification blocks sent after the 5-baud wake-up, and
decodes only data that was actually received.

Init and block layouts follow the open-source KLineKWP1281Lib
(https://github.com/domnulvlad/KLineKWP1281Lib) and blafusel.de: after the
keybytes ``55 01 8A`` the tester waits ~40 ms (W4 25-50 ms) and sends the
complement of KW2; the ECU then starts its first identification block directly
(it does *not* answer the complement).  The ECU drops the session after ~500 ms
without a block, so keep-alive ACKs must be sent faster than that.
"""

import time

from dataclasses import dataclass
from typing import Dict, List, Optional

from klinescan.core.errors import (
    MalformedFrameError,
    ProtocolError,
    ScanTimeout,
    UnsupportedOperationError,
)
from klinescan.core.models import DiagnosticTroubleCode, EcuIdItem, LivePid
from klinescan.protocols.base import BaseProtocol

TITLE_ACK = 0x09
TITLE_NAK = 0x0A
TITLE_READ_IDENT = 0x00
TITLE_OUTPUT_TEST = 0x04
TITLE_CLEAR_FAULTS = 0x05
TITLE_DISCONNECT = 0x06
TITLE_READ_FAULTS = 0x07
TITLE_ADAPT_READ = 0x21
TITLE_ADAPT_TEST = 0x22
TITLE_BASIC_SETTING = 0x28
TITLE_GROUP_READ = 0x29
TITLE_ADAPT_SAVE = 0x2A
TITLE_LOGIN = 0x2B
TITLE_ADAPT_RESPONSE = 0xE6
TITLE_ASCII = 0xF6
TITLE_BASIC_SETTING_RESPONSE = 0xF4
TITLE_OUTPUT_TEST_RESPONSE = 0xF5
TITLE_FAULT_RESPONSE = 0xFC
TITLE_GROUP_RESPONSE = 0xE7
TITLE_NAK_RESPONSE = 0x0A

KW1281_KEYWORD = 1281            # ((KW2 & 0x7F) << 7) | (KW1 & 0x7F)
INIT_COMPLEMENT_DELAY_S = 0.040  # inside W4 (25..50 ms)
KEEPALIVE_MS = 300               # ECU drops the link after ~500 ms of silence
DEFAULT_GROUPS = (1, 2, 3)

NO_FAULT_CODES = (0xFF, 0xFF, 0x88)


@dataclass
class Kwp1281Block:
    length: int
    counter: int
    title: int
    data: bytes


class Kwp1281Protocol(BaseProtocol):
    def __init__(self, transport, target, frame_cb=None):
        super().__init__(transport, target, frame_cb)
        self._counter = 1
        self._ident_blocks: List[bytes] = []
        self._session_open = False
        self._local_echo = False
        self._rx_pushback: List[int] = []
        self.keyword = 0
        self.groups: List[int] = list(getattr(target, "measuring_groups", None) or DEFAULT_GROUPS)
        self._output_test_running = False

    keep_alive_interval_ms = KEEPALIVE_MS

    # -- byte handshake ----------------------------------------------------
    def _read_byte(self, with_complement: bool = True, timeout: float = 0.6) -> int:
        if self._rx_pushback:
            value = self._rx_pushback.pop(0)
        else:
            raw = self.transport.read(1, timeout=timeout)
            if not raw:
                raise ScanTimeout("KWP1281 byte timeout")
            value = raw[0]

        if with_complement:
            self._write_byte((~value) & 0xFF, with_complement=False)
        return value

    def _write_byte(self, value: int, with_complement: bool = True, timeout: float = 0.6) -> None:
        self.transport.write(bytes([value]))
        if not with_complement:
            if self._local_echo:
                echo = self.transport.read(1, timeout=0.05)
                if echo and echo[0] != value:
                    self._rx_pushback.append(echo[0])
            return

        raw = self.transport.read(1, timeout=timeout)
        if not raw:
            raise ScanTimeout("KWP1281 missing byte complement")
        answer = raw[0]
        if answer == value:
            self._local_echo = True
            raw = self.transport.read(1, timeout=timeout)
            if not raw:
                raise ScanTimeout("KWP1281 missing byte complement after echo")
            answer = raw[0]
        expected = (~value) & 0xFF
        if answer != expected:
            self.log(
                "WARN",
                bytes([answer]),
                f"KWP1281 complement mismatch for 0x{value:02X} (expected 0x{expected:02X})",
            )

    def _send_block(self, title: int, data: bytes = b"", timeout: float = 0.6) -> None:
        if len(data) > 0xFC:
            raise MalformedFrameError("KWP1281 block payload too long")
        self._counter = (self._counter + 1) & 0xFF
        length = len(data) + 3
        body = bytes([length, self._counter, title]) + data + b"\x03"
        self.log("TX", body, f"KWP1281 block title 0x{title:02X}")
        for byte in body[:-1]:
            self._write_byte(byte, with_complement=True, timeout=timeout)
        self._write_byte(body[-1], with_complement=False)

    def _receive_block(self, timeout: float = 0.8) -> Kwp1281Block:
        length = self._read_byte(with_complement=True, timeout=timeout)
        if length < 3:
            raise MalformedFrameError(f"KWP1281 invalid block length {length}")
        counter = self._read_byte(with_complement=True, timeout=timeout)
        self._counter = counter
        title = self._read_byte(with_complement=True, timeout=timeout)
        data = bytearray()
        for _ in range(length - 3):
            data.append(self._read_byte(with_complement=True, timeout=timeout))
        end = self._read_byte(with_complement=False, timeout=timeout)
        if end != 0x03:
            raise MalformedFrameError(
                f"KWP1281 block ended with 0x{end:02X}, expected 0x03"
            )
        block = Kwp1281Block(length, counter, title, bytes(data))
        self.log(
            "RX",
            bytes([length, counter, title]) + bytes(data) + b"\x03",
            f"KWP1281 block title 0x{title:02X}",
        )
        return block

    def _ack(self) -> None:
        self._send_block(TITLE_ACK)

    # -- connection --------------------------------------------------------
    def connect(self) -> bool:
        address = self.target.slow_init_addr or 0x01
        self.log("INFO", bytes([address]), f"KWP1281 5-baud slow init (0x{address:02X})")
        self._counter = 0
        self._rx_pushback.clear()
        sync = self.transport.slow_init_5baud(address)
        if len(sync) < 3 or sync[0] != 0x55:
            self.log("ERR", sync, "KWP1281 slow init failed (expected 55 KW1 KW2)")
            return False

        kw1, kw2 = sync[1], sync[2]
        self.keyword = ((kw2 & 0x7F) << 7) | (kw1 & 0x7F)
        self.log("RX", sync, f"Keybytes: {kw1:02X} {kw2:02X} (protocol {self.keyword})")
        if self.keyword != KW1281_KEYWORD:
            self.log("WARN", sync[1:], f"Keywords give protocol {self.keyword}, not 1281")
        time.sleep(INIT_COMPLEMENT_DELAY_S)
        # The complement of KW2 is not answered: the ECU starts its first block.
        self._write_byte((~kw2) & 0xFF, with_complement=False)

        self._ident_blocks.clear()
        saw_ack = False
        try:
            for _ in range(24):
                block = self._receive_block(timeout=2.0 if not self._ident_blocks else 0.9)
                if block.title == TITLE_ACK:
                    saw_ack = True
                    break
                if block.title == TITLE_ASCII:
                    self._ident_blocks.append(block.data)
                self._ack()
        except ScanTimeout:
            self.log("WARN", b"", "No identification blocks after KWP1281 init")

        self._session_open = saw_ack or bool(self._ident_blocks)
        return self._session_open

    def disconnect(self) -> None:
        """End the session (title 0x06); some modules answer with a refuse block."""
        if not self._session_open:
            return
        self._session_open = False
        try:
            self._send_block(TITLE_DISCONNECT)
        except Exception:
            pass

    def _ensure_session(self) -> None:
        if not self._session_open:
            if not self.connect():
                raise ProtocolError("KWP1281 session is not established")

    def keep_alive(self) -> None:
        self._ensure_session()
        self._ack()
        block = self._receive_block()
        if block.title not in (TITLE_ACK, TITLE_NAK_RESPONSE):
            self.log("WARN", bytes([block.title]), "Unexpected keep-alive response")

    # -- diagnostics -------------------------------------------------------
    def read_dtcs(self, status_filter: str = "all") -> List[DiagnosticTroubleCode]:
        self._ensure_session()
        self._send_block(TITLE_READ_FAULTS)
        dtcs: List[DiagnosticTroubleCode] = []
        while True:
            block = self._receive_block()
            if block.title == TITLE_ACK:
                break
            if block.title != TITLE_FAULT_RESPONSE:
                raise ProtocolError(f"Unexpected KWP1281 block 0x{block.title:02X}")
            if len(block.data) % 3 != 0:
                raise MalformedFrameError("KWP1281 fault block is not a multiple of 3")
            for index in range(0, len(block.data), 3):
                high, low, status = block.data[index : index + 3]
                if (high, low, status) == NO_FAULT_CODES:
                    continue
                raw_code = (high << 8) | low
                obd = 0x4000 <= raw_code <= 0x7FFF  # OBD P-code carried in VAG format
                code = f"VAG {raw_code:05d}"
                description = f"VAG fault code {raw_code:05d}"
                if obd:
                    from klinescan.data.dtc_db import lookup_dtc
                    from klinescan.protocols.kwp2000 import decode_dtc_pair

                    pcode = decode_dtc_pair(high & 0x3F, low)
                    description = f"{pcode}: {lookup_dtc(pcode)[0]}"
                dtcs.append(
                    DiagnosticTroubleCode(
                        code=code,
                        raw_hex=f"{high:02X} {low:02X} {status:02X}",
                        status=self._fault_status(status),
                        description=description,
                        severity="Warning",
                    )
                )
            self._ack()
        return dtcs

    @staticmethod
    def _fault_status(status: int) -> str:
        """Elaboration code (bits 0-6) and the intermittent flag (bit 7)."""
        text = f"Elaboration {status & 0x7F:02d}"
        if status & 0x80:
            text += " (intermittent)"
        return text

    def clear_dtcs(self) -> bool:
        self._ensure_session()
        self._send_block(TITLE_CLEAR_FAULTS)
        while True:
            block = self._receive_block()
            if block.title == TITLE_ACK:
                return True
            if block.title == TITLE_NAK_RESPONSE:
                return False
            self._ack()

    def read_live_pids(self) -> List[LivePid]:
        pids: List[LivePid] = []
        for group in self.groups:
            pids.extend(self.read_group(group))
        return pids

    def read_group(self, group: int) -> List[LivePid]:
        """Read one measuring block (any group 1-255)."""
        self._ensure_session()
        self._send_block(TITLE_GROUP_READ, bytes([group & 0xFF]))
        block = self._receive_block()
        if block.title == TITLE_NAK_RESPONSE:
            return []  # group not available on this module
        if block.title == TITLE_ACK:
            return []  # empty group
        if block.title != TITLE_GROUP_RESPONSE:
            self._ack()
            self._expect_ack()
            raise ProtocolError(
                f"Group {group} answered with block 0x{block.title:02X} (header+body groups are not decoded)"
            )
        pids = self._decode_measurements(group, block.data)
        self._ack()
        self._expect_ack()
        return pids

    def _expect_ack(self) -> None:
        ack = self._receive_block()
        if ack.title != TITLE_ACK:
            raise ProtocolError(f"Unexpected KWP1281 ACK response 0x{ack.title:02X}")

    def _decode_measurements(self, group: int, data: bytes) -> List[LivePid]:
        from klinescan.core import kwp1281_formulas as F

        pids: List[LivePid] = []
        index = 0
        field = 0
        while index + 3 <= len(data):
            formula, nw, mw = data[index], data[index + 1], data[index + 2]
            field += 1
            if formula in F.VARIABLE_LENGTH:
                rest = data[index:]
                pids.append(LivePid(pid=(group << 8) | field, name=f"Group {group:03d}.{field} Raw",
                                    value=rest.hex(" ").upper(), unit="", raw_hex=rest.hex(" ").upper()))
                break
            name, unit, value, numeric = F.decode(formula, nw, mw)
            pids.append(LivePid(
                pid=(group << 8) | field, name=f"Group {group:03d}.{field} {name}",
                value=value, unit=unit, raw_hex=f"{nw:02X} {mw:02X}", numeric_value=numeric,
                category="Measuring blocks"))
            index += 3
        return pids

    @staticmethod
    def _decode_measurement(value_type: int, a: int, b: int):
        from klinescan.core import kwp1281_formulas as F

        return F.decode(value_type, a, b)

    # -- login / adaptation / basic settings / output tests ---------------------
    def _request(self, title: int, data: bytes = b"") -> Kwp1281Block:
        self._ensure_session()
        self._send_block(title, data)
        return self._receive_block()

    def login(self, code: int, workshop_code: int = 0) -> bool:
        """Login (0x2B): ``code`` 0..65535, workshop code (WSC) 0..131071."""
        block = self._request(TITLE_LOGIN, bytes([
            (code >> 8) & 0xFF, code & 0xFF, (workshop_code >> 16) & 0x01,
            (workshop_code >> 8) & 0xFF, workshop_code & 0xFF]))
        return block.title == TITLE_ACK

    def _adaptation_value(self, block: Kwp1281Block) -> Optional[int]:
        if block.title == TITLE_ADAPT_RESPONSE and len(block.data) >= 3:
            value = (block.data[1] << 8) | block.data[2]
            self._ack()
            self._expect_ack()
            return value
        return None

    def read_adaptation(self, channel: int) -> Optional[int]:
        return self._adaptation_value(self._request(TITLE_ADAPT_READ, bytes([channel & 0xFF])))

    def test_adaptation(self, channel: int, value: int) -> bool:
        block = self._request(TITLE_ADAPT_TEST, bytes([channel & 0xFF, (value >> 8) & 0xFF, value & 0xFF]))
        return self._adaptation_value(block) == value

    def save_adaptation(self, channel: int, value: int, workshop_code: int = 0) -> bool:
        block = self._request(TITLE_ADAPT_SAVE, bytes([
            channel & 0xFF, (value >> 8) & 0xFF, value & 0xFF,
            (workshop_code >> 16) & 0x01, (workshop_code >> 8) & 0xFF, workshop_code & 0xFF]))
        return self._adaptation_value(block) is not None

    def basic_setting(self, group: int) -> List[LivePid]:
        """Basic settings (0x28) for ``group``; returns the measured values the ECU reports."""
        block = self._request(TITLE_BASIC_SETTING, bytes([group & 0xFF]))
        if block.title in (TITLE_ACK, TITLE_NAK_RESPONSE):
            return []
        pids = self._decode_measurements(group, block.data) if block.title in (
            TITLE_BASIC_SETTING_RESPONSE, TITLE_GROUP_RESPONSE) else []
        self._ack()
        self._expect_ack()
        return pids

    def output_test_step(self) -> Optional[int]:
        """Advance the output-test sequence (0x04).  Returns the test ID, None at the end."""
        block = self._request(TITLE_OUTPUT_TEST, b"\x00")
        if block.title == TITLE_OUTPUT_TEST_RESPONSE and len(block.data) >= 2:
            test_id = (block.data[0] << 8) | block.data[1]
            self._ack()
            self._expect_ack()
            return test_id
        if block.title == TITLE_NAK_RESPONSE:
            raise UnsupportedOperationError("This module does not support output tests")
        return None  # ACK: sequence finished

    def read_ecu_id(self) -> List[EcuIdItem]:
        self._ensure_session()
        if not self._ident_blocks:
            self._send_block(TITLE_READ_IDENT)
            while True:
                block = self._receive_block()
                if block.title == TITLE_ACK:
                    break
                if block.title == TITLE_ASCII:
                    self._ident_blocks.append(block.data)
                self._ack()

        items: List[EcuIdItem] = []
        for index, data in enumerate(self._ident_blocks, start=1):
            text = data.decode("ascii", errors="replace").strip("\x00 ")
            if text:
                items.append(
                    EcuIdItem(f"KWP1281 Identification {index}", text, data.hex())
                )
        return items

    def read_freeze_frame(self):
        raise UnsupportedOperationError("KWP1281 has no freeze-frame service")

    def read_readiness(self):
        return []

    def run_actuator_test(self, actuator, start: bool = True) -> bool:
        """KWP1281 output tests run in a fixed sequence chosen by the ECU.

        Starting steps to the next element (``payload_start`` may name a raw block
        ``title data...`` instead); stopping ends the session's test sequence.
        """
        if not start:
            self._output_test_running = False
            return True
        if actuator.payload_start:
            block = self._request(actuator.payload_start[0], actuator.payload_start[1:])
            ok = block.title not in (TITLE_NAK_RESPONSE,)
            if block.title != TITLE_ACK:
                self._ack()
                self._expect_ack()
            return ok
        test_id = self.output_test_step()
        self._output_test_running = test_id is not None
        if test_id is not None:
            self.log("INFO", test_id.to_bytes(2, "big"), f"Output test element {test_id:05d}")
        return test_id is not None

    def run_special_function(self, func) -> bool:
        """Pack-defined function: ``command`` is a raw block ``title data...``.

        e.g. ``28 01`` = basic settings group 1, ``2B 2E 5F 00 00 00`` = login 11871.
        """
        if not func.command:
            raise UnsupportedOperationError(f"No KWP1281 block defined for {func.name}")
        block = self._request(func.command[0], func.command[1:])
        if block.title == TITLE_NAK_RESPONSE:
            return False
        if block.title != TITLE_ACK:
            self._ack()
            self._expect_ack()
        return True

    def send_raw_hex(self, raw: bytes) -> bytes:
        self._ensure_session()
        self.log("TX", raw, "KWP1281 raw send")
        framed = raw if raw.endswith(b"\x03") else raw + b"\x03"
        for byte in framed[:-1]:
            self._write_byte(byte, with_complement=True)
        self._write_byte(framed[-1], with_complement=False)
        try:
            block = self._receive_block()
        except ScanTimeout:
            return b""
        return bytes([block.length, block.counter, block.title]) + block.data + b"\x03"
