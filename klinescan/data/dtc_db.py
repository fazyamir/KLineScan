"""DTC (Diagnostic Trouble Code) database and resolver."""

from typing import Dict, Tuple

_STANDARD_DTC_MAP: Dict[str, Tuple[str, str]] = {
    # P00xx - Fuel and Air Metering and Auxiliary Emission Controls
    "P0001": ("Fuel Volume Regulator Control Circuit / Open", "Warning"),
    "P0002": ("Fuel Volume Regulator Control Circuit Range/Performance", "Warning"),
    "P0010": ("Camshaft Position Actuator Circuit / Open (Bank 1)", "Warning"),
    "P0011": ("Camshaft Position - Timing Over-Advanced (Bank 1)", "Warning"),
    "P0012": ("Camshaft Position - Timing Over-Retarded (Bank 1)", "Warning"),
    "P0016": ("Crankshaft Position - Camshaft Position Correlation (Bank 1 Sensor A)", "Critical"),
    "P0017": ("Crankshaft Position - Camshaft Position Correlation (Bank 1 Sensor B)", "Critical"),
    "P0030": ("HO2S Heater Control Circuit (Bank 1 Sensor 1)", "Warning"),
    "P0036": ("HO2S Heater Control Circuit (Bank 1 Sensor 2)", "Warning"),
    # P01xx - Fuel and Air Metering
    "P0100": ("Mass or Volume Air Flow Circuit Malfunction", "Warning"),
    "P0101": ("Mass or Volume Air Flow Circuit Range/Performance Problem", "Warning"),
    "P0102": ("Mass or Volume Air Flow Circuit Low Input", "Warning"),
    "P0103": ("Mass or Volume Air Flow Circuit High Input", "Warning"),
    "P0106": ("Manifold Absolute Pressure/Barometric Pressure Circuit Range/Performance", "Warning"),
    "P0107": ("Manifold Absolute Pressure/Barometric Pressure Circuit Low Input", "Warning"),
    "P0108": ("Manifold Absolute Pressure/Barometric Pressure Circuit High Input", "Warning"),
    "P0110": ("Intake Air Temperature Circuit Malfunction", "Info"),
    "P0112": ("Intake Air Temperature Circuit Low Input", "Warning"),
    "P0113": ("Intake Air Temperature Circuit High Input", "Warning"),
    "P0115": ("Engine Coolant Temperature Circuit Malfunction", "Warning"),
    "P0117": ("Engine Coolant Temperature Circuit Low Input", "Warning"),
    "P0118": ("Engine Coolant Temperature Circuit High Input", "Critical"),
    "P0120": ("Throttle/Pedal Position Sensor/Switch A Circuit Malfunction", "Critical"),
    "P0122": ("Throttle/Pedal Position Sensor/Switch A Circuit Low Input", "Critical"),
    "P0123": ("Throttle/Pedal Position Sensor/Switch A Circuit High Input", "Critical"),
    "P0128": ("Coolant Thermostat (Coolant Temp Below Thermostat Regulating Temp)", "Warning"),
    "P0130": ("O2 Sensor Circuit Malfunction (Bank 1 Sensor 1)", "Warning"),
    "P0133": ("O2 Sensor Circuit Slow Response (Bank 1 Sensor 1)", "Warning"),
    "P0134": ("O2 Sensor Circuit No Activity Detected (Bank 1 Sensor 1)", "Warning"),
    "P0135": ("O2 Sensor Heater Circuit Malfunction (Bank 1 Sensor 1)", "Warning"),
    "P0141": ("O2 Sensor Heater Circuit Malfunction (Bank 1 Sensor 2)", "Warning"),
    "P0171": ("System Too Lean (Bank 1)", "Warning"),
    "P0172": ("System Too Rich (Bank 1)", "Warning"),
    # P02xx - Fuel and Air Metering (Injector Circuit)
    "P0201": ("Cylinder 1 Injector Circuit / Open", "Critical"),
    "P0202": ("Cylinder 2 Injector Circuit / Open", "Critical"),
    "P0203": ("Cylinder 3 Injector Circuit / Open", "Critical"),
    "P0204": ("Cylinder 4 Injector Circuit / Open", "Critical"),
    "P0217": ("Engine Coolant Over Temperature Condition", "Critical"),
    "P0219": ("Engine Overspeed Condition", "Critical"),
    "P0234": ("Engine Overboost Condition", "Critical"),
    # P03xx - Ignition System or Misfire
    "P0300": ("Random/Multiple Cylinder Misfire Detected", "Critical"),
    "P0301": ("Cylinder 1 Misfire Detected", "Warning"),
    "P0302": ("Cylinder 2 Misfire Detected", "Warning"),
    "P0303": ("Cylinder 3 Misfire Detected", "Warning"),
    "P0304": ("Cylinder 4 Misfire Detected", "Warning"),
    "P0325": ("Knock Sensor 1 Circuit Malfunction (Bank 1)", "Warning"),
    "P0335": ("Crankshaft Position Sensor A Circuit Malfunction", "Critical"),
    "P0336": ("Crankshaft Position Sensor A Circuit Range/Performance", "Critical"),
    "P0340": ("Camshaft Position Sensor Circuit Malfunction", "Critical"),
    "P0341": ("Camshaft Position Sensor Circuit Range/Performance", "Warning"),
    # P04xx - Auxiliary Emissions Controls
    "P0400": ("Exhaust Gas Recirculation Flow Malfunction", "Warning"),
    "P0401": ("Exhaust Gas Recirculation Flow Insufficient Detected", "Warning"),
    "P0420": ("Catalyst System Efficiency Below Threshold (Bank 1)", "Warning"),
    "P0430": ("Catalyst System Efficiency Below Threshold (Bank 2)", "Warning"),
    "P0440": ("Evaporative Emission Control System Malfunction", "Warning"),
    "P0442": ("Evaporative Emission Control System Leak Detected (Small Leak)", "Warning"),
    "P0443": ("Evaporative Emission Control System Purge Control Valve Circuit", "Warning"),
    "P0455": ("Evaporative Emission Control System Leak Detected (Gross Leak)", "Warning"),
    # P05xx - Vehicle Speed Controls and Idle Control System
    "P0500": ("Vehicle Speed Sensor Malfunction", "Warning"),
    "P0505": ("Idle Control System Malfunction", "Warning"),
    "P0506": ("Idle Control System RPM Lower Than Expected", "Warning"),
    "P0507": ("Idle Control System RPM Higher Than Expected", "Warning"),
    "P0520": ("Engine Oil Pressure Sensor/Switch Circuit Malfunction", "Critical"),
    "P0562": ("System Voltage Low", "Warning"),
    "P0563": ("System Voltage High", "Warning"),
    # P06xx - Computer Output Circuit
    "P0600": ("Serial Communication Link Malfunction", "Critical"),
    "P0605": ("Internal Control Module Read Only Memory (ROM) Error", "Critical"),
    "P0606": ("ECM / PCM Processor Fault", "Critical"),
    # P07xx - Transmission
    "P0700": ("Transmission Control System (MIL Request)", "Warning"),
    "P0705": ("Transmission Range Sensor Circuit Malfunction (PRNDL Input)", "Warning"),
    "P0720": ("Output Speed Sensor Circuit Malfunction", "Warning"),
    "P0730": ("Incorrect Gear Ratio", "Critical"),
    "P0740": ("Torque Converter Clutch Circuit Malfunction", "Warning"),
    # Chassis (C-Codes)
    "C0035": ("Left Front Wheel Speed Sensor Circuit Malfunction", "Warning"),
    "C0040": ("Right Front Wheel Speed Sensor Circuit Malfunction", "Warning"),
    "C0045": ("Left Rear Wheel Speed Sensor Circuit Malfunction", "Warning"),
    "C0050": ("Right Rear Wheel Speed Sensor Circuit Malfunction", "Warning"),
    "C0200": ("Right Front Wheel Speed Sensor Signal Malfunction", "Warning"),
    "C0205": ("Left Front Wheel Speed Sensor Signal Malfunction", "Warning"),
    "C1201": ("Engine Control System Malfunction / ABS Inhibited", "Warning"),
    "C1234": ("Yaw Rate Sensor Malfunction", "Warning"),
    # Body (B-Codes)
    "B0001": ("Driver Frontal Stage 1 Deployment Control", "Critical"),
    "B0010": ("Passenger Frontal Stage 1 Deployment Control", "Critical"),
    "B1325": ("Device Power #1 Circuit Low Voltage", "Warning"),
    "B1342": ("ECU Internal Fault", "Critical"),
    # Network (U-Codes)
    "U0001": ("High Speed CAN Communication Bus", "Critical"),
    "U0100": ("Lost Communication With ECM/PCM 'A'", "Critical"),
    "U0101": ("Lost Communication with TCM", "Critical"),
    "U0121": ("Lost Communication With Anti-Lock Brake System (ABS) Control Module", "Critical"),
    "U0155": ("Lost Communication With Instrument Panel Cluster (IPC) Control Module", "Warning"),
    "U0401": ("Invalid Data Received From ECM/PCM 'A'", "Warning"),
    # Hyundai / Kia OEM Specific
    "P1111": ("Intake Air Temperature Sensor Circuit Intermittent High Voltage", "Warning"),
    "P1123": ("Long Term Fuel Trim Additive Air System Too Rich", "Warning"),
    "P1127": ("Long Term Fuel Trim Additive Air System Too Lean", "Warning"),
    "P1300": ("Ignition Timing Adjust Signal Circuit Failure", "Warning"),
    "P1500": ("Vehicle Speed Sensor Signal Error to TCM", "Warning"),
    "P1610": ("Immobilizer Smartra Communication Line Error", "Critical"),
    # Toyota OEM Specific
    "P1335": ("Crankshaft Position Sensor Circuit Fault During Engine Running", "Critical"),
    "P1346": ("VVT Sensor / Camshaft Position Sensor Range/Performance (Bank 1)", "Warning"),
    "P1349": ("VVT (Variable Valve Timing) System Malfunction", "Warning"),
    "P1603": ("Engine Stall History / Electronic Throttle Fault", "Critical"),
    # VAG OEM Specific
    "P1128": ("Long Term Fuel Trim Mult., Bank 1 System too Lean", "Warning"),
    "P1176": ("O2 Correction Behind Catalyst, B1 Limit Attained", "Warning"),
    "P1296": ("Cooling System Malfunction (Engine Coolant Sensor/Thermostat)", "Warning"),
    "P1602": ("Power Supply (B+) Terminal 30 Low Voltage", "Info"),
}


