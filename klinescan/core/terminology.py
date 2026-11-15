"""
Plain-English Terminology, Contextual Explanations, Expected Healthy Ranges,
and Actionable Diagnostic Trouble Code (DTC) Details.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class PidMetadata:
    pid: int
    short_name: str
    friendly_name: str
    category: str
    unit: str
    description: str
    ranges: Dict[str, Tuple[float, float]]  # State -> (min, max)


PID_TERMINOLOGY: Dict[int, PidMetadata] = {
    0x04: PidMetadata(
        pid=0x04,
        short_name="LOAD",
        friendly_name="Engine Load",
        category="Engine Dynamics",
        unit="%",
        description="Percentage of peak available engine torque demanded.",
        ranges={"Warm Idle": (15.0, 30.0), "Cruising": (30.0, 75.0), "Cold Idle": (25.0, 45.0)},
    ),
    0x05: PidMetadata(
        pid=0x05,
        short_name="ECT",
        friendly_name="Engine Coolant Temperature",
        category="Temperatures",
        unit="°C",
        description="Temperature of the engine cooling fluid circulating through cylinder head.",
        ranges={"Warm Idle": (82.0, 98.0), "Cruising": (82.0, 98.0), "Cold Idle": (10.0, 70.0)},
    ),
    0x06: PidMetadata(
        pid=0x06,
        short_name="STFT1",
        friendly_name="Short-Term Fuel Adjustment (Bank 1)",
        category="Fuel System",
        unit="%",
        description="Immediate ECU closed-loop fuel correction based on O2 sensor feedback.",
        ranges={"Warm Idle": (-8.0, 8.0), "Cruising": (-10.0, 10.0)},
    ),
    0x07: PidMetadata(
        pid=0x07,
        short_name="LTFT1",
        friendly_name="Long-Term Fuel Adjustment (Bank 1)",
        category="Fuel System",
        unit="%",
        description="Learned baseline fuel compensation. Persistent values beyond ±10% indicate intake leak or fuel delivery issue.",
        ranges={"Warm Idle": (-10.0, 10.0), "Cruising": (-10.0, 10.0)},
    ),
    0x0B: PidMetadata(
        pid=0x0B,
        short_name="MAP",
        friendly_name="Intake Manifold Absolute Pressure",
        category="Air & Fuel",
        unit="kPa",
        description="Vacuum/pressure inside intake manifold. Low pressure at idle indicates good engine vacuum.",
        ranges={"Warm Idle": (25.0, 42.0), "Cruising": (40.0, 85.0)},
    ),
    0x0C: PidMetadata(
        pid=0x0C,
        short_name="RPM",
        friendly_name="Engine Speed (RPM)",
        category="Engine Dynamics",
        unit="rpm",
        description="Crankshaft rotational speed in revolutions per minute.",
        ranges={"Warm Idle": (650.0, 850.0), "Cruising": (1500.0, 3200.0), "Cold Idle": (1000.0, 1500.0)},
    ),
    0x0D: PidMetadata(
        pid=0x0D,
        short_name="VSS",
        friendly_name="Vehicle Speed",
        category="Vehicle Dynamics",
        unit="km/h",
        description="Road speed reported by vehicle transmission output sensor.",
        ranges={"Warm Idle": (0.0, 0.0), "Cruising": (40.0, 140.0)},
    ),
    0x0E: PidMetadata(
        pid=0x0E,
        short_name="SPARKADV",
        friendly_name="Ignition Timing Advance",
        category="Engine Dynamics",
        unit="deg",
        description="Spark plug firing angle relative to top dead center (TDC).",
        ranges={"Warm Idle": (5.0, 16.0), "Cruising": (20.0, 42.0)},
    ),
    0x0F: PidMetadata(
        pid=0x0F,
        short_name="IAT",
        friendly_name="Intake Air Temperature",
        category="Temperatures",
        unit="°C",
        description="Temperature of ambient air entering the engine airbox/filter.",
        ranges={"Warm Idle": (15.0, 55.0), "Cruising": (15.0, 45.0)},
    ),
    0x10: PidMetadata(
        pid=0x10,
        short_name="MAF",
        friendly_name="Mass Air Flow Rate",
        category="Air & Fuel",
        unit="g/s",
        description="Mass flow rate of air drawn into cylinders. Rule of thumb: equal to engine liters at idle.",
        ranges={"Warm Idle": (1.8, 4.5), "Cruising": (10.0, 50.0)},
    ),
    0x11: PidMetadata(
        pid=0x11,
        short_name="TPS",
        friendly_name="Throttle Position",
        category="Air & Fuel",
        unit="%",
        description="Throttle plate opening angle relative to fully closed stop.",
        ranges={"Warm Idle": (8.0, 18.0), "Cruising": (15.0, 45.0)},
    ),
    0x14: PidMetadata(
        pid=0x14,
        short_name="O2S1",
        friendly_name="Upstream O2 Sensor Voltage (Bank 1)",
        category="Emissions",
        unit="V",
        description="Zirconia sensor cycling rapidly between 0.1V (lean) and 0.9V (rich).",
        ranges={"Warm Idle": (0.1, 0.9), "Cruising": (0.1, 0.9)},
    ),
    0x42: PidMetadata(
        pid=0x42,
        short_name="VBATT",
        friendly_name="Battery / Alternator Voltage",
        category="Electrical",
        unit="V",
        description="Supply voltage supplied by vehicle 12V battery and alternator charging system.",
        ranges={"Warm Idle": (13.6, 14.8), "Cruising": (13.6, 14.8), "Key On Engine Off": (11.8, 12.8)},
    ),
}


def get_pid_friendly_name(pid: int) -> str:
    meta = PID_TERMINOLOGY.get(pid)
    return meta.friendly_name if meta else f"Sensor 0x{pid:02X}"


def get_pid_category(pid: int) -> str:
    meta = PID_TERMINOLOGY.get(pid)
    return meta.category if meta else "General"


def evaluate_pid_health(pid: int, value: float, engine_state: str = "Warm Idle") -> Tuple[str, str]:
    """
    Evaluates live sensor value against expected healthy operational boundaries.
    Returns: (status: 'NORMAL' | 'WATCH' | 'OUT_OF_RANGE', advice: str)
    """
    meta = PID_TERMINOLOGY.get(pid)
    if not meta or not meta.ranges:
        return "NORMAL", "Nominal"

    target_range = meta.ranges.get(engine_state) or meta.ranges.get("Warm Idle")
    if not target_range:
        return "NORMAL", "Nominal"

    low, high = target_range
    tolerance = (high - low) * 0.25

    if low <= value <= high:
        return "NORMAL", f"Within healthy range ({low:.1f} - {high:.1f} {meta.unit})"
    elif (low - tolerance) <= value <= (high + tolerance):
        return "WATCH", f"Slightly drifted from target ({low:.1f} - {high:.1f} {meta.unit})"
    else:
        direction = "Low" if value < low else "High"
        return "OUT_OF_RANGE", f"Unusually {direction}! Expected {low:.1f} - {high:.1f} {meta.unit}"


@dataclass
class PlainEnglishDtc:
    code: str
    friendly_title: str
    severity_badge: str  # "Info", "Check Soon", "Do Not Drive"
    likely_causes: List[str]
    symptoms: List[str]
    simple_summary: str


DTC_PLAIN_ENGLISH: Dict[str, PlainEnglishDtc] = {
    "P0115": PlainEnglishDtc(
        code="P0115",
        friendly_title="Engine Coolant Temperature Sensor Problem",
        severity_badge="Check Soon",
        likely_causes=[
            "Faulty engine coolant temperature (ECT) sensor",
            "Corroded, wet, or damaged sensor wiring connector",
            "Engine thermostat stuck open (engine fails to reach operating temp)",
            "Low coolant fluid level / air pockets in cooling system",
        ],
        symptoms=[
            "Check Engine light illuminated",
            "Engine temperature gauge reads erratic or cold",
            "Radiator cooling fans running continuously at high speed",
            "Difficult cold engine starts and poor fuel economy",
        ],
        simple_summary="The car computer cannot read the engine water temperature reliably.",
    ),
    "P0300": PlainEnglishDtc(
        code="P0300",
        friendly_title="Random / Multiple Cylinder Misfire",
        severity_badge="Do Not Drive" if True else "Check Soon",
        likely_causes=[
            "Worn or fouled spark plugs",
            "Failing ignition coil packs or plug wires",
            "Vacuum intake manifold air leak",
            "Low fuel pressure or clogged fuel filter",
            "Low engine cylinder compression",
        ],
        symptoms=[
            "Engine shakes or stumbles heavily at idle",
            "Loss of acceleration power / hesitating under throttle",
            "Flashing Check Engine Light (flashing means catalyst damage imminent!)",
            "Strong unburnt fuel odor from exhaust",
        ],
        simple_summary="Fuel in one or more cylinders is not igniting properly.",
    ),
    "P0171": PlainEnglishDtc(
        code="P0171",
        friendly_title="Fuel System Running Too Lean (Bank 1)",
        severity_badge="Check Soon",
        likely_causes=[
            "Intake vacuum leak (cracked PCV hose, intake gasket)",
            "Dirty or contaminated Mass Air Flow (MAF) sensor",
            "Weak fuel pump or clogged fuel filter/injectors",
            "Exhaust leak before upstream oxygen sensor",
        ],
        symptoms=[
            "Engine hesitates on initial acceleration",
            "Rough idle that smooths out when revving engine",
            "Pinging or knocking under uphill load",
        ],
        simple_summary="The engine is getting too much air or not enough fuel.",
    ),
    "P0420": PlainEnglishDtc(
        code="P0420",
        friendly_title="Catalytic Converter Efficiency Below Threshold",
        severity_badge="Check Soon",
        likely_causes=[
            "Aged or degraded catalytic converter substrate",
            "Lazy downstream oxygen sensor (Bank 1 Sensor 2)",
            "Exhaust pipe leak near the converter",
            "Prolonged misfire previously dumping raw fuel into exhaust",
        ],
        symptoms=[
            "Check Engine light stays ON",
            "Usually no noticeable drivability change",
            "Vehicle will fail emissions inspection test",
        ],
        simple_summary="The catalytic converter is not cleaning exhaust gases as well as required.",
    ),
    "P0335": PlainEnglishDtc(
        code="P0335",
        friendly_title="Crankshaft Position Sensor Circuit Malfunction",
        severity_badge="Do Not Drive",
        likely_causes=[
            "Failed crankshaft position (CKP) sensor",
            "Damaged sensor wiring harness near hot exhaust or pulleys",
            "Broken reluctor wheel teeth on flywheel/crank",
        ],
        symptoms=[
            "Engine cranks for a long time but will not start",
            "Engine suddenly stalls while driving and refuses to restart",
            "Tachometer needle does not move while cranking",
        ],
        simple_summary="The computer cannot tell where the engine pistons and crankshaft are turning.",
    ),
    "P0500": PlainEnglishDtc(
        code="P0500",
        friendly_title="Vehicle Speed Sensor (VSS) Signal Error",
        severity_badge="Check Soon",
        likely_causes=[
            "Faulty transmission output vehicle speed sensor",
            "Broken speed sensor drive gear or loose plug",
            "ABS wheel speed sensor fault (on CAN-linked vehicles)",
        ],
        symptoms=[
            "Speedometer on dashboard does not work or fluctuates",
            "Automatic transmission shifts erratically or hard",
            "Cruise control disabled",
        ],
        simple_summary="The transmission and computer cannot detect how fast the vehicle is traveling.",
    ),
    "U0100": PlainEnglishDtc(
        code="U0100",
        friendly_title="Lost Communication With Engine Computer (ECM/PCM)",
        severity_badge="Do Not Drive",
        likely_causes=[
            "Low battery voltage or dead 12V battery",
            "Blown main engine management fuse or main relay",
            "Damaged CAN bus wiring (CAN-High or CAN-Low shorted)",
            "Corroded ground wire strap on engine block",
        ],
        symptoms=[
            "Multiple dashboard warning lights illuminated (ABS, Airbag, Check Engine)",
            "No-crank or no-start condition",
            "Diagnostic scan tool cannot establish connection to engine",
        ],
        simple_summary="Other vehicle computers cannot talk to the main engine computer over the network.",
    ),
}


def get_dtc_plain_english(code: str) -> PlainEnglishDtc:
    clean = code.strip().upper()
    if clean in DTC_PLAIN_ENGLISH:
        return DTC_PLAIN_ENGLISH[clean]

    # Rule-based fallback
    prefix = clean[0] if clean else "P"
    digit2 = clean[1] if len(clean) > 1 else "0"

    sev = "Check Soon"
    if prefix == "U":
        sev = "Check Soon"
        title = f"Network Communication Trouble Code ({clean})"
        causes = ["Loose CAN bus wiring", "Low vehicle battery voltage", "Unresponsive module"]
        symp = ["Warning lights on dash", "Intermittent accessory functions"]
    elif prefix == "C":
        title = f"Chassis / Anti-Lock Brake Trouble Code ({clean})"
        causes = ["Wheel speed sensor debris or fault", "Brake pedal switch", "ABS pump wiring"]
        symp = ["ABS or Traction Control warning light on", "Traction control disabled"]
    elif prefix == "B":
        title = f"Body / Safety System Trouble Code ({clean})"
        causes = ["Airbag sensor connection", "Door latch switch", "Battery low voltage"]
        symp = ["Airbag light or door ajar warning"]
    else:
        title = f"Engine / Powertrain Trouble Code ({clean})"
        causes = ["Sensor out of calibration", "Wiring harness issue", "Component wear"]
        symp = ["Check Engine Light active"]

    return PlainEnglishDtc(
        code=clean,
        friendly_title=title,
        severity_badge=sev,
        likely_causes=causes,
        symptoms=symp,
        simple_summary=f"System reported diagnostic fault code {clean}.",
    )
