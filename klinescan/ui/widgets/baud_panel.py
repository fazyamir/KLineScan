"""
Baud Rate Control Panel.
Provides Automatic, Incremental (Auto+), and Fixed / Override modes.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)
from klinescan.core.models import BaudMode


class BaudPanel(QWidget):
    mode_changed = pyqtSignal(object, int)  # BaudMode, custom_baud

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        box = QGroupBox("Baud Rate Control")
        box_layout = QVBoxLayout(box)

        self._btn_group = QButtonGroup(self)

        # 1. Automatic
        self._rb_auto = QRadioButton("Automatic (Use Vehicle / ECU Profile Default)")
        self._rb_auto.setChecked(True)
        self._btn_group.addButton(self._rb_auto)
        box_layout.addWidget(self._rb_auto)

        # 2. Incremental / Auto+
        self._rb_incr = QRadioButton("Incremental / Auto+ (Auto-cycle: 10400 -> 9600 -> 4800 -> 15625)")
        self._btn_group.addButton(self._rb_incr)
        box_layout.addWidget(self._rb_incr)

        # 3. Fixed / Override
        fixed_layout = QHBoxLayout()
        self._rb_fixed = QRadioButton("Fixed / Override:")
        self._btn_group.addButton(self._rb_fixed)
        fixed_layout.addWidget(self._rb_fixed)

        self._combo_baud = QComboBox()
        self._combo_baud.setEditable(True)
        common_bauds = ["4800", "9600", "10400", "15625", "19200", "38400", "57600", "115200", "250000", "500000"]
        self._combo_baud.addItems(common_bauds)
        self._combo_baud.setCurrentText("10400")
        fixed_layout.addWidget(self._combo_baud)
        box_layout.addLayout(fixed_layout)

        # Status Readouts
        self._status_label = QLabel("Active: Automatic | Requested: 10400 bps | Actual: 10400 bps")
        self._status_label.setStyleSheet("color: #38bdf8; font-weight: bold; margin-top: 4px;")
        box_layout.addWidget(self._status_label)

        layout.addWidget(box)

        self._rb_auto.toggled.connect(self._emit_change)
        self._rb_incr.toggled.connect(self._emit_change)
        self._rb_fixed.toggled.connect(self._emit_change)
        self._combo_baud.currentTextChanged.connect(self._emit_change)

    def get_mode(self) -> BaudMode:
        if self._rb_auto.isChecked():
            return BaudMode.AUTOMATIC
        if self._rb_incr.isChecked():
            return BaudMode.INCREMENTAL
        return BaudMode.FIXED

    def get_custom_baud(self) -> int:
        try:
            return int(self._combo_baud.currentText())
        except ValueError:
            return 10400

    def set_readouts(self, requested: int, actual: int) -> None:
        mode_str = self.get_mode().value
        self._status_label.setText(f"Active: {mode_str} | Requested: {requested} bps | Actual: {actual} bps")

    def _emit_change(self) -> None:
        self.mode_changed.emit(self.get_mode(), self.get_custom_baud())


class BaudDialog(QDialog):
    def __init__(self, current_mode: BaudMode = BaudMode.AUTOMATIC, current_baud: int = 10400, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Baud Rate Configuration")
        self.resize(460, 220)
        layout = QVBoxLayout(self)

        self.panel = BaudPanel()
        if current_mode == BaudMode.INCREMENTAL:
            self.panel._rb_incr.setChecked(True)
        elif current_mode == BaudMode.FIXED:
            self.panel._rb_fixed.setChecked(True)
        self.panel._combo_baud.setCurrentText(str(current_baud))

        layout.addWidget(self.panel)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
