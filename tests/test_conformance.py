from pathlib import Path

from klinescan.core.conformance import (
    discover_transcripts,
    run_directory,
    run_transcript_file,
)

GOLDEN_DIR = Path(__file__).parent / "golden"


def test_all_golden_transcripts_pass():
    results = run_directory(str(GOLDEN_DIR))
    assert results
    failures = [
        {
            "id": result.transcript_id,
            "error": result.error,
            "mismatches": result.mismatches,
            "expected_tx": result.expected_tx,
            "actual_tx": result.actual_tx,
        }
        for result in results
        if not result.passed
    ]
    assert failures == []


def test_golden_transcript_ids_are_unique():
    ids = []
    for path in discover_transcripts(str(GOLDEN_DIR)):
        with open(path, "r", encoding="utf-8") as handle:
            import json

            ids.append(json.load(handle)["id"])
    assert len(ids) == len(set(ids))


def test_golden_obd_live_values_are_decoded():
    result = run_transcript_file(str(GOLDEN_DIR / "obd2_can_live_pids.json"))
    assert result.passed, result.error
    pids = {pid.pid: pid for pid in result.operation_result}
    assert set(pids) == {0x04, 0x05, 0x0C, 0x0D, 0x0F, 0x11}  # exactly the bitmap's PIDs
    assert pids[0x04].value == "50.2"
    assert pids[0x05].value == "50"
    assert pids[0x0C].value == "1726"
    assert pids[0x0D].value == "50"
    assert pids[0x0F].value == "20"
    assert pids[0x11].value == "25.1"


def test_golden_kwp2000_dtc_is_decoded():
    result = run_transcript_file(str(GOLDEN_DIR / "kwp2000_fast_dtc.json"))
    assert result.passed, result.error
    assert len(result.operation_result) == 1
    dtc = result.operation_result[0]
    assert dtc.code == "P0115"
    assert dtc.status == "Stored"


def test_golden_uds_connect_uses_extended_session():
    result = run_transcript_file(str(GOLDEN_DIR / "uds_can_connect.json"))
    assert result.passed, result.error
    assert result.operation_result is True
    assert result.actual_tx == ["02 10 03 00 00 00 00 00"]


def test_golden_kwp1281_fault_and_counter():
    result = run_transcript_file(str(GOLDEN_DIR / "kwp1281_faults.json"))
    assert result.passed, result.error
    assert len(result.operation_result) == 1
    dtc = result.operation_result[0]
    assert dtc.raw_hex == "01 15 20"
    assert dtc.code == "VAG 00277"
    assert dtc.status == "Elaboration 32"


def test_golden_kwp1281_live_types():
    result = run_transcript_file(str(GOLDEN_DIR / "kwp1281_live.json"))
    assert result.passed, result.error
    pids = result.operation_result
    assert pids[0].raw_hex == "0D AC"
    # formula 0x01 = MW*NW*0.2 /min, 0x02 = MW*NW*0.002 %, 0x03 = MW*NW*0.002 deg
    # (KLineKWP1281Lib getMeasurementValue; NW is the first data byte)
    assert abs(pids[0].numeric_value - 0xAC * 0x0D * 0.2) < 1e-9
    assert abs(pids[1].numeric_value - 0x20 * 0x10 * 0.002) < 1e-9
    assert pids[2].raw_hex == "0A F0"
    assert abs(pids[2].numeric_value - 0xF0 * 0x0A * 0.2) < 1e-9
    assert abs(pids[3].numeric_value - 0xFF * 0x80 * 0.002) < 1e-9
    assert abs(pids[4].numeric_value - 0xA0 * 0x0F * 0.2) < 1e-9
    assert abs(pids[5].numeric_value - 0xAA * 0x55 * 0.002) < 1e-9
