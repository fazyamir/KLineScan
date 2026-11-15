"""Diagnostic Health Report generator (HTML output with printable CSS)."""

import datetime
from html import escape as _esc
from typing import List, Optional
from klinescan.core.models import DiagnosticTroubleCode, EcuIdItem, FreezeFrame, LivePid, ReadinessMonitor


def generate_html_report(
    vehicle_label: str,
    ecu_name: str,
    ecu_ids: List[EcuIdItem],
    dtcs: List[DiagnosticTroubleCode],
    freeze_frame: Optional[FreezeFrame] = None,
    readiness: Optional[List[ReadinessMonitor]] = None,
    live_pids: Optional[List[LivePid]] = None,
) -> str:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ECU IDs rows
    id_rows = "".join(
        f"<tr><td><strong>{_esc(str(item.label))}</strong></td><td><code>{_esc(str(item.value))}</code></td></tr>"
        for item in ecu_ids
    ) or "<tr><td colspan='2'>No ECU identification items recorded</td></tr>"

    # DTC rows
    dtc_rows = ""
    if dtcs:
        for d in dtcs:
            badge_class = "badge-danger" if d.severity == "Critical" else "badge-warning"
            dtc_rows += f"""
            <tr>
                <td><span class='badge {badge_class}'>{_esc(str(d.code))}</span></td>
                <td>{_esc(str(d.status))}</td>
                <td><code>{_esc(str(d.raw_hex))}</code></td>
                <td>{_esc(str(d.description))}</td>
            </tr>
            """
    else:
        dtc_rows = "<tr><td colspan='4' style='color: #22c55e;'>No Fault Codes Detected (System Healthy)</td></tr>"

    # Freeze Frame rows
    ff_rows = ""
    if freeze_frame and freeze_frame.pids:
        for p in freeze_frame.pids:
            ff_rows += f"<tr><td>{_esc(str(p.name))} (0x{p.pid:02X})</td><td><strong>{_esc(str(p.value))} {_esc(str(p.unit))}</strong></td><td><code>{_esc(str(p.raw_hex))}</code></td></tr>"
    else:
        ff_rows = "<tr><td colspan='3'>No Freeze Frame snapshot recorded</td></tr>"

    # Readiness rows
    rd_rows = ""
    if readiness:
        for r in readiness:
            status_text = "PASSED" if r.complete else "NOT READY"
            badge = "badge-success" if r.complete else "badge-secondary"
            rd_rows += f"<tr><td>{_esc(str(r.name))}</td><td><span class='badge {badge}'>{status_text}</span></td></tr>"
    else:
        rd_rows = "<tr><td colspan='2'>Readiness data not polled</td></tr>"

    # Live PIDs sample
    pid_rows = ""
    if live_pids:
        for p in live_pids:
            pid_rows += f"<tr><td>0x{p.pid:02X}</td><td>{_esc(str(p.name))}</td><td><strong>{_esc(str(p.value))} {_esc(str(p.unit))}</strong></td><td><code>{_esc(str(p.raw_hex))}</code></td></tr>"
    else:
        pid_rows = "<tr><td colspan='4'>No telemetry samples</td></tr>"

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>KLineScan Diagnostic Report - {_esc(str(vehicle_label))}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #f8fafc;
            color: #1e293b;
            margin: 0;
            padding: 30px;
        }}
        .header {{
            border-bottom: 2px solid #38bdf8;
            padding-bottom: 15px;
            margin-bottom: 25px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        h1 {{ margin: 0; color: #0f172a; font-size: 24px; }}
        .meta {{ color: #64748b; font-size: 13px; }}
        .card {{
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 18px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }}
        h2 {{
            margin-top: 0;
            font-size: 16px;
            color: #0f172a;
            border-bottom: 1px solid #f1f5f9;
            padding-bottom: 8px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}
        th, td {{
            text-align: left;
            padding: 8px 12px;
            border-bottom: 1px solid #f1f5f9;
        }}
        th {{
            background-color: #f8fafc;
            color: #475569;
            font-weight: 600;
        }}
        code {{
            background: #f1f5f9;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: monospace;
        }}
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
        }}
        .badge-danger {{ background: #fee2e2; color: #dc2626; }}
        .badge-warning {{ background: #fef3c7; color: #d97706; }}
        .badge-success {{ background: #dcfce7; color: #16a34a; }}
        .badge-secondary {{ background: #f1f5f9; color: #64748b; }}
        @media print {{
            body {{ background: white; padding: 0; }}
            .card {{ box-shadow: none; border: 1px solid #ccc; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>KLineScan Diagnostic Health Report</h1>
            <div class="meta">Target: <strong>{_esc(str(vehicle_label))}</strong> &bull; Module: <strong>{_esc(str(ecu_name))}</strong></div>
        </div>
        <div class="meta" style="text-align: right;">
            <div>Report Date: {now_str}</div>
            <div>Generated by KLineScan Suite</div>
        </div>
    </div>

    <div class="card">
        <h2>ECU Identification & VIN</h2>
        <table>
            <thead><tr><th>Parameter</th><th>Value</th></tr></thead>
            <tbody>{id_rows}</tbody>
        </table>
    </div>

    <div class="card">
        <h2>Diagnostic Trouble Codes (DTC)</h2>
        <table>
            <thead><tr><th>Code</th><th>Status</th><th>Raw Hex</th><th>Description</th></tr></thead>
            <tbody>{dtc_rows}</tbody>
        </table>
    </div>

    <div class="card">
        <h2>Freeze Frame Snapshot</h2>
        <table>
            <thead><tr><th>Parameter</th><th>Value</th><th>Raw Hex</th></tr></thead>
            <tbody>{ff_rows}</tbody>
        </table>
    </div>

    <div class="card">
        <h2>I/M Readiness Status</h2>
        <table>
            <thead><tr><th>System Monitor</th><th>Status</th></tr></thead>
            <tbody>{rd_rows}</tbody>
        </table>
    </div>

    <div class="card">
        <h2>Telemetry Sample (Live PIDs)</h2>
        <table>
            <thead><tr><th>PID</th><th>Parameter</th><th>Value</th><th>Raw Hex</th></tr></thead>
            <tbody>{pid_rows}</tbody>
        </table>
    </div>
</body>
</html>
"""
