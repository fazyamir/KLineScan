"""Unit tests for Session Recording & Playback roundtrip integrity."""

import os
import tempfile
import time
import pytest
from klinescan.core.models import BusFrame, DiagnosticTroubleCode, LivePid
from klinescan.core.session_recorder import SessionPlayer, SessionRecorder


def test_session_recorder_and_player_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        rec_path = os.path.join(tmpdir, "test_run.csv")

        recorder = SessionRecorder()
        recorder.start_recording(rec_path, {"vehicle": "Test Car", "tester": "Antigravity"})

        # Record frames, PIDs, DTCs
        f1 = BusFrame(direction="TX", data=bytes([0x68, 0x6A, 0xF1, 0x01, 0x0C, 0xD0]), note="Req RPM")
        f2 = BusFrame(direction="RX", data=bytes([0x48, 0x6B, 0x12, 0x41, 0x0C, 0x0D, 0xAC, 0x67]), note="Resp RPM")
        recorder.record_frame(f1)
        recorder.record_frame(f2)

        pid = LivePid(pid=0x0C, name="Engine RPM", value="875", unit="rpm", raw_hex="0D AC", numeric_value=875.0)
        recorder.record_pids([pid])

        dtc = DiagnosticTroubleCode(code="P0115", raw_hex="01 15", status="Stored", description="Coolant Circuit")
        recorder.record_dtcs([dtc])

        recorder.stop_recording()

        assert os.path.exists(rec_path)
        import csv
        with open(rec_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["event"] == "META" and rows[0]["value"] == "klinescan-session-csv/1"
        assert {r["event"] for r in rows} == {"META", "FRAME", "PID", "DTC"}
        assert any(r["event"] == "PID" and r["value"] == "875" and r["unit"] == "rpm" for r in rows)

        # Load back with Player
        received_frames = []
        received_pids = []
        received_dtcs = []

        player = SessionPlayer(
            frame_cb=lambda f: received_frames.append(f),
            pid_cb=lambda p: received_pids.extend(p),
            dtc_cb=lambda d: received_dtcs.extend(d),
        )

        ok = player.load(rec_path)
        assert ok, "Failed to load session recording"
        assert player.meta["vehicle"] == "Test Car"
        assert len(player._events) == 4

        # Run playback at high speed (5x)
        player.set_speed(10.0)
        player.play()
        time.sleep(0.3)
        player.stop()

        # Check roundtrip events
        assert len(received_frames) == 2
        assert received_frames[0].direction == "TX"
        assert received_frames[0].data == bytes([0x68, 0x6A, 0xF1, 0x01, 0x0C, 0xD0])
        assert received_frames[1].direction == "RX"

        assert len(received_pids) == 1
        assert received_pids[0].name == "Engine RPM"
        assert received_pids[0].numeric_value == 875.0

        assert len(received_dtcs) == 1
        assert received_dtcs[0].code == "P0115"
        assert received_dtcs[0].status == "Stored"
