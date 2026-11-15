"""Safety gate for operations that change ECU state.

Clearing DTCs, actuator tests, special functions (adaptation, coding, basic
settings) and security access can affect a running vehicle.  They are allowed
only on targets whose definition was curated (verified by a person), never on
imported/inferred catalogue entries, and never while a recorded session is
being replayed.  The check lives here, below the UI, so every caller
(ScannerWorker, KLineScanService, scripts) is subject to the same rules.
"""

from __future__ import annotations

from typing import Optional

from klinescan.core.errors import SafetyGateError
from klinescan.core.models import (
    ActuatorDefinition,
    EcuTarget,
    ProtocolType,
    SpecialFunctionDefinition,
)

WRITE_CONFIDENCE = {"curated"}
WRITE_OPERATIONS = {"clear_dtcs", "actuator", "special_function", "security_access", "raw"}


def write_block_reason(
    target: Optional[EcuTarget],
    operation: str,
    *,
    replaying: bool = False,
    definition: object = None,
) -> str:
    """Why ``operation`` is not allowed on ``target`` ("" when it is allowed)."""
    if operation not in WRITE_OPERATIONS:
        return ""
    if target is None:
        return "No ECU target selected."
    if replaying:
        return "Unavailable while a recorded session is replayed."
    confidence = (target.confidence or "unverified").lower()
    if confidence not in WRITE_CONFIDENCE:
        return (f"Target '{target.name}' is {confidence}; curate and verify its definition "
                "before running operations that change ECU state.")
    if operation == "actuator" and isinstance(definition, ActuatorDefinition):
        if not definition.payload_start and target.protocol != ProtocolType.KWP1281:
            return f"No command is defined for '{definition.name}' on this ECU."
    if operation == "special_function" and isinstance(definition, SpecialFunctionDefinition):
        if not definition.command:
            return f"'{definition.name}' is a manual procedure: follow its steps (no command defined)."
    return ""


def require_write_allowed(target, operation: str, **kwargs) -> None:
    reason = write_block_reason(target, operation, **kwargs)
    if reason:
        raise SafetyGateError(reason)
