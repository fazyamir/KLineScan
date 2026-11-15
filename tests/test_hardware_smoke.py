import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import hardware_smoke
from klinescan.core.trace import Trace


def test_hardware_smoke_reports_and_records_with_fake_ecu(tmp_path, capsys):
    report_path = tmp_path / "smoke.json"
    trace_path = tmp_path / "smoke.trace.csv"

    exit_code = hardware_smoke.main(
        [
            "--transport",
            "fake",
            "--port",
            "MOCK_PORT",
            "--protocol",
            "can_obd",
            "--trace",
            str(trace_path),
            "--output",
            str(report_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["connected"] is True
    assert [item["operation"] for item in report["operations"]] == [
        "connect",
        "read_dtcs",
    ]
    assert report["metadata"]["connection"]["transport"] == "fake"

    trace = Trace.load(str(trace_path))
    assert trace.metadata["extra"]["tool"] == "hardware-smoke"
    assert any(event.direction == "TX" for event in trace.events)
    assert any(event.direction == "RX" for event in trace.events)


def test_hardware_smoke_failure_returns_nonzero(tmp_path):
    report_path = tmp_path / "failed.json"

    exit_code = hardware_smoke.main(
        [
            "--transport",
            "does-not-exist",
            "--port",
            "NONE",
            "--protocol",
            "can_obd",
            "--output",
            str(report_path),
        ]
    )

    assert exit_code == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["operations"][0]["operation"] == "connect"
    assert report["operations"][0]["ok"] is False
