"""
AI-Ready Sensor Diagnostic Analytics & Half-Failing Sensor Detection Engine.
Calculates response latencies, O2 switching rates, fuel trim shifts, and warmup curves.
"""

import math
from typing import Any, Dict, List, Optional, Tuple


def compute_series_statistics(values: List[float]) -> Dict[str, float]:
    """Computes basic distribution statistics for a numeric sample list."""
    if not values:
        return {"count": 0, "min": 0.0, "max": 0.0, "mean": 0.0, "std_dev": 0.0}

    n = len(values)
    v_min = float(min(values))
    v_max = float(max(values))
    mean = float(sum(values) / n)
    variance = sum((x - mean) ** 2 for x in values) / n if n > 1 else 0.0
    std_dev = math.sqrt(variance)

    return {
        "count": n,
        "min": round(v_min, 3),
        "max": round(v_max, 3),
        "mean": round(mean, 3),
        "std_dev": round(std_dev, 3),
    }


def analyze_o2_sensor(samples: List[Tuple[float, float]]) -> Dict[str, Any]:
    """
    Analyzes upstream Oxygen Sensor (O2S1) response speed and amplitude swing.
    samples: list of (time_s, voltage_v).
    Normal zirconia upstream O2 should cycle between < 0.2V and > 0.8V at 0.5 - 2.0 Hz.
    """
    if len(samples) < 10:
        return {
            "status": "INSUFFICIENT_DATA",
            "diagnosis": "Collect at least 10 seconds of warm engine data to analyze O2 sensor response.",
        }

    times = [s[0] for s in samples]
    volts = [s[1] for s in samples]
    stats = compute_series_statistics(volts)
    total_time = times[-1] - times[0]
    if total_time <= 0.5:
        return {"status": "INSUFFICIENT_DATA", "diagnosis": "Duration too short."}

    # Count rich-to-lean and lean-to-rich transitions across 0.45V threshold
    crossings = 0
    transition_times_ms: List[float] = []
    last_cross_time: Optional[float] = None
    was_rich = volts[0] >= 0.45

    for t, v in zip(times, volts):
        is_rich = v >= 0.45
        if is_rich != was_rich:
            crossings += 1
            if last_cross_time is not None:
                transition_times_ms.append((t - last_cross_time) * 1000.0)
            last_cross_time = t
            was_rich = is_rich

    freq_hz = crossings / (2.0 * total_time)
    avg_transition_ms = (
        sum(transition_times_ms) / len(transition_times_ms) if transition_times_ms else 0.0
    )

    # Classify Health
    v_min = stats["min"]
    v_max = stats["max"]
    v_swing = v_max - v_min

    if v_swing < 0.25:
        if stats["mean"] < 0.35:
            status = "STUCK_LEAN"
            diag = "O2 sensor voltage stays low (< 0.35V). Engine is running very lean, or sensor is shorted to ground."
        elif stats["mean"] > 0.65:
            status = "STUCK_RICH"
            diag = "O2 sensor voltage stays high (> 0.65V). Fuel mixture is stuck rich or sensor bias voltage fault."
        else:
            status = "INACTIVE_BIAS"
            diag = "O2 sensor output is flatlining around 0.45V bias. Sensor heater or circuit open."
    elif freq_hz < 0.3:
        status = "LAZY_SLOW_O2"
        diag = (
            f"Sluggish O2 sensor response! Switching frequency is only {freq_hz:.2f} Hz "
            f"(expected > 0.6 Hz). Sensor is aged/poisoned and causing slow fuel compensation."
        )
    elif v_min > 0.35 or v_max < 0.70:
        status = "DEGRADED_AMPLITUDE"
        diag = (
            f"O2 sensor amplitude is degraded (Swing: {v_swing:.2f}V, Min: {v_min:.2f}V, Max: {v_max:.2f}V). "
            f"Sensor is contaminated with soot/silicone."
        )
    else:
        status = "HEALTHY"
        diag = (
            f"Upstream O2 sensor is healthy and responsive (Rate: {freq_hz:.2f} Hz, "
            f"Swing: {v_min:.2f}V - {v_max:.2f}V, Avg Cycle: {avg_transition_ms:.0f} ms)."
        )

    return {
        "status": status,
        "switching_frequency_hz": round(freq_hz, 3),
        "min_voltage_v": round(v_min, 3),
        "max_voltage_v": round(v_max, 3),
        "voltage_swing_v": round(v_swing, 3),
        "avg_cycle_ms": round(avg_transition_ms, 1),
        "diagnosis": diag,
        "stats": stats,
    }


