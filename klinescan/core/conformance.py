"""
Golden-transcript conformance runner.

A transcript is an ordered list of (expected TX, [RX...]) exchanges plus one
operation.  It is intentionally hardware-independent so CI can validate the
protocol state machines against fixture bytes or a real vehicle.

Supported operation names: ``connect``, ``read_dtcs``, ``clear_dtcs``,
``read_live_pids``, ``read_ecu_id``, ``read_readiness``, ``read_freeze_frame``,
``send_raw`` (with ``payload_hex``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

from klinescan.core.errors import KLineScanError
from klinescan.core.models import EcuTarget, ProtocolType
from klinescan.protocols.base import BaseProtocol
from klinescan.protocols.isotp_obd import IsoTpObdProtocol
from klinescan.protocols.kwp1281 import Kwp1281Protocol
from klinescan.protocols.kwp2000 import Kwp2000Protocol
from klinescan.protocols.uds import UdsProtocol
from klinescan.transport.base import BaseTransport
from klinescan.transport.replay import ScriptedTransport

PROTOCOLS: Dict[str, Type[BaseProtocol]] = {
    "obd2": IsoTpObdProtocol,
    "isotp": IsoTpObdProtocol,
    "can_obd": IsoTpObdProtocol,
    "kwp1281": Kwp1281Protocol,
    "kwp2000": Kwp2000Protocol,
    "uds": UdsProtocol,
}


@dataclass
class TranscriptResult:
    transcript_id: str
    passed: bool
    transaction_ok: bool
    operation_result: Any = None
    error: str = ""
    error_class: str = ""
    expected_tx: List[str] = field(default_factory=list)
    actual_tx: List[str] = field(default_factory=list)
    mismatches: List[Tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.transcript_id,
            "passed": self.passed,
            "transaction_ok": self.transaction_ok,
            "error": self.error,
            "error_class": self.error_class,
            "expected_tx": self.expected_tx,
            "actual_tx": self.actual_tx,
            "mismatches": [
                {"expected": e, "actual": a} for e, a in self.mismatches
            ],
        }


def _hex(value: str) -> bytes:
    return bytes.fromhex(value.replace(" ", ""))


class Kwp1281EcuSimulator(BaseTransport):
    """Data-driven KWP1281 ECU for byte-handshake conformance transcripts.

    This simulator lives in the conformance tooling; it is not used by real
    transports. It implements the complement handshake and counters, then
    serves blocks from a JSON scenario.
    """

    name = "kwp1281_sim"

    def __init__(self, scenario: Dict[str, Any]):
        super().__init__()
        self._slow_init = _hex(str(scenario.get("slow_init", "55 01 8A")))
        self._initial_blocks = list(scenario.get("initial_blocks", []))
        self._responses = {
            str(key).upper(): value
            for key, value in scenario.get("responses", {}).items()
        }
        self._rx = []
        self._expected_complements = []
        self._tester_block = bytearray()
        self._tester_length = 0
        self._counter = 0
        self._awaiting_init_ack = False
        self.tx_frames: List[bytes] = []
        self.mismatches: List[Tuple[bytes, bytes]] = []
        self.unhandled: List[str] = []
        self.counter_errors: List[str] = []

    def open(self, port: str = "kwp1281-sim", baudrate: int = 10400) -> None:
        self.port = port
        self.actual_baud = baudrate
        self._mark_open()

    def close(self) -> None:
        self._mark_closed()

    def set_baudrate(self, baudrate: int) -> None:
        self.actual_baud = baudrate

    def flush(self) -> None:
        self._rx.clear()

    def slow_init_5baud(self, target_addr: int) -> bytes:
        self._awaiting_init_ack = True
        for block in self._initial_blocks:
            self._queue_spec(block)
        return self._slow_init

    def read(self, size: int, timeout: float = 0.5) -> bytes:
        out = bytearray()
        while self._rx and len(out) < size:
            value, needs_complement = self._rx.pop(0)
            out.append(value)
            if needs_complement:
                self._expected_complements.append((~value) & 0xFF)
        return bytes(out)

    def write(self, data: bytes) -> None:
        if not data:
            return
        value = data[0]
        self.tx_frames.append(bytes(data))
        if self._awaiting_init_ack:
            # Tester's complement of KW2.  The ECU does not answer it; it starts
            # sending its identification blocks (already queued).
            self._awaiting_init_ack = False
            expected = (~self._slow_init[2]) & 0xFF if len(self._slow_init) >= 3 else None
            if expected is not None and value != expected:
                self.mismatches.append((bytes([expected]), bytes([value])))
            return
        if self._expected_complements and value == self._expected_complements[0]:
            self._expected_complements.pop(0)
            return
        self._tester_block.append(value)
        if len(self._tester_block) == 1:
            self._tester_length = value
        is_final = len(self._tester_block) >= self._tester_length + 1
        if not is_final:
            self._rx.append(((~value) & 0xFF, False))
            return
        block = bytes(self._tester_block)
        self._tester_block.clear()
        self._tester_length = 0
        self._handle_tester_block(block)

    def _handle_tester_block(self, block: bytes) -> None:
        if len(block) < 3:
            self.unhandled.append(block.hex(" ").upper())
            return
        counter, title = block[1], block[2]
        if title != 0x09 and self._counter and counter != ((self._counter + 1) & 0xFF):
            self.counter_errors.append(
                f"title 0x{title:02X}: counter {counter:02X}, expected {((self._counter + 1) & 0xFF):02X}"
            )
        self._counter = counter
        payload = block[3:-1] if block.endswith(b"\x03") else block[3:]
        key_candidates = [
            f"{title:02X}{payload.hex().upper()}",
            f"{title:02X}:{payload.hex().upper()}",
            f"{title:02X}",
        ]
        specs = None
        for key in key_candidates:
            if key in self._responses:
                specs = self._responses[key]
                break
        if specs is None and title == 0x09:
            specs = [{"title": "09"}]
        if specs is None:
            self.unhandled.append(block.hex(" ").upper())
            return
        if isinstance(specs, dict):
            specs = [specs]
        for spec in specs:
            self._queue_spec(spec)

    def _queue_spec(self, spec: Dict[str, Any]) -> None:
        raw_title = spec.get("title", 0x09)
        title = int(raw_title, 16) if isinstance(raw_title, str) else int(raw_title)
        if "data_hex" in spec:
            data = _hex(str(spec["data_hex"]))
        else:
            data = str(spec.get("data_ascii", "")).encode("ascii")
        self._counter = (self._counter + 1) & 0xFF
        body = bytes([len(data) + 3, self._counter, title]) + data + b"\x03"
        self._rx.extend((byte, True) for byte in body[:-1])
        self._rx.append((body[-1], False))

    @property
    def complete(self) -> bool:
        return not self.unhandled and not self.counter_errors and not self.mismatches


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


def _make_target(raw: Dict[str, Any]) -> EcuTarget:
    protocol_value = raw.get("protocol", ProtocolType.ISO9141.value)
    try:
        protocol = ProtocolType(protocol_value)
    except ValueError:
        by_name = {member.name: member for member in ProtocolType}
        protocol = by_name.get(str(protocol_value).upper(), ProtocolType.ISO9141)

    return EcuTarget(
        name=raw.get("name", "ECU"),
        protocol=protocol,
        baudrate=_as_int(raw.get("baudrate"), 10400),
        slow_init_addr=_as_int(raw.get("slow_init_addr"), 0x33),
        tester_addr=_as_int(raw.get("tester_addr"), 0xF1),
        ecu_addr=_as_int(raw.get("ecu_addr"), 0x11),
        explicit_length=bool(raw.get("explicit_length", False)),
        use_obd_header=bool(raw.get("use_obd_header", False)),
        can_tx_id=_as_int(raw.get("can_tx_id"), 0x7DF),
        can_rx_id=_as_int(raw.get("can_rx_id"), 0x7E8),
        pin=_as_int(raw.get("pin"), 7),
    )


def load_transcript(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_transcript(data: Dict[str, Any]) -> TranscriptResult:
    transcript_id = data.get("id", "unnamed")
    protocol_name = str(data.get("protocol", "")).lower()
    protocol_cls = PROTOCOLS.get(protocol_name)
    if not protocol_cls:
        return TranscriptResult(
            transcript_id, False, False, error=f"unknown protocol '{protocol_name}'"
        )

    exchanges = [
        (_hex(item["tx"]), [_hex(rx) for rx in item.get("rx", [])])
        for item in data.get("exchanges", [])
    ]
    initial_rx = [_hex(rx) for rx in data.get("initial_rx", [])]
    simulator = data.get("simulator")
    if simulator:
        transport = Kwp1281EcuSimulator(simulator)
    else:
        transport = ScriptedTransport(exchanges, initial_rx=initial_rx)
    target = _make_target(data.get("target", {}))
    protocol = protocol_cls(transport, target)

    operation = data.get("operation", {})
    op_name = str(operation.get("name", "connect"))
    operation_result: Any = None
    error_text = ""
    error_class = ""
    try:
        transport.open()
        if op_name == "connect":
            operation_result = protocol.connect()
        elif op_name == "read_dtcs":
            operation_result = protocol.read_dtcs()
        elif op_name == "clear_dtcs":
            operation_result = protocol.clear_dtcs()
        elif op_name == "read_live_pids":
            operation_result = protocol.read_live_pids()
        elif op_name == "read_ecu_id":
            operation_result = protocol.read_ecu_id()
        elif op_name == "read_readiness":
            operation_result = protocol.read_readiness()
        elif op_name == "read_freeze_frame":
            operation_result = protocol.read_freeze_frame()
        elif op_name == "send_raw":
            operation_result = protocol.send_raw_hex(
                _hex(str(operation.get("payload_hex", "")))
            )
        else:
            error_text = f"unknown operation '{op_name}'"
            error_class = "unsupported"
    except KLineScanError as exc:
        error_text = str(exc)
        error_class = exc.error_class.value
    except Exception as exc:  # pragma: no cover - defensive
        error_text = str(exc)
        error_class = "internal"
    finally:
        transport.close()

    expected_tx = [tx.hex(" ").upper() for tx, _ in exchanges]
    actual_tx = [frame.hex(" ").upper() for frame in transport.tx_frames]
    mismatches = [(e.hex(" ").upper(), a.hex(" ").upper()) for e, a in transport.mismatches]
    transaction_ok = transport.complete and not mismatches

    operation_ok = True
    if "expect" in operation:
        operation_ok = operation_result == operation["expect"]
    elif not error_text:
        operation_ok = operation_result is not None

    return TranscriptResult(
        transcript_id=transcript_id,
        passed=transaction_ok and operation_ok and not error_text,
        transaction_ok=transaction_ok,
        operation_result=operation_result,
        error=error_text,
        error_class=error_class,
        expected_tx=expected_tx,
        actual_tx=actual_tx,
        mismatches=mismatches,
    )


def run_transcript_file(path: str) -> TranscriptResult:
    return run_transcript(load_transcript(path))


def discover_transcripts(directory: str) -> List[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.lower().endswith(".json")
    )


def run_directory(directory: str) -> List[TranscriptResult]:
    return [run_transcript_file(path) for path in discover_transcripts(directory)]
