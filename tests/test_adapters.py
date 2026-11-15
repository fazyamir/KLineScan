"""Adapter, link, VIN, PID and identification tests (no hardware needed)."""

from __future__ import annotations

import pytest

from klinescan.core import kline_timing as KT
from klinescan.core.errors import BusError, ScanTimeout
from klinescan.core.identify import identify, year_span
from klinescan.core.models import EcuTarget, ProtocolType
from klinescan.core.obd_pids import PIDS, decode_bitmap, discover_supported, encode_bitmap
from klinescan.core.vin import check_digit, decode_vin
from klinescan.protocols.isotp_obd import IsoTpObdProtocol
from klinescan.protocols.kwp2000 import Kwp2000Protocol
from klinescan.transport.doip import DoIPTransport, _parse_port
from klinescan.transport.elm327 import Elm327Transport
from klinescan.transport.fake_ecu import FakeEcuTransport
from klinescan.transport.links import ByteLink


class ScriptedLink(ByteLink):
    """Answers ELM327 commands from a dict; records everything sent."""

    kind = "test"

    def __init__(self, replies: dict):
        self.replies = {k.upper(): v for k, v in replies.items()}
        self.sent: list[str] = []
        self._out = bytearray()

    def write(self, data: bytes) -> None:
        for cmd in data.decode().split("\r"):
            cmd = cmd.strip()
            if not cmd:
                continue
            self.sent.append(cmd.upper())
            reply = self.replies.get(cmd.upper(), "OK" if cmd.upper().startswith(("AT", "ST")) else "NO DATA")
            if callable(reply):
                reply = reply(cmd.upper())
            self._out += (reply + "\r\r>").encode()

    def read(self, size: int, timeout: float) -> bytes:
        out = bytes(self._out[:size])
        del self._out[:size]
        return out

    def reset_input(self) -> None:
        self._out.clear()

    def close(self) -> None:
        pass


def _elm(replies: dict) -> Elm327Transport:
    base = {"ATZ": "ELM327 v1.5", "ATI": "ELM327 v1.5", "ATRV": "12.4V", "STI": "?"}
    base.update(replies)
    link = ScriptedLink(base)
    elm = Elm327Transport(link=link)
    elm.open("test", 38400)
    return elm


# ----------------------------------------------------------------- ELM327
def test_elm_kwp_header_uses_datasheet_rules():
    elm = _elm({})
    target = EcuTarget("ABS", ProtocolType.KWP2000_FAST, ecu_addr=0x28, tester_addr=0xF1, explicit_length=True)
    elm.configure_target(target)
    sent = elm._link.sent
    assert "ATSP5" in sent
    assert "ATSH8028F1" in sent           # 80 -> 4-byte header with separate length byte
    assert "ATWM8028F13E" in sent         # TesterPresent wake-up with the session header
    target.explicit_length = False
    elm.configure_target(target)
    assert "ATSH8128F1" in elm._link.sent  # non-zero low digit -> length in the format byte


def test_elm_slow_init_sets_iia_and_protocol():
    elm = _elm({})
    elm.configure_target(EcuTarget("ABS", ProtocolType.ISO9141, slow_init_addr=0x30, ecu_addr=0x28))
    assert "ATSP4" in elm._link.sent and "ATIIA30" in elm._link.sent


def test_elm_can_bmw_extended_addressing_and_flow_control():
    elm = _elm({})
    target = EcuTarget("CAS", ProtocolType.UDS_CAN, can_tx_id=0x6F1, can_rx_id=0x640,
                       can_ext_address=0x40, can_rx_ext_address=0xF1, can_bitrate=500000)
    elm.configure_target(target)
    sent = elm._link.sent
    for cmd in ("ATSP6", "ATSH6F1", "ATCEA40", "ATCRA640", "ATFCSH6F1", "ATFCSD40300000", "ATFCSM1"):
        assert cmd in sent, cmd


def test_elm_can_bitrate_falls_back_and_discovers_responder():
    attempts = {"n": 0}

    def probe(cmd):
        attempts["n"] += 1
        return "CAN ERROR" if attempts["n"] == 1 else "7DC 02 7E 00"

    elm = _elm({"3E00": probe})
    target = EcuTarget("MDPS", ProtocolType.UDS_CAN, can_tx_id=0x7D4, can_rx_id=0)
    elm.configure_target(target)
    assert elm.can_bitrate == 250000          # 500k reported CAN ERROR
    assert elm.can_rx_id == 0x7DC             # learned from the reply, not assumed
    assert "ATCRA7DC" in elm._link.sent


def test_elm_parses_multiframe_can_and_kline_and_errors():
    elm = _elm({"0902": "7E8 10 14 49 02 01 57 42 41\r7E8 21 33 41 35 43 35 31 43\r7E8 22 46 32 35 36 36 35 31",
                "0100": "48 6B 10 41 00 BE 3E B8 11 FA"})
    elm.protocol = "6"
    assert elm.send_obd_payload(bytes([0x09, 0x02])) == b"\x49\x02\x01WBA3A5C51CF256651"
    elm.protocol = "3"
    assert elm.send_obd_payload(bytes([0x01, 0x00])) == bytes.fromhex("4100BE3EB811")
    elm2 = _elm({"0100": "BUS INIT: ...ERROR"})
    with pytest.raises(BusError):
        elm2.send_obd_payload(bytes([0x01, 0x00]))
    assert elm2.request(bytes([0x01, 0x05])) == []  # NO DATA -> no responders


