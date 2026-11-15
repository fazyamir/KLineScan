import csv
import json

from klinescan.core.models import BusFrame
from klinescan.core.trace import RedactionPolicy, Trace, TraceRecorder


def test_recorder_roundtrip_and_redaction(tmp_path):
    path = tmp_path / "run.trace.csv"
    recorder = TraceRecorder(RedactionPolicy())
    recorder.start(
        str(path),
        {"transport": "fake", "vin": "SECRETVIN", "nested": {"plate": "ABC123"}},
    )
    recorder.record("TX", b"\x01\x02", "request")
    recorder.record_frame(BusFrame("RX", b"\x41\x02", "reply"))
    recorder.stop()

    with open(path, newline="", encoding="utf-8") as f:
        rows = {r["name"]: r["value"] for r in csv.DictReader(f) if r["event"] == "META"}
    assert json.loads(rows["version"]) == 1
    metadata = json.loads(rows["metadata"])
    assert metadata["vin"] == "[redacted]"
    assert metadata["nested"]["plate"] == "[redacted]"
    assert "SECRETVIN" not in path.read_text()

    trace = Trace.load(str(path))
    assert trace.metadata["transport"] == "fake"
    assert [event.data for event in trace.events] == [b"\x01\x02", b"\x41\x02"]


def test_trace_save_load(tmp_path):
    path = tmp_path / "saved.trace.csv"
    recorder = TraceRecorder()
    recorder.start(str(path), {"transport": "unit"})
    recorder.record("TX", b"\xAA", "n")
    recorder.stop()
    trace = Trace.load(str(path))
    trace.save(str(tmp_path / "copy.trace.csv"))
    copy = Trace.load(str(tmp_path / "copy.trace.csv"))
    assert copy.events[0].data == b"\xAA"
