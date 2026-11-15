"""Regression tests for the production-readiness fixes (no hardware needed)."""

from __future__ import annotations

import json
import os
from collections import deque

import pytest

from klinescan.core.errors import (
    MalformedFrameError,
    OperationCancelled,
    ProtocolError,
    SafetyGateError,
    ScanTimeout,
)
from klinescan.core.models import ActuatorDefinition, EcuTarget, ProtocolType, SpecialFunctionDefinition
from klinescan.protocols.isotp import IsoTpChannel, IsoTpConfig
from klinescan.protocols.kwp2000 import Kwp2000Protocol
from klinescan.transport.base import BaseTransport, CanFrame
from klinescan.transport.kline_echo import EchoCanceller


# ------------------------------------------------------------------ echo canceller
def _reader(stream):
    queue = deque(stream)
    return lambda timeout: bytes([queue.popleft()]) if queue else b""


def test_echo_is_matched_as_prefix_and_ecu_bytes_are_kept():
    echo = EchoCanceller("auto")
    # echo of 0xCC, then the ECU's own byte (also 0xCC in KWP1281 worst case) must survive
    assert echo.consume(b"\xcc", _reader([0xCC, 0x33]), 10400) == []
    assert echo.detected is True


def test_echo_auto_detects_echo_cancelling_cable():
    echo = EchoCanceller("auto")
    leftovers = echo.consume(b"\x81\x11\xf1", _reader([0x83, 0xF1]), 10400)
    assert leftovers == [0x83]          # ECU byte handed back, not swallowed
    assert echo.detected is False       # no echo -> later writes do not wait
    assert echo.consume(b"\x01", _reader([0x01]), 10400) == []
    assert echo.expect_echo is False


def test_echo_off_mode_reads_nothing():
    calls = []
    EchoCanceller("off").consume(b"\x55", lambda t: calls.append(t) or b"\x55", 10400)
    assert calls == []


# ------------------------------------------------------------------ K-line checksums
class ByteStream(BaseTransport):
    def __init__(self, rx: bytes):
        super().__init__()
        self.rx = deque(rx)
        self.tx = []

    def open(self, port, baudrate):
        pass

    def close(self):
        pass

    def set_baudrate(self, baudrate):
        pass

    def flush(self):
        pass

    def write(self, data):
        self.tx.append(bytes(data))

    def read(self, size, timeout=0.5):
        out = bytearray()
        while self.rx and len(out) < size:
            out.append(self.rx.popleft())
        return bytes(out)


def _kwp(rx: bytes, **kw) -> Kwp2000Protocol:
    target = EcuTarget("ECU", ProtocolType.KWP2000_FAST, ecu_addr=0x11, tester_addr=0xF1, **kw)
    return Kwp2000Protocol(ByteStream(rx), target)


def test_kwp2000_rejects_bad_checksum():
    with pytest.raises(MalformedFrameError):
        _kwp(bytes.fromhex("83 F1 11 C1 8A 00 00"))._read_frame()
    assert _kwp(bytes.fromhex("83 F1 11 C1 8A 00 D0"))._read_frame()[-1] == 0xD0


def test_iso9141_rejects_bad_checksum_and_splits_good_frames():
    good = bytes.fromhex("48 6B 12 41 05 64")
    good += bytes([sum(good) & 0xFF])
    proto = _kwp(good + good, use_obd_header=True)
    assert proto._read_frame() == good
    assert proto._read_frame() == good  # second frame kept for the next read
    with pytest.raises(MalformedFrameError):
        _kwp(good[:-1] + b"\x00", use_obd_header=True)._read_frame()


def test_kwp2000_waits_through_response_pending():
    pending = bytes.fromhex("83 F1 11 7F 31 78")
    pending += bytes([sum(pending) & 0xFF])
    final = bytes.fromhex("82 F1 11 71 01")
    final += bytes([sum(final) & 0xFF])
    proto = _kwp(pending + final)
    assert proto._extract_payload(proto._exchange(b"\x31\x01")) == b"\x71\x01"


def test_kwp2000_pack_defined_live_params():
    from klinescan.core.models import LiveParamDefinition

    reply = bytes.fromhex("85 F1 11 61 01 0F A0 5A")
    reply += bytes([sum(reply) & 0xFF])
    proto = _kwp(reply, live_params=[
        LiveParamDefinition("Engine speed", b"\x21\x01", offset=2, length=2, formula="(A*256+B)/4", unit="rpm"),
        LiveParamDefinition("Coolant", b"\x21\x01", offset=4, formula="A-40", unit="°C"),
    ])
    pids = proto.read_live_pids()
    assert [(p.name, p.numeric_value) for p in pids] == [("Engine speed", 1000.0), ("Coolant", 50.0)]
    assert len(proto.transport.tx) == 1  # one request serves both values


