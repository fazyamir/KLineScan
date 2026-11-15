#!/usr/bin/env python3
"""Run a read-only hardware smoke test and write a reproducible JSON report.

The default sequence is connect, read DTCs and read ECU identification. Live
data, readiness, DTC clearing and raw requests are opt-in. Every run can write
a versioned trace containing the connect handshake and all protocol frames.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from klinescan.core.errors import KLineScanError
from klinescan.core.models import EcuTarget, ProtocolType
from klinescan.service import KLineScanService

PROTOCOLS = {
    "can_obd": ProtocolType.CAN_OBD,
    "uds": ProtocolType.UDS_CAN,
    "kwp1281": ProtocolType.KWP1281,
    "kwp2000_fast": ProtocolType.KWP2000_FAST,
    "iso9141": ProtocolType.ISO9141,
}


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {key: _jsonable(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _run_operation(name: str, fn: Callable[[], Any]) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fn()
    except KLineScanError as exc:
        return {
            "operation": name,
            "ok": False,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "error_class": exc.error_class.value,
            "error": str(exc),
        }
    except Exception as exc:  # pragma: no cover - hardware defensive path
        return {
            "operation": name,
            "ok": False,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "error_class": "internal",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "operation": name,
        "ok": True,
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "result": _jsonable(result),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _int(value: str) -> int:
    return int(value, 0)


def build_target(args: argparse.Namespace) -> EcuTarget:
    return EcuTarget(
        name=args.name,
        protocol=PROTOCOLS[args.protocol],
        baudrate=args.baudrate,
        slow_init_addr=args.slow_init_addr,
        tester_addr=args.tester_addr,
        ecu_addr=args.ecu_addr,
        can_tx_id=args.can_tx_id,
        can_rx_id=args.can_rx_id,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", required=True, help="fake, ftdi, kl_serial, kcan, j2534, elm327, socketcan")
    parser.add_argument("--port", required=True, help="Serial port, D2XX index/serial, or SocketCAN interface")
    parser.add_argument("--protocol", required=True, choices=sorted(PROTOCOLS))
    parser.add_argument("--name", default="Smoke ECU")
    parser.add_argument("--baudrate", type=_int, default=None)
    parser.add_argument("--slow-init-addr", type=_int, default=0x33)
    parser.add_argument("--tester-addr", type=_int, default=0xF1)
    parser.add_argument("--ecu-addr", type=_int, default=0x11)
    parser.add_argument("--can-tx-id", type=_int, default=0x7DF)
    parser.add_argument("--can-rx-id", type=_int, default=0x7E8)
    parser.add_argument("--trace", help="Write a versioned trace (.trace.csv) to this path")
    parser.add_argument("--output", help="Write the JSON report to this path")
    parser.add_argument("--live", action="store_true", help="Also read live PIDs")
    parser.add_argument("--ecu-id", action="store_true", help="Also read ECU identification")
    parser.add_argument("--readiness", action="store_true", help="Also read readiness monitors")
    parser.add_argument("--clear-dtcs", action="store_true", help="Destructive: clear DTCs after reading")
    parser.add_argument("--raw", help="Optional hex payload sent with send_raw after the read operations")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.baudrate is None:
        args.baudrate = 500000 if args.protocol in ("can_obd", "uds") else 10400

    target = build_target(args)
    started_utc = _utc_now()
    operations = []
    metadata: Dict[str, Any] = {}
    service = KLineScanService()
    connected = False

    try:
        connect = _run_operation(
            "connect",
            lambda: service.connect(
                args.transport,
                args.port,
                target,
                trace_path=args.trace,
                trace_extra={"tool": "hardware-smoke", "started_utc": started_utc},
            ),
        )
        operations.append(connect)
        connected = connect["ok"]
        if not connected:
            return _finish(args, started_utc, target, operations, metadata, connected)

        metadata = service.session_metadata({"tool": "hardware-smoke"})
        operations.append(_run_operation("read_dtcs", service.read_dtcs))
        if args.ecu_id:
            operations.append(_run_operation("read_ecu_id", service.read_ecu_id))
        if args.live:
            operations.append(_run_operation("read_live_pids", service.read_live_pids))
        if args.readiness:
            operations.append(_run_operation("read_readiness", service.read_readiness))
        if args.clear_dtcs:
            operations.append(_run_operation("clear_dtcs", service.clear_dtcs))
        if args.raw:
            payload = bytes.fromhex(args.raw.replace(" ", ""))
            operations.append(
                _run_operation("send_raw", lambda: service.send_raw(payload))
            )
    finally:
        service.disconnect()
        if connected and not metadata:
            metadata = {}

    return _finish(args, started_utc, target, operations, metadata, connected)


def _finish(
    args: argparse.Namespace,
    started_utc: str,
    target: EcuTarget,
    operations,
    metadata,
    connected: bool,
) -> int:
    report = {
        "tool": "hardware-smoke",
        "schema_version": 1,
        "started_utc": started_utc,
        "finished_utc": _utc_now(),
        "trace": args.trace,
        "connected": connected,
        "target": {
            "name": target.name,
            "protocol": target.protocol.value,
            "baudrate": target.baudrate,
        },
        "metadata": metadata,
        "operations": operations,
        "ok": connected and all(item["ok"] for item in operations),
    }
    payload = json.dumps(report, indent=2, sort_keys=False) + "\n"
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        print(f"wrote {path}")
    else:
        sys.stdout.write(payload)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
