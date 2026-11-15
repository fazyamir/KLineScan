"""
ISO 14229 (UDS over ISO-TP) using the shared robust channel.

Only bytes received from the ECU are decoded.  Services that are not
implemented are reported as unsupported rather than simulated.
"""

from typing import List, Optional

from klinescan.core.errors import UnsupportedOperationError
from klinescan.core.models import (
    ActuatorDefinition,
    DiagnosticTroubleCode,
    EcuIdItem,
    FreezeFrame,
    LivePid,
    ReadinessMonitor,
    SpecialFunctionDefinition,
)
from klinescan.data.dtc_db import lookup_dtc
from klinescan.protocols.base import BaseProtocol
from klinescan.protocols.isotp import IsoTpChannel
from klinescan.protocols.kwp2000 import decode_dtc_pair


class UdsProtocol(BaseProtocol):
    def __init__(self, transport, target, frame_cb=None):
        super().__init__(transport, target, frame_cb)
        self.channel = IsoTpChannel(transport, target)

    def _exchange(self, payload: bytes, note: str = "") -> bytes:
        self.log("TX", payload, note)
        response = self.channel.send(payload)
        self.log("RX", response, note)
        return response

    def _prepare_adapter(self) -> None:
        configure = getattr(self.transport, "configure_target", None)
        if configure is not None:
            configure(self.target)

    def connect(self) -> bool:
        self._prepare_adapter()
        response = self._exchange(bytes([0x10, 0x03]), "UDS Extended Session")
        if len(response) >= 2 and response[0] == 0x50:
            return True
        response = self._exchange(bytes([0x10, 0x01]), "UDS Default Session")
        return len(response) >= 2 and response[0] == 0x50

    def keep_alive(self) -> None:
        self._exchange(bytes([0x3E, 0x00]), "UDS TesterPresent")

    def read_dtcs(self, status_filter: str = "all") -> List[DiagnosticTroubleCode]:
        response = self._exchange(
            bytes([0x19, 0x02, 0xFF]), "UDS ReadDTCByStatusMask"
        )
        if len(response) < 3 or response[0] != 0x59 or response[1] != 0x02:
            return []
        raw = response[3:]
        dtcs: List[DiagnosticTroubleCode] = []
        for index in range(0, len(raw) - 3, 4):
            b1, b2, b3, status = (
                raw[index],
                raw[index + 1],
                raw[index + 2],
                raw[index + 3],
            )
            if b1 == 0 and b2 == 0 and b3 == 0:
                continue
            code = decode_dtc_pair(b1, b2)
            desc, sev = lookup_dtc(code)
            status_text = (
                "Confirmed"
                if status & 0x08
                else ("Pending" if status & 0x04 else f"0x{status:02X}")
            )
            dtcs.append(
                DiagnosticTroubleCode(
                    code,
                    f"{b1:02X} {b2:02X} {b3:02X}",
                    status_text,
                    desc,
                    sev,
                )
            )
        return dtcs

    def clear_dtcs(self) -> bool:
        response = self._exchange(
            bytes([0x14, 0xFF, 0xFF, 0xFF]), "UDS ClearDiagnosticInformation"
        )
        return len(response) >= 1 and response[0] == 0x54

    def read_did(self, did: int) -> bytes:
        response = self._exchange(
            bytes([0x22, (did >> 8) & 0xFF, did & 0xFF]), f"UDS DID 0x{did:04X}"
        )
        if len(response) < 3 or response[0] != 0x62:
            return b""
        return response[3:]

    def read_live_pids(self) -> List[LivePid]:
        """Emissions data via OBD-on-UDS DIDs 0xF4xx (SAE J1979-2), when the ECU offers it."""
        return self.poll_standard_pids()

    def query_pid(self, pid: int):
        try:
            response = self._exchange(bytes([0x22, 0xF4, pid]), f"UDS DID F4{pid:02X}")
        except Exception:
            return None
        if len(response) >= 3 and response[0] == 0x62 and response[1] == 0xF4 and response[2] == pid:
            return response[3:]
        return None

    def read_ecu_id(self) -> List[EcuIdItem]:
        items: List[EcuIdItem] = []
        for did, label in (
            (0xF190, "Vehicle VIN (DID F190)"),
            (0xF187, "ECU Part Number (DID F187)"),
            (0xF188, "ECU Software Number (DID F188)"),
            (0xF18A, "Supplier Identifier (DID F18A)"),
        ):
            value = self.read_did(did)
            if not value:
                continue
            text = bytes(value).decode("ascii", errors="ignore").strip("\x00 ")
            if text:
                items.append(EcuIdItem(label, text, value.hex()))
        return items

    def read_freeze_frame(self) -> Optional[FreezeFrame]:
        raise UnsupportedOperationError(
            "UDS freeze-frame decoding is not implemented; use 0x19 0x04 DTC snapshots"
        )

    def read_readiness(self) -> List[ReadinessMonitor]:
        return []

    def run_actuator_test(self, actuator: ActuatorDefinition, start: bool = True) -> bool:
        command = actuator.payload_start if start else actuator.payload_stop
        if not command:
            raise UnsupportedOperationError(
                f"No UDS IO-control payload defined for {actuator.name}"
            )
        response = self._exchange(command, f"UDS IO Control {actuator.name}")
        return len(response) >= 1 and response[0] == (command[0] + 0x40) & 0xFF

    def run_special_function(self, func: SpecialFunctionDefinition) -> bool:
        if not func.command:
            raise UnsupportedOperationError(
                f"No UDS routine command defined for {func.name}"
            )
        self.unlock(func.security_level)
        response = self._exchange(func.command, f"UDS RoutineControl {func.name}")
        return len(response) >= 1 and response[0] == (func.command[0] + 0x40) & 0xFF

    def security_exchange(self, payload: bytes) -> bytes:
        return self._exchange(payload, f"UDS SecurityAccess {payload[:2].hex(' ').upper()}")

    def send_raw_hex(self, raw: bytes) -> bytes:
        self.log("TX", raw, "UDS raw send")
        response = self.channel.send(raw)
        self.log("RX", response, "UDS raw reply")
        return response
