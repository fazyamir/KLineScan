"""Unit tests for FakeECU end-to-end connection, PID polling, and DTC clearing."""

import pytest
from klinescan.core.models import EcuTarget, ProtocolType
from klinescan.protocols.kwp2000 import Kwp2000Protocol
from klinescan.protocols.isotp_obd import IsoTpObdProtocol
from klinescan.transport.fake_ecu import FakeEcuTransport


def test_fake_ecu_kwp2000_fast_init_and_dtcs():
    transport = FakeEcuTransport()
    transport.open("MOCK_PORT", 10400)

    target = EcuTarget(
        name="Hyundai Engine",
        protocol=ProtocolType.KWP2000_FAST,
        baudrate=10400,
        ecu_addr=0x11,
        tester_addr=0xF1,
        explicit_length=False,
    )
    protocol = Kwp2000Protocol(transport, target)

    # 1. Connect
    connected = protocol.connect()
    assert connected, "KWP2000 Fast Init connection failed on FakeECU"

    # 2. Read DTCs
    dtcs = protocol.read_dtcs()
    assert len(dtcs) >= 2, "Expected stored DTCs from FakeECU"
    codes = [d.code for d in dtcs]
    assert "P0115" in codes
    assert "P0300" in codes

    # 3. Clear DTCs (Mode 14)
    cleared = protocol.clear_dtcs()
    assert cleared, "Clear DTCs request rejected"

    # 4. Verify cleared
    dtcs_after = protocol.read_dtcs()
    assert len(dtcs_after) == 0, "DTCs should be empty after clear"

    transport.close()


def test_fake_ecu_iso9141_telemetry_polling():
    transport = FakeEcuTransport()
    transport.open("MOCK_PORT", 10400)

    target = EcuTarget(
        name="Generic OBD-II",
        protocol=ProtocolType.ISO9141,
        baudrate=10400,
        slow_init_addr=0x33,
        ecu_addr=0x12,
        use_obd_header=True,
    )
    protocol = Kwp2000Protocol(transport, target)

    connected = protocol.connect()
    assert connected, "ISO 9141 slow init handshake failed"

    # Read Live PIDs
    protocol.poll_batch = 0  # full sweep; the app rotates batches of 6 on K-line
    pids = protocol.read_live_pids()
    assert len(pids) >= 5, "Expected live telemetry PIDs"

    pid_map = {p.pid: p for p in pids}
    assert 0x0C in pid_map, "Engine RPM PID 0x0C missing"
    assert 0x05 in pid_map, "Coolant Temp PID 0x05 missing"
    assert 0x0D in pid_map, "Vehicle Speed PID 0x0D missing"

    # Verify numeric values are valid
    rpm = pid_map[0x0C].numeric_value
    assert rpm is not None and 700 <= rpm <= 3500

    coolant = pid_map[0x05].numeric_value
    assert coolant is not None and 60 <= coolant <= 110

    transport.close()


def test_fake_ecu_can_obd_dtc_clear():
    transport = FakeEcuTransport()
    transport.open("MOCK_PORT", 500000)

    target = EcuTarget(
        name="Generic CAN OBD",
        protocol=ProtocolType.CAN_OBD,
        baudrate=500000,
        can_tx_id=0x7DF,
        can_rx_id=0x7E8,
    )
    protocol = IsoTpObdProtocol(transport, target)

    connected = protocol.connect()
    assert connected, "CAN OBD connection failed"

    dtcs = protocol.read_dtcs()
    assert len(dtcs) >= 1

    ok = protocol.clear_dtcs()
    assert ok, "Clear CAN DTCs failed"

    dtcs_after = protocol.read_dtcs()
    assert len(dtcs_after) == 0

    transport.close()