# ------------------------------------------------------------------ ISO-TP
class CanBus(BaseTransport):
    """Raw-frame transport; ``script`` maps a sent frame's data to frames to deliver."""

    def __init__(self, script):
        super().__init__()
        self.script = script
        self.inbox = deque()
        self.ids = None

    def open(self, port, baudrate):
        pass

    def close(self):
        pass

    def set_baudrate(self, baudrate):
        pass

    def flush(self):
        pass

    def write(self, data):
        pass

    def read(self, size, timeout=0.5):
        return b""

    def set_can_ids(self, tx_id, rx_id, extended=False):
        self.ids = (tx_id, rx_id, extended)

    def send_frame(self, frame):
        self.inbox.extend(self.script(frame))

    def recv_frame(self, timeout=0.5):
        return self.inbox.popleft() if self.inbox else None


def test_isotp_programs_ids_and_ignores_other_ecus():
    def script(frame):
        return [CanFrame(0x7E9, bytes([0x02, 0x41, 0x00] + [0] * 5)),        # other ECU
                CanFrame(0x7E8, bytes([0x06, 0x41, 0x00, 1, 2, 3, 4, 0]))]
    bus = CanBus(script)
    channel = IsoTpChannel(bus, EcuTarget("ECM", ProtocolType.CAN_OBD, can_tx_id=0x7E0, can_rx_id=0x7E8))
    assert bus.ids == (0x7E0, 0x7E8, False)
    assert channel.send(b"\x01\x00") == bytes([0x41, 0x00, 1, 2, 3, 4])


def test_isotp_responder_lock_requires_plausible_answer():
    def script(frame):
        return [CanFrame(0x7DF, frame.data),                                 # our own echo
                CanFrame(0x7EA, bytes([0x03, 0x7E, 0x00, 0, 0, 0, 0, 0])),  # unrelated reply
                CanFrame(0x7EC, bytes([0x02, 0x50, 0x03, 0, 0, 0, 0, 0]))]
    bus = CanBus(script)
    channel = IsoTpChannel(bus, EcuTarget("BCM", ProtocolType.UDS_CAN, can_tx_id=0x7DF, can_rx_id=0))
    assert channel.send(b"\x10\x03") == b"\x50\x03"
    assert channel.rx_id == 0x7EC and bus.ids[1] == 0x7EC


def test_isotp_flow_control_wait_is_capped():
    def script(frame):
        if frame.data[0] >> 4 == 1:  # first frame -> endless FC.WAIT
            return [CanFrame(0x7E8, bytes([0x31, 0, 0, 0, 0, 0, 0, 0]))] * 20
        return []
    channel = IsoTpChannel(CanBus(script), EcuTarget("ECM", ProtocolType.UDS_CAN, can_tx_id=0x7E0),
                           IsoTpConfig(max_wait_frames=5))
    with pytest.raises(ScanTimeout, match="WAIT"):
        channel.send(bytes(range(20)))


def test_isotp_response_pending_then_answer():
    def script(frame):
        return [CanFrame(0x7E8, bytes([0x03, 0x7F, 0x31, 0x78, 0, 0, 0, 0])),
                CanFrame(0x7E8, bytes([0x04, 0x71, 0x01, 0x02, 0x03, 0, 0, 0]))]
    channel = IsoTpChannel(CanBus(script), EcuTarget("ECM", ProtocolType.UDS_CAN, can_tx_id=0x7E0))
    assert channel.send(b"\x31\x01\x02\x03") == b"\x71\x01\x02\x03"


def test_cancellation_interrupts_waiting():
    bus = CanBus(lambda f: [])
    channel = IsoTpChannel(bus, EcuTarget("ECM", ProtocolType.UDS_CAN, can_tx_id=0x7E0))
    bus.cancel()
    with pytest.raises(OperationCancelled):
        channel.send(b"\x3e\x00", timeout=5)
    bus.reset_cancel()
    with pytest.raises(ScanTimeout):
        channel.send(b"\x3e\x00", timeout=0.05)


# ------------------------------------------------------------------ J2534 framing
def test_j2534_can_message_layout_and_filter():
    from klinescan.transport import j2534 as J

    class FakeDll:
        def __init__(self):
            self.calls = []

        def PassThruIoctl(self, *a):
            self.calls.append(("ioctl", a[1]))
            return 0

        def PassThruStartMsgFilter(self, channel, ftype, mask, pattern, fc, fid):
            m, p = mask._obj, pattern._obj
            self.calls.append(("filter", ftype, bytes(m.Data[:4]), bytes(p.Data[:4])))
            return 0

    t = J.J2534Transport()
    t._dll, t._is_open, t._protocol = FakeDll(), True, J.J2534_CAN
    t.set_can_ids(0x7E0, 0x7E8)
    assert ("filter", J.PASS_FILTER, bytes.fromhex("000007FF"), bytes.fromhex("000007E8")) in t._dll.calls
    msg = t._can_msg(0x7E0, b"\x02\x01\x00")
    assert bytes(msg.Data[:msg.DataSize]) == bytes.fromhex("000007E0 020100")


