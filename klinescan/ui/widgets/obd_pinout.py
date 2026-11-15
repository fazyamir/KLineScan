"""
Interactive 16-Pin OBD-II (SAE J1962) Connector Visualizer.
Highlights active diagnostic pins (Pin 7 K-Line, Pin 8 ABS, Pin 6/14 CAN, Pin 1 SCS, etc.).
"""

from typing import Dict
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

PIN_DESCRIPTIONS: Dict[int, str] = {
    1: "Manufacturer Discretion (e.g. Mitsubishi Diag/SCS / ABS / L-Line)",
    2: "SAE J1850 Bus+ (VPW / PWM)",
    3: "Manufacturer Discretion (e.g. Ethernet TX+ / Secondary K-Line)",
    4: "Chassis Ground",
    5: "Signal Ground",
    6: "CAN High (ISO 15765-4 / SAE J2284)",
    7: "K-Line ISO 9141-2 / ISO 14230-4 (Engine / Transmission)",
    8: "Manufacturer Discretion (e.g. Hyundai/Kia ABS / BMW Second K-Line)",
    9: "Manufacturer Discretion (e.g. Cruise Control / Secondary K-Line)",
    10: "SAE J1850 Bus- (PWM)",
    11: "Manufacturer Discretion (e.g. Secondary Bus)",
    12: "Manufacturer Discretion (e.g. Hyundai Airbag SRS / Mercedes K-Line)",
    13: "Manufacturer Discretion (e.g. Toyota TC / ABS flash code pin)",
    14: "CAN Low (ISO 15765-4 / SAE J2284) or Honda SRS K-Line",
    15: "L-Line ISO 9141-2 / ISO 14230-4 (Initialization / Secondary)",
    16: "Battery Positive (+12V Unswitched)",
}


class PinWidget(QPushButton):
    def __init__(self, pin_num: int, parent=None):
        super().__init__(str(pin_num), parent)
        self.pin_num = pin_num
        self.setFixedSize(36, 36)
        self.is_active = False
        self.update_style()

    def set_active(self, active: bool) -> None:
        self.is_active = active
        self.update_style()

    def update_style(self) -> None:
        if self.is_active:
            self.setStyleSheet(
                "background-color: #38bdf8; color: #0f172a; border: 2px solid #ffffff; "
                "border-radius: 18px; font-weight: bold; font-size: 13px;"
            )
        elif self.pin_num in (4, 5):
            self.setStyleSheet(
                "background-color: #334155; color: #94a3b8; border: 1px solid #475569; "
                "border-radius: 18px; font-weight: bold; font-size: 11px;"
            )
        elif self.pin_num == 16:
            self.setStyleSheet(
                "background-color: #7f1d1d; color: #fca5a5; border: 1px solid #ef4444; "
                "border-radius: 18px; font-weight: bold; font-size: 11px;"
            )
        elif self.pin_num in (6, 14):
            self.setStyleSheet(
                "background-color: #1e3a5f; color: #93c5fd; border: 1px solid #3b82f6; "
                "border-radius: 18px; font-weight: bold; font-size: 11px;"
            )
        else:
            self.setStyleSheet(
                "background-color: #1e293b; color: #cbd5e1; border: 1px solid #475569; "
                "border-radius: 18px; font-weight: bold; font-size: 11px;"
            )


class ObdPinoutWidget(QWidget):
    pin_clicked = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        header = QLabel("16-Pin OBD-II (SAE J1962) Connector")
        header.setStyleSheet("font-weight: bold; font-size: 13px; color: #38bdf8;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # Connector Housing Frame
        housing = QFrame()
        housing.setStyleSheet(
            "background-color: #0b1120; border: 2px solid #334155; "
            "border-radius: 12px; padding: 12px;"
        )
        housing_layout = QVBoxLayout(housing)

        # Row 1 (Pins 1 - 8)
        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(8)
        row1_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pins: Dict[int, PinWidget] = {}
        for p in range(1, 9):
            btn = PinWidget(p)
            btn.clicked.connect(lambda _, pin=p: self._on_pin_clicked(pin))
            self.pins[p] = btn
            row1_layout.addWidget(btn)
        housing_layout.addLayout(row1_layout)

        # Row 2 (Pins 9 - 16)
        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(8)
        row2_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for p in range(9, 17):
            btn = PinWidget(p)
            btn.clicked.connect(lambda _, pin=p: self._on_pin_clicked(pin))
            self.pins[p] = btn
            row2_layout.addWidget(btn)
        housing_layout.addLayout(row2_layout)

        layout.addWidget(housing)

        # Pin info banner
        self.info_label = QLabel("Click a pin to view description. Active pin is highlighted in cyan.")
        self.info_label.setStyleSheet(
            "background-color: #1e293b; color: #e2e8f0; border: 1px solid #334155; "
            "border-radius: 6px; padding: 8px; font-size: 11px;"
        )
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

    def set_active_pin(self, pin: int) -> None:
        for p, widget in self.pins.items():
            widget.set_active(p == pin)
        desc = PIN_DESCRIPTIONS.get(pin, "Custom Diagnostic Pin")
        self.info_label.setText(f"Active Diagnostic Pin: <strong>Pin {pin}</strong> — {desc}")

    def _on_pin_clicked(self, pin: int) -> None:
        desc = PIN_DESCRIPTIONS.get(pin, "Custom Pin")
        self.info_label.setText(f"<strong>Pin {pin}</strong>: {desc}")
        self.pin_clicked.emit(pin, desc)


class ObdPinoutDialog(QDialog):
    def __init__(self, active_pin: int = 7, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OBD-II 16-Pin Connector Inspector")
        self.resize(500, 260)
        layout = QVBoxLayout(self)
        self.pinout = ObdPinoutWidget()
        self.pinout.set_active_pin(active_pin)
        layout.addWidget(self.pinout)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