def lookup_dtc(code: str) -> Tuple[str, str]:
    """
    Returns (description, severity) for the given DTC code.
    If code is unknown, generates a category-based description.
    """
    clean = code.strip().upper()
    if clean in _STANDARD_DTC_MAP:
        return _STANDARD_DTC_MAP[clean]

    misfire = _misfire(clean)
    if misfire:
        return misfire
    if len(clean) == 5 and clean[0] in "PCBU":
        return (f"{_structure(clean)} ({clean})", "Critical" if clean[0] == "U" else "Warning")
    return ("Unknown Diagnostic Trouble Code", "Warning")


# SAE J2012 code structure: letter = system, 2nd character = generic (0, 2, 3 for P;
# 0, 3 for B/C/U) or manufacturer-defined (1, and 3 for some P3xxx ranges), 3rd
# character = powertrain subsystem.
_SYSTEM = {"P": "Powertrain", "C": "Chassis", "B": "Body", "U": "Network communication"}
_P_SUBSYSTEM = {
    "0": "fuel and air metering / auxiliary emission controls",
    "1": "fuel and air metering",
    "2": "fuel and air metering (injector circuit)",
    "3": "ignition system or misfire",
    "4": "auxiliary emission controls",
    "5": "vehicle speed, idle control and auxiliary inputs",
    "6": "computer and auxiliary outputs",
    "7": "transmission",
    "8": "transmission",
    "9": "transmission",
    "A": "hybrid propulsion",
}


def _structure(code: str) -> str:
    system = _SYSTEM[code[0]].lower()
    # P: 0/2 generic, 1 manufacturer, 3 = P30xx-P33xx manufacturer / P34xx-P39xx generic.
    # B/C/U: 0/3 generic, 1/2 manufacturer.
    if code[0] == "P":
        generic = code[1] in "02" or (code[1] == "3" and code[2] in "456789")
    else:
        generic = code[1] in "03"
    kind = "Generic (SAE)" if generic else "Manufacturer-specific"
    sub = _P_SUBSYSTEM.get(code[2]) if code[0] == "P" else None
    return f"{kind} {system} code: {sub}" if sub else f"{kind} {system} code"


def _misfire(code: str):
    """P0300 random/multiple misfire, P0301..P0312 = misfire in cylinder 1..12 (SAE J2012)."""
    if code == "P0300":
        return ("Random/Multiple Cylinder Misfire Detected", "Critical")
    if code.startswith("P03") and code[3:].isdigit() and 1 <= int(code[3:]) <= 12:
        return (f"Cylinder {int(code[3:])} Misfire Detected", "Critical")
    return None