# ------------------------------------------------------------------ safety gate
def test_safety_gate_rules():
    from klinescan.core.safety import require_write_allowed, write_block_reason

    curated = EcuTarget("ECM", ProtocolType.UDS_CAN, confidence="curated")
    imported = EcuTarget("ECM", ProtocolType.UDS_CAN, confidence="imported")
    act = ActuatorDefinition("fan", "Fan", payload_start=b"\x2f\x01")
    assert write_block_reason(curated, "actuator", definition=act) == ""
    assert "imported" in write_block_reason(imported, "actuator", definition=act)
    assert "replay" in write_block_reason(curated, "clear_dtcs", replaying=True)
    assert "manual procedure" in write_block_reason(
        curated, "special_function", definition=SpecialFunctionDefinition("s", "SRI"))
    with pytest.raises(SafetyGateError):
        require_write_allowed(imported, "clear_dtcs")
    assert write_block_reason(imported, "read_dtcs") == ""  # reads are never gated


def test_service_enforces_gate_below_ui():
    from klinescan.service import KLineScanService

    service = KLineScanService()
    target = EcuTarget("Hyundai ECM", ProtocolType.ISO9141, slow_init_addr=0x33, use_obd_header=True,
                       confidence="imported")
    service.connect("fake", "MOCK", target)
    try:
        with pytest.raises(SafetyGateError):
            service.clear_dtcs()
        with pytest.raises(SafetyGateError):
            service.run_actuator(ActuatorDefinition("x", "X", payload_start=b"\x30\x01"))
        assert service.read_dtcs()  # reading still works
    finally:
        service.disconnect()


# ------------------------------------------------------------------ packs
def test_pack_validation_rejects_unknown_protocol_and_bad_hex(tmp_path):
    from klinescan.core.pack_manager import PackManager

    bad = {"pack_id": "bad", "name": "Bad", "version": "1", "vehicles": [{"label": "Car", "targets": [
        {"name": "ECU", "protocol": "ISO 9141-X"},
        {"name": "ABS", "protocol": "KWP2000_FAST", "actuators": [{"payload_start": "zz"}]}]}]}
    good = {"pack_id": "good", "name": "Good", "version": "1", "vehicles": [{"label": "Car", "targets": [
        {"name": "ECU", "protocol": "KWP2000_FAST", "can_tx_id": "0x7E0", "five_baud_parity": "none",
         "live_params": [{"name": "RPM", "request": "21 01", "offset": 2, "length": 2,
                          "formula": "(A*256+B)/4", "unit": "rpm"}],
         "measuring_groups": [1, 7], "security_level": 1}]}]}
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    (tmp_path / "good.json").write_text(json.dumps(good))
    (tmp_path / "broken.json").write_text("{not json")
    manager = PackManager(str(tmp_path))
    assert set(manager.packs) == {"good"}
    text = "\n".join(manager.errors)
    assert "unknown protocol 'ISO 9141-X'" in text and "not hex" in text and "broken.json" in text
    target = manager.packs["good"].vehicles[0].targets[0]
    assert target.can_tx_id == 0x7E0 and target.five_baud_parity == "none"
    assert target.live_params[0].request == b"\x21\x01" and target.measuring_groups == [1, 7]


# ------------------------------------------------------------------ security access
def test_security_access_flow_and_refusal():
    from klinescan.core.errors import NegativeResponseError
    from klinescan.core.security import security_access

    sent = []

    def ecu(payload):
        sent.append(payload)
        if payload == b"\x27\x01":
            return b"\x67\x01\x12\x34"
        if payload[:2] == b"\x27\x02":
            if payload[2:] != b"\xed\xcb":
                raise NegativeResponseError(0x27, 0x35)
            return b"\x67\x02"
        raise AssertionError(payload)

    assert security_access(ecu, 1, lambda seed, level: bytes(b ^ 0xFF for b in seed))
    assert sent[-1] == b"\x27\x02\xed\xcb"
    with pytest.raises(ProtocolError, match="invalid key"):
        security_access(ecu, 1, lambda seed, level: b"\x00\x00")
    assert security_access(lambda p: b"\x67\x01\x00\x00", 1, lambda s, l: b"")  # already unlocked


