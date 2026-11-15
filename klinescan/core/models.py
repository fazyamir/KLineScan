from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TransportType(str, Enum):
    FAKE = "Fake ECU (Offline)"
    KL_SERIAL = "KL Serial (Dumb KKL)"
    FTDI_DIRECT = "Direct FTDI (Bitbang)"
    ELM327 = "ELM327 / OBDLink (USB, Bluetooth, Wi-Fi, BLE)"
    KCAN = "VAG K+CAN 1.4 (CAN)"
    J2534 = "J2534 PassThru"
    SOCKETCAN = "SocketCAN / python-can"
    DOIP = "DoIP (Ethernet / ENET)"


class ProtocolType(str, Enum):
    ISO9141 = "ISO 9141-2 (5-Baud Slow Init)"
    KWP2000_FAST = "ISO 14230 (KWP2000 Fast Init)"
    CAN_OBD = "ISO 15765-4 (CAN OBD-II)"
    UDS_CAN = "ISO 14229 (UDS over CAN)"
    KWP1281 = "VAG KWP1281"
    NISSAN_CONSULT = "Nissan Consult I (pre-OBD)"


class BaudMode(str, Enum):
    AUTOMATIC = "Automatic (Profile)"
    INCREMENTAL = "Incremental / Auto+"
    FIXED = "Fixed / Override"


@dataclass
class DiagnosticTroubleCode:
    code: str
    raw_hex: str
    status: str = "Stored"  # "Stored", "Pending", "Permanent"
    description: str = "Unknown / Manufacturer Specific"
    severity: str = "Warning"  # "Info", "Warning", "Critical"


@dataclass
class FreezeFramePid:
    pid: int
    name: str
    value: str
    unit: str
    raw_hex: str


@dataclass
class FreezeFrame:
    dtc_trigger: str
    pids: List[FreezeFramePid] = field(default_factory=list)


@dataclass
class EcuIdItem:
    label: str
    value: str
    raw_hex: str = ""


@dataclass
class ReadinessMonitor:
    name: str
    available: bool = True
    complete: bool = False


@dataclass
class LivePid:
    pid: int
    name: str
    value: str
    unit: str
    raw_hex: str
    numeric_value: Optional[float] = None
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    avg_val: Optional[float] = None
    sample_count: int = 0
    category: str = "Engine"

    def update_stats(self, num: float) -> None:
        self.numeric_value = num
        if self.min_val is None or num < self.min_val:
            self.min_val = num
        if self.max_val is None or num > self.max_val:
            self.max_val = num
        if self.avg_val is None:
            self.avg_val = num
        else:
            self.avg_val = (self.avg_val * self.sample_count + num) / (self.sample_count + 1)
        self.sample_count += 1


@dataclass
class ActuatorDefinition:
    id: str
    name: str
    description: str = ""
    payload_start: bytes = b""
    payload_stop: bytes = b""
    duration_seconds: float = 5.0
    warning: str = ""


@dataclass
class LiveParamDefinition:
    """Pack-defined live value for ECUs without SAE J1979 PIDs (KWP2000 local IDs, UDS DIDs...).

    ``request`` is sent as-is (e.g. ``21 01`` ReadDataByLocalIdentifier 0x01, ``22 F4 0C``);
    the value is ``formula`` applied to ``length`` bytes at ``offset`` of the positive
    response (offset counts from the response service byte).  Formulas use A, B, C...
    for those bytes (see ``klinescan.core.pack_schema.evaluate_pid_bytes``).
    """

    name: str
    request: bytes
    offset: int
    length: int = 1
    formula: str = "A"
    unit: str = ""
    category: str = "Engine"
    pid: int = 0


@dataclass
class SpecialFunctionDefinition:
    id: str
    name: str
    description: str = ""
    security_level: int = 0
    command: bytes = b""
    steps: List[str] = field(default_factory=list)


@dataclass
class EcuTarget:
    name: str
    protocol: ProtocolType
    baudrate: int = 10400
    slow_init_addr: int = 0x33
    tester_addr: int = 0xF1
    ecu_addr: int = 0x11
    header_format: int = 0x80          # 0x80 = explicit len or fmt|len
    explicit_length: bool = False      # e.g., True for 80 28 F1 01 81 1B
    can_tx_id: int = 0x7DF
    can_rx_id: int = 0x7E8
    use_obd_header: bool = False       # True for 68 6A F1 ... (ISO 9141-2 / CARB)
    pin: int = 7                       # OBD-II physical pin (7=K-Line Engine/AT, 8=ABS, 1=L-line/SCS, 6=CAN-H, 14=CAN-L)
    keep_alive_ms: int = 2000
    # CAN addressing details.  ``can_rx_id`` of 0 means "discover the responder".
    can_bitrate: int = 0               # 0 = auto-detect (500k -> 250k -> 125k)
    can_extended_id: bool = False      # 29-bit identifiers
    can_ext_address: Optional[int] = None     # ISO 15765-2 extended addressing: first data byte
    can_rx_ext_address: Optional[int] = None  # expected first byte in responses (e.g. tester 0xF1)
    doip_address: str = ""             # DoIP: ECU IP ("" = discover) ...
    doip_logical_address: int = 0      # ... and logical address
    five_baud_parity: str = "odd"      # "odd" (ISO 9141-2) or "none" (raw 8-bit address)
    echo_mode: str = ""                # K-line echo: "" (adapter setting), "auto", "on", "off"
    live_params: List[LiveParamDefinition] = field(default_factory=list)
    measuring_groups: List[int] = field(default_factory=list)   # KWP1281 groups to poll
    security_algorithm: str = ""       # name registered in core.security (seed -> key)
    security_level: int = 0            # 0x27 access level (odd = request seed)
    actuators: List[ActuatorDefinition] = field(default_factory=list)
    special_functions: List[SpecialFunctionDefinition] = field(default_factory=list)
    confidence: str = "curated"  # curated, imported, inferred, unverified
    source_ref: str = ""


@dataclass
class VehicleProfile:
    label: str
    make: str = ""
    model: str = ""
    year: str = ""
    system_type: str = ""
    targets: List[EcuTarget] = field(default_factory=list)


@dataclass
class BusFrame:
    direction: str   # "TX", "RX", "INFO", "ERR"
    data: bytes
    note: str = ""
    timestamp: float = 0.0
    delta_ms: float = 0.0


@dataclass
class SessionEvent:
    timestamp: float
    event_type: str  # "FRAME", "PID", "DTC", "STATE", "HINT"
    payload: Dict[str, Any]
