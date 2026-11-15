"""
Modern Live Gauge Dashboard View with Radial Arc Gauges,
Vehicle Health Summary Card, Driving Phase Taggers, and AI Export Button.
"""

import math
from typing import Dict, List, Optional
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from klinescan.core.models import LivePid


class ArcGaugeWidget(QWidget):
    """Sleek modern radial arc gauge with dynamic needle and digital readout."""

    def __init__(
        self,
        title: str,
        unit: str,
        min_val: float = 0.0,
        max_val: float = 8000.0,
        warn_val: float = 6500.0,
        parent=None,
    ):
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self.min_val = min_val
        self.max_val = max_val
        self.warn_val = warn_val
        self.current_val = min_val
        self.setMinimumSize(160, 160)

    def set_value(self, val: float) -> None:
        self.current_val = max(self.min_val, min(self.max_val, val))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        side = min(w, h)
        margin = 14
        rect = QRectF(margin, margin, side - 2 * margin, side - 2 * margin)

        # Background track arc (240 degrees from 150° to 390°)
        start_angle = 150 * 16
        span_angle = -240 * 16

        bg_pen = QPen(QColor("#1e293b"), 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(bg_pen)
        painter.drawArc(rect, start_angle, span_angle)

        # Active value arc
        val_pct = (self.current_val - self.min_val) / (self.max_val - self.min_val) if self.max_val > self.min_val else 0.0
        active_span = int(-240 * val_pct * 16)

        color_str = "#ef4444" if self.current_val >= self.warn_val else "#38bdf8"
        active_pen = QPen(QColor(color_str), 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(active_pen)
        painter.drawArc(rect, start_angle, active_span)

        # Value Text Center
        painter.setPen(QColor("#f8fafc"))
        val_font = QFont("Consolas", 18, QFont.Weight.Bold)
        painter.setFont(val_font)
        center_text = f"{self.current_val:.0f}" if self.max_val > 100 else f"{self.current_val:.1f}"
        painter.drawText(QRectF(0, side * 0.38, w, 30), Qt.AlignmentFlag.AlignCenter, center_text)

        # Unit Text
        painter.setPen(QColor("#94a3b8"))
        unit_font = QFont("Segoe UI", 10)
        painter.setFont(unit_font)
        painter.drawText(QRectF(0, side * 0.58, w, 20), Qt.AlignmentFlag.AlignCenter, self.unit)

        # Title at Bottom
        painter.setPen(QColor("#38bdf8"))
        title_font = QFont("Segoe UI", 11, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.drawText(QRectF(0, side * 0.78, w, 24), Qt.AlignmentFlag.AlignCenter, self.title)


class TemperatureCardWidget(QFrame):
    """Bar temperature indicator with blue cold, green nominal, and red hot zones."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.temp_val = 0.0
        self.setStyleSheet(
            "background-color: #0b1120; border: 1px solid #1e293b; border-radius: 8px; padding: 10px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet("font-size: 11px; color: #94a3b8; font-weight: bold;")
        layout.addWidget(self._title_label)

        self._val_label = QLabel("-- °C")
        self._val_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #f8fafc; font-family: Consolas;")
        layout.addWidget(self._val_label)

        self._status_chip = QLabel("COLD")
        self._status_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_chip.setStyleSheet("background-color: #1e3a5f; color: #93c5fd; border-radius: 4px; padding: 2px; font-size: 10px; font-weight: bold;")
        layout.addWidget(self._status_chip)

    def set_temperature(self, temp_c: float) -> None:
        self.temp_val = temp_c
        self._val_label.setText(f"{temp_c:.0f} °C")
        if temp_c < 70.0:
            self._status_chip.setText("COLD (WARMING)")
            self._status_chip.setStyleSheet("background-color: #1e3a5f; color: #93c5fd; border-radius: 4px; padding: 2px; font-size: 10px; font-weight: bold;")
        elif temp_c <= 98.0:
            self._status_chip.setText("OPTIMAL")
            self._status_chip.setStyleSheet("background-color: #064e3b; color: #6ee7b7; border-radius: 4px; padding: 2px; font-size: 10px; font-weight: bold;")
        else:
            self._status_chip.setText("HOT / WARNING")
            self._status_chip.setStyleSheet("background-color: #7f1d1d; color: #fca5a5; border-radius: 4px; padding: 2px; font-size: 10px; font-weight: bold;")


class DashboardTab(QWidget):
    full_scan_requested = pyqtSignal()
    export_ai_requested = pyqtSignal()
    phase_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Top Banner: Vehicle Health Scorecard & Action Buttons
        top_card = QFrame()
        top_card.setStyleSheet(
            "background-color: #0b1120; border: 1px solid #1e293b; border-radius: 8px; padding: 10px;"
        )
        tc_layout = QHBoxLayout(top_card)
        tc_layout.setContentsMargins(8, 8, 8, 8)

        # Health Badge
        self._health_badge = QLabel("SYSTEM HEALTHY")
        self._health_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._health_badge.setStyleSheet(
            "background-color: #064e3b; color: #22c55e; border: 1px solid #16a34a; "
            "border-radius: 6px; padding: 6px 14px; font-size: 14px; font-weight: bold;"
        )
        tc_layout.addWidget(self._health_badge)

        self._health_summary = QLabel("0 DTC Faults &bull; All Readiness Monitors Passed &bull; Battery 14.1V")
        self._health_summary.setStyleSheet("color: #cbd5e1; font-size: 12px; margin-left: 8px;")
        tc_layout.addWidget(self._health_summary, 1)

        self._scan_btn = QPushButton("Full Health Scan")
        self._scan_btn.setObjectName("primaryBtn")
        self._scan_btn.clicked.connect(self.full_scan_requested.emit)
        tc_layout.addWidget(self._scan_btn)

        self._ai_export_btn = QPushButton("Export for AI Diagnosis")
        self._ai_export_btn.setObjectName("successBtn")
        self._ai_export_btn.clicked.connect(self.export_ai_requested.emit)
        tc_layout.addWidget(self._ai_export_btn)

        main_layout.addWidget(top_card)

        # Phase Tagger Bar (for AI training & sensor anomaly tagging)
        phase_box = QGroupBox("Diagnostic Phase Tagger (Tags Telemetry in Real-Time)")
        pb_layout = QHBoxLayout(phase_box)
        pb_layout.setContentsMargins(6, 6, 6, 6)

        self._phase_group = QButtonGroup(self)
        phases = [
            ("Cold Start", "Cold Start"),
            ("Warm Idle", "Warm Idle"),
            ("Steady Cruise (2500 RPM)", "Steady Cruise (2500 RPM)"),
            ("Snap Throttle", "Snap Throttle"),
            ("Decel Fuel Cut", "Decel Fuel Cut"),
        ]
        for idx, (label, val) in enumerate(phases):
            rb = QRadioButton(label)
            if idx == 1:
                rb.setChecked(True)
            self._phase_group.addButton(rb)
            rb.toggled.connect(lambda checked, v=val: self._on_phase_toggled(v, checked))
            pb_layout.addWidget(rb)

        main_layout.addWidget(phase_box)

        # Gauge Grid
        grid_container = QWidget()
        grid = QGridLayout(grid_container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)

        # Gauge 1: Tachometer (RPM)
        self._gauge_rpm = ArcGaugeWidget("ENGINE RPM", "rpm", min_val=0, max_val=8000, warn_val=6500)
        grid.addWidget(self._gauge_rpm, 0, 0)

        # Gauge 2: Speedometer
        self._gauge_spd = ArcGaugeWidget("VEHICLE SPEED", "km/h", min_val=0, max_val=220, warn_val=160)
        grid.addWidget(self._gauge_spd, 0, 1)

        # Gauge 3: Engine Load
        self._gauge_load = ArcGaugeWidget("CALCULATED LOAD", "%", min_val=0, max_val=100, warn_val=85)
        grid.addWidget(self._gauge_load, 0, 2)

        # Temperature Cards Column
        temp_col = QVBoxLayout()
        self._card_coolant = TemperatureCardWidget("COOLANT TEMP (ECT)")
        self._card_iat = TemperatureCardWidget("INTAKE AIR TEMP (IAT)")
        temp_col.addWidget(self._card_coolant)
        temp_col.addWidget(self._card_iat)
        grid.addLayout(temp_col, 0, 3)

        # Bottom Row Cards: Voltage, Fuel Trim, Throttle
        bottom_cards = QHBoxLayout()
        self._card_volt = TemperatureCardWidget("BATTERY / CHARGING")
        self._card_trim = TemperatureCardWidget("TOTAL FUEL TRIM")
        self._card_tps = TemperatureCardWidget("THROTTLE OPENING")

        self._card_volt._status_chip.setText("CHARGING (ALT OK)")
        self._card_trim._status_chip.setText("CLOSED LOOP STABLE")
        self._card_tps._status_chip.setText("IDLE STOP")

        bottom_cards.addWidget(self._card_volt)
        bottom_cards.addWidget(self._card_trim)
        bottom_cards.addWidget(self._card_tps)
        grid.addLayout(bottom_cards, 1, 0, 1, 4)

        main_layout.addWidget(grid_container, 1)

    def _on_phase_toggled(self, phase_name: str, checked: bool) -> None:
        if checked:
            self.phase_selected.emit(phase_name)

    def update_pids(self, pids: List[LivePid]) -> None:
        stft = 0.0
        ltft = 0.0
        has_trims = False

        for p in pids:
            num = p.numeric_value
            if num is None:
                continue

            if p.pid == 0x0C:  # RPM
                self._gauge_rpm.set_value(num)
            elif p.pid == 0x0D:  # Speed
                self._gauge_spd.set_value(num)
            elif p.pid == 0x04:  # Load
                self._gauge_load.set_value(num)
            elif p.pid == 0x05:  # Coolant
                self._card_coolant.set_temperature(num)
            elif p.pid == 0x0F:  # IAT
                self._card_iat.set_temperature(num)
            elif p.pid == 0x11:  # Throttle
                self._card_tps._val_label.setText(f"{num:.1f} %")
            elif p.pid == 0x42:  # Battery Voltage
                self._card_volt._val_label.setText(f"{num:.2f} V")
                if num < 12.0:
                    self._card_volt._status_chip.setText("LOW BATTERY")
                    self._card_volt._status_chip.setStyleSheet("background-color: #7f1d1d; color: #fca5a5;")
                elif num >= 13.5:
                    self._card_volt._status_chip.setText("ALTERNATOR ACTIVE")
                    self._card_volt._status_chip.setStyleSheet("background-color: #064e3b; color: #6ee7b7;")
            elif p.pid == 0x06:
                stft = num
                has_trims = True
            elif p.pid == 0x07:
                ltft = num
                has_trims = True

        if has_trims:
            total_trim = stft + ltft
            self._card_trim._val_label.setText(f"{total_trim:+.1f} %")

    def set_health_status(self, num_dtcs: int, status_str: str) -> None:
        if num_dtcs == 0:
            self._health_badge.setText("SYSTEM HEALTHY")
            self._health_badge.setStyleSheet(
                "background-color: #064e3b; color: #22c55e; border: 1px solid #16a34a; "
                "border-radius: 6px; padding: 6px 14px; font-size: 14px; font-weight: bold;"
            )
            self._health_summary.setText("0 Fault Codes Detected &bull; All Subsystems Nominal")
        elif num_dtcs <= 2:
            self._health_badge.setText("ATTENTION REQUIRED")
            self._health_badge.setStyleSheet(
                "background-color: #451a03; color: #f59e0b; border: 1px solid #d97706; "
                "border-radius: 6px; padding: 6px 14px; font-size: 14px; font-weight: bold;"
            )
            self._health_summary.setText(f"{num_dtcs} Diagnostic Trouble Code(s) &bull; Check Fault Codes tab")
        else:
            self._health_badge.setText("CRITICAL FAULTS")
            self._health_badge.setStyleSheet(
                "background-color: #450a0a; color: #ef4444; border: 1px solid #dc2626; "
                "border-radius: 6px; padding: 6px 14px; font-size: 14px; font-weight: bold;"
            )
            self._health_summary.setText(f"{num_dtcs} Diagnostic Faults Active &bull; Service Required")