def test_unlock_requires_registered_algorithm():
    from klinescan.core.errors import ConfigurationError
    from klinescan.protocols.uds import UdsProtocol

    proto = UdsProtocol(CanBus(lambda f: []), EcuTarget("ECM", ProtocolType.UDS_CAN, security_level=1,
                                                         security_algorithm="nope"))
    with pytest.raises(ConfigurationError, match="register"):
        proto.unlock()


# ------------------------------------------------------------------ Nissan Consult
class FakeConsultEcu(BaseTransport):
    def __init__(self):
        super().__init__()
        self.out = deque()
        self.streaming = None

    def open(self, port, baudrate):
        pass

    def close(self):
        pass

    def set_baudrate(self, baudrate):
        pass

    def flush(self):
        self.out.clear()

    def read(self, size, timeout=0.5):
        if not self.out and self.streaming:
            self.out.extend(self.streaming)
        data = bytes(self.out.popleft() for _ in range(min(size, len(self.out))))
        return data

    def write(self, data):
        data = bytes(data)
        if data == b"\xff\xff\xef":
            self.out.append(0x10)
        elif data == b"\x30":
            self.streaming = None
            self.out.clear()
            self.out.append(0xCF)
        elif data == b"\xf0":
            pass
        elif data[0] == 0x5A:
            regs = data[1::2]
            self.out.extend(bytes(b if i % 2 else 0xA5 for i, b in enumerate(data)))
            values = {0x00: 0x00, 0x01: 0x40, 0x08: 0x87, 0x0C: 0x96}
            if any(r not in values for r in regs):
                self.out.clear()
                self.out.extend(b"\xfe")  # wrong echo -> unsupported register
                return
            self.streaming = bytes([0xFF, len(regs)] + [values[r] for r in regs])
        elif data == b"\xd1":
            self.out.append(0x2E)
            self.streaming = bytes([0xFF, 4, 0x13, 0x05, 0x55, 0x00])
        elif data == b"\xd0":
            self.out.append(0x2F)
            self.streaming = bytes([0xFF, 22] + [0] * 2 + [0x12, 0x34] + [0] * 15 + [0x1, 0x23, 0x45])[:24]


def test_consult_live_data_dtcs_and_part_number():
    from klinescan.protocols.consult import ConsultProtocol

    proto = ConsultProtocol(FakeConsultEcu(), EcuTarget("ECU", ProtocolType.NISSAN_CONSULT, baudrate=9600))
    assert proto.connect()
    live = {p.name: p.numeric_value for p in proto.read_live_pids()}
    assert live == {"Engine speed": 0x40 * 12.5, "Coolant temperature": 0x87 - 50,
                    "Battery voltage": 0x96 * 80 / 1000}
    dtcs = proto.read_dtcs()
    assert [d.code for d in dtcs] == ["NISSAN 13"]
    assert dtcs[0].description == "Engine coolant temperature sensor"
    with pytest.raises(Exception, match="not implemented"):
        proto.clear_dtcs()


# ------------------------------------------------------------------ misc
def test_report_escapes_ecu_text():
    from klinescan.core.models import EcuIdItem
    from klinescan.core.report_exporter import generate_html_report

    html = generate_html_report("Car <b>", "ECU", [EcuIdItem("VIN", "<script>alert(1)</script>")], [])
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_atomic_write_keeps_old_file_on_failure(tmp_path, monkeypatch):
    from klinescan.core import fileio

    path = tmp_path / "settings.json"
    path.write_text("old")
    monkeypatch.setattr(fileio.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        fileio.atomic_write_text(str(path), "new")
    assert path.read_text() == "old" and os.listdir(tmp_path) == ["settings.json"]


def test_dtc_structure_fallback():
    from klinescan.data.dtc_db import lookup_dtc

    assert lookup_dtc("P0307")[0] == "Cylinder 7 Misfire Detected"
    assert lookup_dtc("P1234")[0].startswith("Manufacturer-specific powertrain code")
    assert lookup_dtc("B2AAA")[0].startswith("Manufacturer-specific body code")
    assert lookup_dtc("U3000")[0].startswith("Generic (SAE) network")


def test_gui_never_calls_worker_methods_directly():
    """Only cancel_current (a thread-safe flag) may be called across the thread boundary."""
    import re

    source = open(os.path.join(os.path.dirname(__file__), "..", "klinescan", "ui", "main_window.py")).read()
    calls = set(re.findall(r"self\._worker\.(\w+)\(", source))
    assert calls <= {"cancel_current", "moveToThread"}, calls


def test_worker_error_text_keeps_classification():
    from klinescan.workers.scanner_worker import describe_error

    assert describe_error(ScanTimeout("no reply")).startswith("[timeout]")
    assert describe_error(SafetyGateError("blocked")).startswith("[safety gate]")
    assert describe_error(PermissionError("permission denied: /dev/ttyUSB0")).startswith("[permission]")
