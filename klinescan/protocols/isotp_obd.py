"""
ISO 15765-4 (CAN OBD-II) using the shared ISO-TP channel.

All values are decoded from bytes actually received from the ECU.  When the
ECU does not answer a supported service the caller gets an explicit error or
an empty result; no value is fabricated.
"""

from typing import List, Optional

from klinescan.core.errors import NegativeResponseError, UnsupportedOperationError
from klinescan.core.models import (
    ActuatorDefinition,
    DiagnosticTroubleCode,
    EcuIdItem,
    FreezeFrame,
    FreezeFramePid,
    LivePid,
    ReadinessMonitor,
)
from klinescan.data.dtc_db import lookup_dtc
from klinescan.protocols.base import BaseProtocol
from klinescan.protocols.isotp import IsoTpChannel
from klinescan.protocols.kwp2000 import decode_dtc_pair

_FREEZE_PIDS = (0x04, 0x05, 0x0C, 0x0D, 0x0F, 0x11)


class IsoTpObdProtocol(BaseProtocol):
    def __init__(self, transport, target, frame_cb=None):
        super().__init__(transport, target, frame_cb)
        self.channel = IsoTpChannel(transport, target)

    def _pad_sf(self, payload: bytes) -> bytes:
        """Backward-compatible helper for callers that inspect single frames."""
        return self.channel._single_frame(payload)

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
        resp = self._exchange(bytes([0x01, 0x00]), "CAN OBD Supported PIDs")
        return len(resp) >= 2 and resp[0] == 0x41

    def keep_alive(self) -> None:
        self._exchange(bytes([0x3E, 0x00]), "CAN TesterPresent")

    def _read_dtc_service(self, service: int, status: str) -> List[DiagnosticTroubleCode]:
        positive = service + 0x40
        try:
            response = self._exchange(bytes([service]), f"CAN Mode 0x{service:02X} DTCs")
        except NegativeResponseError:
            return []
        if len(response) < 2 or response[0] != positive:
            return []
        count = response[1]
        raw = response[2 : 2 + count * 2]
        dtcs: List[DiagnosticTroubleCode] = []
        for index in range(0, len(raw) - 1, 2):
            b1, b2 = raw[index], raw[index + 1]
            if b1 == 0 and b2 == 0:
                continue
            code = decode_dtc_pair(b1, b2)
            desc, sev = lookup_dtc(code)
            dtcs.append(
                DiagnosticTroubleCode(
                    code, f"{b1:02X} {b2:02X}", status, desc, sev
                )
            )
        return dtcs

    def read_dtcs(self, status_filter: str = "all") -> List[DiagnosticTroubleCode]:
        dtcs: List[DiagnosticTroubleCode] = []
        if status_filter in ("all", "stored"):
            dtcs.extend(self._read_dtc_service(0x03, "Stored"))
        if status_filter in ("all", "pending"):
            dtcs.extend(self._read_dtc_service(0x07, "Pending"))
        if status_filter in ("all", "permanent"):
            dtcs.extend(self._read_dtc_service(0x0A, "Permanent"))
        return dtcs

    def clear_dtcs(self) -> bool:
        response = self._exchange(bytes([0x04]), "CAN Clear DTCs (Mode 04)")
        return len(response) >= 1 and response[0] == 0x44

    def read_live_pids(self) -> List[LivePid]:
        return self.poll_standard_pids()

    def query_pid(self, pid: int):
        """Service 01 PID; falls back to OBD-on-UDS (SAE J1979-2, DID 0xF4xx)."""
        if not getattr(self, "_obd_on_uds", False):
            try:
                response = self._exchange(bytes([0x01, pid]), f"CAN PID 0x{pid:02X}")
                if len(response) >= 2 and response[0] == 0x41 and response[1] == pid:
                    return response[2:]
            except NegativeResponseError as exc:
                if pid == 0x00 and exc.nrc in (0x11, 0x12):  # serviceNotSupported
                    self._obd_on_uds = True  # service 01 not supported: J1979-2 vehicle
                else:
                    return None
            if not getattr(self, "_obd_on_uds", False):
                return None
        response = self._exchange(bytes([0x22, 0xF4, pid]), f"OBDonUDS DID F4{pid:02X}")
        if len(response) >= 3 and response[0] == 0x62 and response[1] == 0xF4 and response[2] == pid:
            return response[3:]
        return None

    def read_freeze_frame(self) -> Optional[FreezeFrame]:
        # Mode 02 PID 02 reports the DTC that triggered the freeze frame.
        base = self._exchange(bytes([0x02, 0x02, 0x00]), "CAN Freeze Frame DTC")
        if len(base) < 4 or base[0] != 0x42 or base[1] != 0x02:
            return None
        dtc_trigger = decode_dtc_pair(base[3], base[4]) if len(base) >= 5 else ""
        pids: List[FreezeFramePid] = []
        if dtc_trigger:
            pids.append(
                FreezeFramePid(
                    0x02,
                    "DTC Trigger",
                    dtc_trigger,
                    "",
                    base[3:5].hex(" ").upper(),
                )
            )
        for pid, name, decoder in (
            (0x04, "Calculated Load", lambda d: f"{d[0] * 100 / 255:.1f}"),
            (0x05, "Coolant Temp", lambda d: f"{d[0] - 40}"),
            (0x0C, "Engine speed", lambda d: f"{((d[0] << 8) | d[1]) / 4:.0f}"),
            (0x0D, "Vehicle Speed", lambda d: f"{d[0]}"),
        ):
            response = self._exchange(bytes([0x02, pid, 0x00]), f"Freeze PID 0x{pid:02X}")
            if len(response) >= 5 and response[0] == 0x42 and response[1] == pid:
                payload = response[3:]
                try:
                    value = decoder(payload)
                except (IndexError, ZeroDivisionError):
                    continue
                pids.append(
                    FreezeFramePid(
                        pid,
                        name,
                        value,
                        "",
                        payload.hex(" ").upper(),
                    )
                )
        if not pids:
            return None
        return FreezeFrame(dtc_trigger=dtc_trigger or "Unknown", pids=pids)

    def read_ecu_id(self) -> List[EcuIdItem]:
        """Service 09 VIN / ECU name / calibration IDs; each item is read independently."""
        items: List[EcuIdItem] = []
        for info, label in ((0x02, "Vehicle VIN"), (0x0A, "ECU name"), (0x04, "Calibration ID")):
            try:
                response = self._exchange(bytes([0x09, info]), f"CAN Read {label} (Mode 09 {info:02X})")
            except Exception:
                continue  # one unsupported info type must not hide the others
            if len(response) >= 4 and response[0] == 0x49 and response[1] == info:
                # byte 2 is the number of data items; the data follow, NUL padded
                text = bytes(response[3:]).decode("ascii", errors="ignore").strip("\x00 ")
                if text:
                    items.append(EcuIdItem(label, text, response[3:].hex()))
        return items

    def read_readiness(self) -> List[ReadinessMonitor]:
        response = self._exchange(bytes([0x01, 0x01]), "CAN Readiness (PID 01)")
        if len(response) < 6 or response[0] != 0x41 or response[1] != 0x01:
            raise UnsupportedOperationError("ECU did not report readiness monitors")
        b2, b3 = response[3], response[4]
        return [
            ReadinessMonitor("Misfire", bool(b2 & 0x01), not bool(b2 & 0x10)),
            ReadinessMonitor("Fuel System", bool(b2 & 0x02), not bool(b2 & 0x20)),
            ReadinessMonitor("Comprehensive", bool(b2 & 0x04), not bool(b2 & 0x40)),
            ReadinessMonitor("Catalyst", bool(b3 & 0x01), not bool(b3 & 0x10)),
            ReadinessMonitor("Evaporative", bool(b3 & 0x04), not bool(b3 & 0x40)),
            ReadinessMonitor("O2 Sensor", bool(b3 & 0x20), not bool(b3 & 0x02)),
        ]

    def run_actuator_test(self, actuator: ActuatorDefinition, start: bool = True) -> bool:
        command = actuator.payload_start if start else actuator.payload_stop
        if not command:
            raise UnsupportedOperationError(
                f"No CAN actuator payload defined for {actuator.name}"
            )
        response = self._exchange(command, f"CAN actuator {actuator.name}")
        return len(response) >= 1 and response[0] in (0x6F, 0x70, 0x48)

    def send_raw_hex(self, raw: bytes) -> bytes:
        self.log("TX", raw, "CAN manual raw")
        response = self.channel.send(raw)
        self.log("RX", response, "CAN manual reply")
        return response
