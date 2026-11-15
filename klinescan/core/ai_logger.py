"""
AI-Ready Diagnostic Telemetry Logger & Export Engine (.aidiag.json + .csv + ai_prompt.md).
Captures microsecond-precision inter-sample deltas, driving phase tags, and computed sensor analytics.
"""

from collections import defaultdict
import csv
import datetime
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple
from klinescan.core.models import DiagnosticTroubleCode, EcuIdItem, FreezeFrame, LivePid
from klinescan.core.sensor_health import (
    analyze_coolant_warmup,
    analyze_fuel_trims,
    analyze_o2_sensor,
    analyze_throttle_lag,
    compute_series_statistics,
)
from klinescan.core.terminology import PID_TERMINOLOGY, evaluate_pid_health, get_dtc_plain_english


class AiDiagnosticLogger:
    def __init__(self):
        self._is_logging = False
        self._t0: float = 0.0
        self._last_t: float = 0.0
        self._current_phase: str = "Warm Idle"
        self._metadata: Dict[str, Any] = {}

        # Raw sample series: pid -> list of (t_rel_s, value, phase)
        self._samples: Dict[int, List[Tuple[float, float, str]]] = defaultdict(list)
        self._pid_names: Dict[int, str] = {}
        self._pid_units: Dict[int, str] = {}

        self._active_dtcs: List[DiagnosticTroubleCode] = []
        self._freeze_frame: Optional[FreezeFrame] = None
        self._ecu_ids: List[EcuIdItem] = []

    @property
    def is_logging(self) -> bool:
        return self._is_logging

    def set_phase(self, phase: str) -> None:
        self._current_phase = phase

    def start_session(
        self,
        metadata: Dict[str, Any],
        dtcs: Optional[List[DiagnosticTroubleCode]] = None,
        freeze_frame: Optional[FreezeFrame] = None,
        ecu_ids: Optional[List[EcuIdItem]] = None,
    ) -> None:
        self._is_logging = True
        self._t0 = time.perf_counter()
        self._last_t = self._t0
        self._samples.clear()
        self._pid_names.clear()
        self._pid_units.clear()
        self._metadata = dict(metadata)
        self._metadata["session_start_iso"] = datetime.datetime.now().isoformat()
        self._active_dtcs = list(dtcs or [])
        self._freeze_frame = freeze_frame
        self._ecu_ids = list(ecu_ids or [])

    def record_sample(self, pid: LivePid) -> None:
        if not self._is_logging:
            return
        now = time.perf_counter()
        t_rel = now - self._t0
        num_val = pid.numeric_value

        if num_val is None:
            try:
                num_val = float(pid.value)
            except (ValueError, TypeError):
                return

        self._samples[pid.pid].append((t_rel, num_val, self._current_phase))
        self._pid_names[pid.pid] = pid.name
        self._pid_units[pid.pid] = pid.unit
        self._last_t = now

    def stop_session(self) -> None:
        self._is_logging = False

    def build_diagnostic_bundle(self) -> Dict[str, Any]:
        """Calculates sensor health analytics and generates AI diagnostic bundle JSON."""
        duration_s = (self._last_t - self._t0) if self._t0 > 0 else 0.0

        # Calculate per-PID analytics
        sensor_analytics: Dict[str, Any] = {}
        for pid_code, sample_list in self._samples.items():
            vals = [s[1] for s in sample_list]
            times = [s[0] for s in sample_list]
            name = self._pid_names.get(pid_code, f"PID_0x{pid_code:02X}")
            unit = self._pid_units.get(pid_code, "")

            stats = compute_series_statistics(vals)
            # Evaluate health against terminology ranges
            status, advice = evaluate_pid_health(pid_code, stats["mean"], self._current_phase)

            sensor_analytics[f"0x{pid_code:02X}_{name}"] = {
                "pid": f"0x{pid_code:02X}",
                "name": name,
                "unit": unit,
                "sample_count": len(vals),
                "effective_hz": round(len(vals) / duration_s, 2) if duration_s > 0 else 0.0,
                "statistics": stats,
                "health_status": status,
                "clinical_observation": advice,
            }

        # Dynamic cross-sensor health checks
        dynamic_checks: Dict[str, Any] = {}

        # 1. Oxygen Sensor Analysis (0x14 / 0x15)
        o2_samples = [(s[0], s[1]) for s in self._samples.get(0x14, [])]
        if o2_samples:
            dynamic_checks["upstream_o2_sensor"] = analyze_o2_sensor(o2_samples)

        # 2. Coolant Warm-up Curve (0x05)
        coolant_samples = [(s[0], s[1]) for s in self._samples.get(0x05, [])]
        if coolant_samples:
            dynamic_checks["thermostat_warmup"] = analyze_coolant_warmup(coolant_samples)

        # 3. Fuel Trims Analysis (0x06 STFT & 0x07 LTFT & 0x0C RPM)
        stft_s = [(s[0], s[1]) for s in self._samples.get(0x06, [])]
        ltft_s = [(s[0], s[1]) for s in self._samples.get(0x07, [])]
        rpm_s = [(s[0], s[1]) for s in self._samples.get(0x0C, [])]
        if stft_s and ltft_s:
            dynamic_checks["fuel_trim_balance"] = analyze_fuel_trims(stft_s, ltft_s, rpm_s)

        # 4. Throttle Snap Lag (0x11 TPS & 0x0C RPM)
        tps_s = [(s[0], s[1]) for s in self._samples.get(0x11, [])]
        if tps_s and rpm_s:
            dynamic_checks["throttle_to_rpm_response"] = analyze_throttle_lag(tps_s, rpm_s)

        # Prepare DTC details
        dtc_details = []
        for d in self._active_dtcs:
            dtc_info = get_dtc_plain_english(d.code)
            dtc_details.append({
                "code": d.code,
                "status": d.status,
                "friendly_title": dtc_info.friendly_title,
                "severity": dtc_info.severity_badge,
                "likely_causes": dtc_info.likely_causes,
                "symptoms": dtc_info.symptoms,
            })

        bundle = {
            "version": "2.0-AI",
            "metadata": self._metadata,
            "session_duration_s": round(duration_s, 2),
            "dtcs": dtc_details,
            "ecu_identifications": [{"label": i.label, "value": i.value} for i in self._ecu_ids],
            "freeze_frame": (
                {
                    "trigger_dtc": self._freeze_frame.dtc_trigger,
                    "pids": [{"name": p.name, "value": p.value, "unit": p.unit} for p in self._freeze_frame.pids],
                }
                if self._freeze_frame
                else None
            ),
            "dynamic_sensor_health_checks": dynamic_checks,
            "sensor_analytics": sensor_analytics,
        }
        return bundle

    def export_ai_package(self, output_dir: str) -> Dict[str, str]:
        """
        Exports the complete AI package:
        1. diagnostic_bundle.json
        2. telemetry.csv
        3. ai_prompt.md
        """
        os.makedirs(output_dir, exist_ok=True)
        bundle = self.build_diagnostic_bundle()

        # 1. Save JSON
        json_path = os.path.join(output_dir, "diagnostic_bundle.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2)

        # 2. Save CSV
        csv_path = os.path.join(output_dir, "telemetry.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["t_rel_ms", "phase", "pid", "sensor_name", "value", "unit"])
            # Collate and sort by time
            all_rows = []
            for pid, s_list in self._samples.items():
                name = self._pid_names.get(pid, f"0x{pid:02X}")
                unit = self._pid_units.get(pid, "")
                for t_s, val, phase in s_list:
                    all_rows.append((round(t_s * 1000.0, 1), phase, f"0x{pid:02X}", name, val, unit))
            all_rows.sort(key=lambda r: r[0])
            for row in all_rows:
                writer.writerow(row)

        # 3. Save AI Prompt Markdown
        prompt_path = os.path.join(output_dir, "ai_prompt.md")
        prompt_content = self._generate_ai_prompt_markdown(bundle)
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt_content)

        return {"json": json_path, "csv": csv_path, "prompt": prompt_path}

    def _generate_ai_prompt_markdown(self, bundle: Dict[str, Any]) -> str:
        meta = bundle.get("metadata", {})
        dtcs = bundle.get("dtcs", [])
        checks = bundle.get("dynamic_sensor_health_checks", {})
        analytics = bundle.get("sensor_analytics", {})

        dtc_summary = ""
        if dtcs:
            for d in dtcs:
                dtc_summary += f"- **{d['code']}**: {d['friendly_title']} (Status: `{d['status']}`, Severity: `{d['severity']}`)\n"
                dtc_summary += f"  - Likely Causes: {', '.join(d.get('likely_causes', [])[:3])}\n"
        else:
            dtc_summary = "- *No fault codes logged.*\n"

        checks_summary = ""
        for name, res in checks.items():
            checks_summary += f"- **{name.replace('_', ' ').title()}**: `{res.get('status')}`\n"
            checks_summary += f"  - *Observation*: {res.get('diagnosis')}\n"

        pids_table = "| Sensor | Samples | Min | Mean | Max | Unit | Health | Note |\n|---|---|---|---|---|---|---|---|\n"
        for name, item in analytics.items():
            st = item.get("statistics", {})
            pids_table += (
                f"| {item.get('name')} | {item.get('sample_count')} | "
                f"{st.get('min')} | {st.get('mean')} | {st.get('max')} | {item.get('unit')} | "
                f"`{item.get('health_status')}` | {item.get('clinical_observation')} |\n"
            )

        return f"""# Vehicle Diagnostic Telemetry & AI Consultation Prompt

You are an expert master automotive diagnostic technician specializing in computerized engine controls, CAN networks, and sensor behavior.

Analyze the recorded telemetry and sensor dynamic response metrics below to diagnose any failing, drifting, or lazy components.

---

## 1. Vehicle & Session Overview
- **Vehicle Profile**: {meta.get('vehicle', 'Generic OBD-II')}
- **Protocol**: {meta.get('protocol', 'OBD-II')}
- **Hardware Adapter**: {meta.get('adapter', 'Standard Serial')}
- **Session Duration**: {bundle.get('session_duration_s')} seconds
- **Session Timestamp**: {meta.get('session_start_iso', 'N/A')}

---

## 2. Active Diagnostic Trouble Codes (DTCs)
{dtc_summary}

---

## 3. Dynamic Sensor Health & Response Speed Analysis
{checks_summary or '- *No dynamic tests computed.*'}

---

## 4. Telemetry Distribution & Statistical Summary
{pids_table}

---

## 5. Diagnostic Questions for the AI
1. Based on the sensor response speeds, trim shifts, and distribution statistics, which specific component is most likely failing or out of calibration?
2. Are there indicators of a **lazy O2 sensor**, **vacuum leak**, **fuel delivery restriction**, or **thermostat malfunction**?
3. What step-by-step physical inspection or multimeter pin tests should the technician perform next to verify the root cause?
"""