# ------------------------------------------------------------------- K-line
def test_odd_parity_address_framing():
    assert KT.odd_parity_address(0x33) == 0xB3   # four ones -> parity bit set
    assert KT.odd_parity_address(0x01) == 0x01   # one one -> already odd
    assert all(bin(KT.odd_parity_address(a)).count("1") % 2 == 1 for a in range(128))


def test_kwp_functional_obd_header():
    target = EcuTarget("OBD", ProtocolType.KWP2000_FAST, ecu_addr=0x33, tester_addr=0xF1, header_format=0xC0)
    frame = Kwp2000Protocol(FakeEcuTransport(), target)._build_frame(bytes([0x81]))
    assert frame[:3] == bytes([0xC1, 0x33, 0xF1])


def test_iso9141_split_on_checksum():
    a = bytes.fromhex("486B1249020100000058") ; a = a[:-1] + bytes([sum(a[:-1]) & 0xFF])
    b = bytes.fromhex("486B124902024B4D484300"); b = b[:-1] + bytes([sum(b[:-1]) & 0xFF])
    assert Kwp2000Protocol._split_iso9141(a + b) == len(a)


# ------------------------------------------------------------------ DoIP
def test_doip_port_parsing_and_session():
    assert _parse_port("doip://192.168.0.10/0x1001") == ("192.168.0.10", 0x1001)
    assert _parse_port("doip://") == ("", None)

    class FakeClient:
        def __init__(self, host, logical, client_logical_address):
            self.args = (host, logical, client_logical_address)
            self.replies = [bytes([0x7F, 0x22, 0x78]), bytes([0x62, 0xF1, 0x90]) + b"VIN"]

        def send_diagnostic(self, payload, timeout):
            self.sent = bytes(payload)

        def receive_diagnostic(self, timeout):
            return self.replies.pop(0)

        def close(self):
            pass

    doip = DoIPTransport(client_factory=FakeClient)
    doip.open("doip://10.0.0.2/0x10", 0)
    doip.configure_target(EcuTarget("DME", ProtocolType.UDS_CAN))
    assert doip._client.args == ("10.0.0.2", 0x10, 0x0E00)
    assert doip.send_obd_payload(bytes([0x22, 0xF1, 0x90])) == b"\x62\xf1\x90VIN"  # 0x78 pending skipped


# ----------------------------------------------------------- VIN / PIDs
def test_vin_check_digit_and_decode():
    assert check_digit("1M8GDM9AXKP042788") == "X"   # worked example in the cited source
    info = decode_vin("KMHCN46C17U123456")
    assert info.manufacturer == "HYUNDAI" and info.check_digit_ok and info.likely_year == 2007
    assert not decode_vin("KMHCN46C17U12345O").valid_format  # O is never used


def test_pid_bitmaps_roundtrip_and_discovery():
    pids = {0x04, 0x05, 0x0C, 0x0D, 0x20, 0x40, 0x42}  # 0x20/0x40 advertise the next bitmap
    assert decode_bitmap(0x00, encode_bitmap(0x00, pids)) == {p for p in pids if p <= 0x20}
    chain = {base: encode_bitmap(base, pids) for base in (0x00, 0x20, 0x40)}
    assert discover_supported(lambda base: chain.get(base)) == pids
    assert PIDS[0x0C].decode(bytes([0x1A, 0xF8])) == 1726


def test_year_span_parsing():
    assert year_span("ACCENT     03-05") == (2003, 2005)
    assert year_span("2007MY") == (2007, 2007)
    assert year_span("ES300(    ~1993)") == (1980, 1993)


# ---------------------------------------------------------- identification
def _factory(transport, target):
    cls = IsoTpObdProtocol if target.protocol in (ProtocolType.CAN_OBD, ProtocolType.UDS_CAN) else Kwp2000Protocol
    return cls(transport, target)


@pytest.mark.parametrize("kinds,vin", [(("can",), "WBA3A5C51CF256651"), (("kline",), "KMHCN46C17U123456")])
def test_identify_on_mock_ecu(kinds, vin):
    transport = FakeEcuTransport()
    transport.open("MOCK", 10400)
    result = identify(transport, _factory, None, kinds)
    assert result.connected
    assert result.vin.vin == vin
    assert result.ecu_name == "ECM-EngineControl"
    assert len(result.supported_pids) >= 25


def test_mock_live_polling_covers_all_supported_pids():
    transport = FakeEcuTransport()
    transport.open("MOCK", 500000)
    protocol = IsoTpObdProtocol(transport, EcuTarget("can", ProtocolType.CAN_OBD, can_tx_id=0x7DF, can_rx_id=0x7E8))
    assert protocol.connect()
    supported = protocol.discover_supported_pids()
    live = protocol.read_live_pids()
    assert {p.pid for p in live} == set(supported) and len(live) >= 25
