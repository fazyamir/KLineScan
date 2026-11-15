"""
Persistent Status Strip with Styled Glowing LEDs, Active OBD-II Pin Badge,
Baud Rate Readout, and Quick Hardware Controls.
"""

from typing import Dict
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)


class LedIndicator(QWidget):
    def __init__(self, label: str, on_color: str, parent=None):
        super().__init__(parent)
        self._on_color = on_color
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(4)

        self._dot = QFrame()
        self._dot.setFixedSize(11, 11)
        self._text = QLabel(label)
        self._text.setStyleSheet("font-size: 11px; font-weight: bold; color: #94a3b8;")
        layout.addWidget(self._dot)
        layout.addWidget(self._text)
        self.set_state(False)

    def set_state(self, on: bool) -> None:
        if on:
            self._dot.setStyleSheet(
                f"background-color: {self._on_color}; border: 1px solid #ffffff; "
                f"border-radius: 5px;"
            )
            self._text.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {self._on_color};")
        else:
            self._dot.setStyleSheet("background-color: #1e293b; border: 1px solid #475569; border-radius: 5px;")
            self._text.setStyleSheet("font-size: 11px; font-weight: bold; color: #64748b;")


class LedPanel(QWidget):
    echo_toggled = pyqtSignal(bool)
    l_line_toggled = pyqtSignal(bool)
    break_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(10)

        # LEDs
        self._leds: Dict[str, LedIndicator] = {
            "iface": LedIndicator("IFACE", "#38bdf8"),
            "connected": LedIndicator("LINK", "#22c55e"),
            "can": LedIndicator("CAN", "#f97316"),
            "tx": LedIndicator("TX", "#eab308"),
            "rx": LedIndicator("RX", "#06b6d4"),
            "break": LedIndicator("BRK", "#ef4444"),
            "echo": LedIndicator("ECHO", "#a855f7"),
            "rec": LedIndicator("REC", "#ef4444"),
            "play": LedIndicator("PLAY", "#22c55e"),
        }
        for led in self._leds.values():
            layout.addWidget(led)

        for name in ("iface", "break", "echo", "rec", "play"):
            self._leds[name].hide()

        # Active OBD Pin Badge
        self._pin_badge = QLabel("PIN 7 [K-LINE]")
        self._pin_badge.setStyleSheet(
            "background-color: #1e293b; color: #38bdf8; border: 1px solid #334155; "
            "border-radius: 4px; padding: 2px 8px; font-weight: bold; font-size: 11px;"
        )
        layout.addWidget(self._pin_badge)

        # Baud Rate Readout
        self._baud_label = QLabel("BAUD: 10400 / 10400")
        self._baud_label.setStyleSheet(
            "background-color: #1e293b; color: #a5f3fc; border: 1px solid #334155; "
            "border-radius: 4px; padding: 2px 8px; font-family: monospace; font-size: 11px;"
        )
        layout.addWidget(self._baud_label)

        layout.addStretch()

        # Hardware Action Controls
        self._echo_chk = QCheckBox("Echo Cancel")
        self._echo_chk.setChecked(True)
        self._echo_chk.setStyleSheet("font-size: 11px; color: #94a3b8;")
        self._echo_chk.toggled.connect(self._on_echo_toggled)
        layout.addWidget(self._echo_chk)

        self._l_line_btn = QPushButton("L-Line (Pin 15)")
        self._l_line_btn.setCheckable(True)
        self._l_line_btn.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self._l_line_btn.toggled.connect(self.l_line_toggled.emit)
        layout.addWidget(self._l_line_btn)

        self._break_btn = QPushButton("K-Break")
        self._break_btn.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self._break_btn.clicked.connect(self.break_requested.emit)
        layout.addWidget(self._break_btn)

        self.set_led("echo", True)

    def set_led(self, name: str, on: bool) -> None:
        if name in self._leds:
            self._leds[name].set_state(on)

    def set_pin(self, pin: int, desc: str = "") -> None:
        label = f"PIN {pin}"
        if desc:
            label += f" [{desc}]"
        elif pin == 7:
            label += " [K-LINE ENG/AT]"
        elif pin == 8:
            label += " [ABS/ESP]"
        elif pin in (6, 14):
            label += " [CAN BUS]"
        elif pin == 1:
            label += " [L-LINE/SCS]"
        self._pin_badge.setText(label)

    def set_baud(self, requested: int, actual: int) -> None:
        self._baud_label.setText(f"BAUD: {requested} / {actual}")

    def _on_echo_toggled(self, checked: bool) -> None:
        self.set_led("echo", checked)
        self.echo_toggled.emit(checked)