def analyze_coolant_warmup(samples: List[Tuple[float, float]]) -> Dict[str, Any]:
    """
    Analyzes engine coolant temperature warmup curve to diagnose stuck-open thermostat
    or overheating. samples: list of (time_s, temp_c).
    """
    if len(samples) < 5:
        return {"status": "INSUFFICIENT_DATA", "diagnosis": "Not enough temperature samples."}

    times = [s[0] for s in samples]
    temps = [s[1] for s in samples]
    start_t, end_t = temps[0], temps[-1]
    duration_min = (times[-1] - times[0]) / 60.0

    if duration_min < 0.2:
        return {"status": "INSUFFICIENT_DATA", "diagnosis": "Duration too brief for temperature trend."}

    rate_c_per_min = (end_t - start_t) / duration_min
    max_t = max(temps)

    if max_t > 105.0:
        status = "OVERHEATING"
        diag = f"CRITICAL: Engine coolant reached {max_t:.1f}°C! Risk of cylinder head warping or blown gasket."
    elif start_t < 65.0 and end_t < 72.0 and duration_min > 8.0:
        status = "STUCK_OPEN_THERMOSTAT"
        diag = (
            f"Thermostat appears stuck open. Engine ran for {duration_min:.1f} minutes but only reached {end_t:.1f}°C "
            f"(expected 85°C - 95°C). Causes poor heater output, fuel waste, and code P0128."
        )
    elif end_t >= 82.0:
        status = "HEALTHY_CLOSED_LOOP"
        diag = f"Engine coolant reached normal operating temperature ({end_t:.1f}°C) and stabilized."
    else:
        status = "WARMING_UP"
        diag = f"Coolant warming at {rate_c_per_min:.1f}°C/min (Currently at {end_t:.1f}°C)."

    return {
        "status": status,
        "initial_temp_c": round(start_t, 1),
        "current_temp_c": round(end_t, 1),
        "warmup_rate_c_per_min": round(rate_c_per_min, 2),
        "diagnosis": diag,
    }


