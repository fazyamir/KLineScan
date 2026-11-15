"""Unit tests for KWP2000 & ISO 9141-2 framing, checksums, and DTC decoding."""

import pytest
from klinescan.core.models import EcuTarget, ProtocolType
from klinescan.data.dtc_db import lookup_dtc
from klinescan.protocols.kwp2000 import Kwp2000Protocol, decode_dtc_pair
from klinescan.transport.fake_ecu import FakeEcuTransport


def test_dtc_pair_decoding():
    # 0x0115 -> P0115 (Engine Coolant Temp Circuit)
    assert decode_dtc_pair(0x01, 0x15) == "P0115"
    # 0x0300 -> P0300 (Random Misfire)
    assert decode_dtc_pair(0x03, 0x00) == "P0300"
    # Chassis C0035 (b1 >> 6 == 1)
    assert decode_dtc_pair(0x40, 0x35) == "C0035"
    # Body B0001 (b1 >> 6 == 2)
    assert decode_dtc_pair(0x80, 0x01) == "B0001"
    # Network U0100 (b1 >> 6 == 3)
    assert decode_dtc_pair(0xC1, 0x00) == "U0100"


def test_dtc_database_lookup():
    desc, sev = lookup_dtc("P0115")
    assert "Coolant" in desc
    assert sev == "Warning"

    desc, sev = lookup_dtc("P0300")
    assert "Misfire" in desc
    assert sev == "Critical"

    desc, sev = lookup_dtc("U0100")
    assert "Communication" in desc
    assert sev == "Critical"


def test_kwp2000_frame_building_and_checksum():
    transport = FakeEcuTransport()
    target = EcuTarget(
        name="Engine",
        protocol=ProtocolType.KWP2000_FAST,
        ecu_addr=0x11,
        tester_addr=0xF1,
        explicit_length=False,
    )
    proto = Kwp2000Protocol(transport, target)

    # Payload 0x81 (StartCommunication): Header 0x81 0x11 0xF1 + 0x81 = sum 0x204 -> csum 0x04
    frame = proto._build_frame(bytes([0x81]))
    assert frame == bytes.fromhex("81 11 F1 81 04")
    assert sum(frame[:-1]) & 0xFF == frame[-1]

    # Explicit length target
    target_explicit = EcuTarget(
        name="ABS",
        protocol=ProtocolType.KWP2000_FAST,
        ecu_addr=0x28,
        tester_addr=0xF1,
        explicit_length=True,
    )
    proto_explicit = Kwp2000Protocol(transport, target_explicit)
    # Payload 0x81: 0x80 0x28 0xF1 0x01 0x81 -> sum 0x21B -> csum 0x1B
    frame_explicit = proto_explicit._build_frame(bytes([0x81]))
    assert frame_explicit == bytes.fromhex("80 28 F1 01 81 1B")
    assert sum(frame_explicit[:-1]) & 0xFF == frame_explicit[-1]


def test_kwp2000_payload_extraction():
    transport = FakeEcuTransport()
    target = EcuTarget(name="Engine", protocol=ProtocolType.KWP2000_FAST)
    proto = Kwp2000Protocol(transport, target)

    # 0x83 0xF1 0x11 0xC1 0xEF 0x8F + csum 0xC4
    frame = bytes.fromhex("83 F1 11 C1 EF 8F C4")
    payload = proto._extract_payload(frame)
    assert payload == bytes.fromhex("C1 EF 8F")
