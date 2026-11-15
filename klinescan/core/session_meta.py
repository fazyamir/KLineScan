"""Reproducible session metadata for captures, bug reports and CI artifacts."""

from __future__ import annotations

import platform
import sys
from typing import Any, Dict, Optional

from klinescan import __version__
from klinescan.core.models import EcuTarget
from klinescan.transport.base import TransportInfo

SCHEMA_VERSION = 1


def target_as_dict(target: EcuTarget) -> Dict[str, Any]:
    return {
        "name": target.name,
        "protocol": target.protocol.value,
        "baudrate": target.baudrate,
        "slow_init_addr": target.slow_init_addr,
        "tester_addr": target.tester_addr,
        "ecu_addr": target.ecu_addr,
        "explicit_length": target.explicit_length,
        "use_obd_header": target.use_obd_header,
        "can_tx_id": target.can_tx_id,
        "can_rx_id": target.can_rx_id,
        "pin": target.pin,
    }


def build_session_metadata(
    *,
    transport: str,
    protocol: str,
    port: str,
    baudrate: int,
    target: EcuTarget,
    transport_info: Optional[TransportInfo] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build stable metadata identifying the exact software/transport context.

    The metadata intentionally contains no vehicle secrets. Trace redaction is
    applied separately by :class:`klinescan.core.trace.TraceRecorder`.
    """

    metadata: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "application": {"name": "KLineScan", "version": __version__},
        "runtime": {
            "python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "connection": {
            "transport": transport,
            "protocol": protocol,
            "port": port,
            "baudrate": int(baudrate),
        },
        "target": target_as_dict(target),
    }
    if transport_info is not None:
        metadata["transport_info"] = transport_info.as_dict()
    if extra:
        metadata["extra"] = dict(extra)
    return metadata