def analyze_fuel_trims(
    stft_samples: List[Tuple[float, float]],
    ltft_samples: List[Tuple[float, float]],
    rpm_samples: List[Tuple[float, float]],
) -> Dict[str, Any]:
    """
    Analyzes STFT + LTFT fuel trims separated into Idle vs. Load/Cruise.
    Distinguishes intake vacuum leaks from dirty MAF or low fuel pressure.
    """
    if not stft_samples or not ltft_samples:
        return {"status": "INSUFFICIENT_DATA", "diagnosis": "Fuel trim data missing."}

    # Match by approximate time
    idle_totals: List[float] = []
    cruise_totals: List[float] = []

    # Map time -> rpm
    rpm_by_t = {round(s[0], 1): s[1] for s in rpm_samples}

    for (t_stft, stft), (t_ltft, ltft) in zip(stft_samples, ltft_samples):
        total = stft + ltft
        rpm = rpm_by_t.get(round(t_stft, 1), 750.0)
        if rpm < 1050.0:
            idle_totals.append(total)
        elif rpm > 1800.0:
            cruise_totals.append(total)

    idle_avg = sum(idle_totals) / len(idle_totals) if idle_totals else 0.0
    cruise_avg = sum(cruise_totals) / len(cruise_totals) if cruise_totals else 0.0

    if idle_avg > 12.0 and cruise_avg < 5.0:
        status = "VACUUM_LEAK"
        diag = (
            f"Intake vacuum leak detected! Fuel trims are significantly positive at idle ({idle_avg:+.1f}%), "
            f"but improve toward normal under throttle/cruise ({cruise_avg:+.1f}%). Check intake manifold gaskets and PCV hoses."
        )
    elif idle_avg > 10.0 and cruise_avg > 10.0:
        status = "LEAN_ALL_RANGES"
        diag = (
            f"Engine is running lean across all engine speeds (Idle: {idle_avg:+.1f}%, Cruise: {cruise_avg:+.1f}%). "
            f"Likely contaminated Mass Air Flow (MAF) sensor or weak fuel delivery (low pump pressure)."
        )
    elif idle_avg < -10.0 or cruise_avg < -10.0:
        status = "RUNNING_RICH"
        diag = (
            f"Engine is pulling significant fuel (Idle: {idle_avg:+.1f}%, Cruise: {cruise_avg:+.1f}%). "
            f"Check for leaking fuel injector, stuck EVAP purge valve, or high fuel rail pressure."
        )
    else:
        status = "NORMAL_TRIMS"
        diag = f"Fuel trim adjustments are within normal healthy tolerances (Idle: {idle_avg:+.1f}%, Cruise: {cruise_avg:+.1f}%)."

    return {
        "status": status,
        "idle_total_trim_pct": round(idle_avg, 2),
        "cruise_total_trim_pct": round(cruise_avg, 2),
        "diagnosis": diag,
    }


def analyze_throttle_lag(
    tps_samples: List[Tuple[float, float]],
    rpm_samples: List[Tuple[float, float]],
) -> Dict[str, Any]:
    """
    Measures cross-correlation lag between a sudden throttle snap (> 15% delta) and RPM climb.
    """
    if len(tps_samples) < 10 or len(rpm_samples) < 10:
        return {"status": "INSUFFICIENT_DATA", "diagnosis": "Need more throttle step samples."}

    # Find maximum positive slope in TPS (snap throttle event)
    max_tps_step_time = None
    for i in range(1, len(tps_samples)):
        dt = tps_samples[i][0] - tps_samples[i - 1][0]
        dv = tps_samples[i][1] - tps_samples[i - 1][1]
        if dt > 0 and (dv / dt) > 75.0 and dv > 12.0:  # Rapid throttle blip
            max_tps_step_time = tps_samples[i][0]
            break

    if max_tps_step_time is None:
        return {
            "status": "NO_SNAP_DETECTED",
            "diagnosis": "No rapid snap throttle event was detected. Perform a quick throttle blip while parked in neutral.",
        }

    # Find time when RPM rises by at least 200 RPM above pre-step RPM
    pre_rpm = [s[1] for s in rpm_samples if s[0] <= max_tps_step_time]
    baseline_rpm = pre_rpm[-1] if pre_rpm else 750.0

    rpm_rise_time = None
    for s in rpm_samples:
        if s[0] >= max_tps_step_time and s[1] >= (baseline_rpm + 200.0):
            rpm_rise_time = s[0]
            break

    if rpm_rise_time is None:
        return {"status": "NO_RPM_RESPONSE", "diagnosis": "RPM did not rise following throttle input."}

    lag_ms = (rpm_rise_time - max_tps_step_time) * 1000.0

    if lag_ms > 350.0:
        status = "SLUGGISH_RESPONSE"
        diag = (
            f"Excessive throttle-to-RPM response lag ({lag_ms:.0f} ms). "
            f"Check for electronic throttle body carbon buildup, clogged catalytic converter, or weak fuel delivery."
        )
    else:
        status = "CRISP_RESPONSE"
        diag = f"Crisp engine throttle response time ({lag_ms:.0f} ms from pedal snap to RPM rise)."

    return {
        "status": status,
        "response_lag_ms": round(lag_ms, 1),
        "diagnosis": diag,
    }
