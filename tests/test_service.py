from klinescan.core.models import EcuTarget, ProtocolType
from klinescan.service import KLineScanService


def test_service_with_fake_can_ecu():
    target = EcuTarget(
        name="Fake CAN",
        protocol=ProtocolType.CAN_OBD,
        baudrate=500000,
        can_tx_id=0x7DF,
        can_rx_id=0x7E8,
    )
    with KLineScanService() as service:
        assert service.connect("fake", "MOCK_PORT", target)
        assert service.connected
        assert service.info.transport == "fake"
        dtcs = service.read_dtcs()
        assert any(d.code == "P0115" for d in dtcs)
        live = service.read_live_pids()
        assert live
        assert any(p.pid == 0x0C and p.numeric_value for p in live)
        assert service.clear_dtcs() is True
        assert service.read_dtcs() == []
    assert not service.connected


def test_service_session_metadata_identifies_connection():
    target = EcuTarget(
        name="Fake CAN",
        protocol=ProtocolType.CAN_OBD,
        baudrate=500000,
        can_tx_id=0x7DF,
        can_rx_id=0x7E8,
    )
    with KLineScanService() as service:
        service.connect("fake", "MOCK_PORT", target)
        metadata = service.session_metadata({"note": "unit test"})
        assert metadata["schema_version"] == 1
        assert metadata["application"]["name"] == "KLineScan"
        assert metadata["connection"] == {
            "transport": "fake",
            "protocol": ProtocolType.CAN_OBD.value,
            "port": "MOCK_PORT",
            "baudrate": 500000,
        }
        assert metadata["target"]["can_tx_id"] == 0x7DF
        assert metadata["target"]["can_rx_id"] == 0x7E8
        assert metadata["extra"]["note"] == "unit test"
        assert metadata["transport_info"]["capabilities"]["can"] is True


def test_service_connect_records_trace_with_metadata(tmp_path):
    from klinescan.core.trace import Trace

    target = EcuTarget(
        name="Fake CAN",
        protocol=ProtocolType.CAN_OBD,
        baudrate=500000,
        can_tx_id=0x7DF,
        can_rx_id=0x7E8,
    )
    path = tmp_path / "smoke.trace.csv"
    with KLineScanService() as service:
        service.connect("fake", "MOCK_PORT", target, trace_path=str(path))
        service.read_dtcs()
    trace = Trace.load(str(path))
    assert trace.metadata["connection"]["transport"] == "fake"
    assert trace.metadata["target"]["can_tx_id"] == 0x7DF
    assert any(event.direction == "TX" for event in trace.events)
    assert any(event.direction == "RX" for event in trace.events)


def test_service_start_and_stop_trace(tmp_path):
    from klinescan.core.trace import Trace

    target = EcuTarget(name="Fake CAN", protocol=ProtocolType.CAN_OBD, baudrate=500000)
    path = tmp_path / "late.trace.csv"
    with KLineScanService() as service:
        service.connect("fake", "MOCK_PORT", target)
        service.start_trace(str(path), {"run": "late"})
        assert service.trace_path == str(path)
        service.read_live_pids()
        assert service.stop_trace() == str(path)
        assert service.trace_path is None
    trace = Trace.load(str(path))
    assert trace.metadata["extra"]["run"] == "late"
    assert trace.events
