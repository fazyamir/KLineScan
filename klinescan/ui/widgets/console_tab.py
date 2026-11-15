"""
Color-Coded Monospace Raw Bus Monitor with Timestamp Deltas,
Direction Filtering, Quick Hex Macros, and Macro Buttons.
"""

from typing import List
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from klinescan.core.models import BusFrame


class ConsoleTab(QWidget):
    raw_send_requested = pyqtSignal(bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Filter & Control Bar
        filter_bar = QHBoxLayout()
        self._chk_tx = QCheckBox("TX")
        self._chk_tx.setChecked(True)
        self._chk_tx.setStyleSheet("color: #eab308; font-weight: bold;")
        filter_bar.addWidget(self._chk_tx)

        self._chk_rx = QCheckBox("RX")
        self._chk_rx.setChecked(True)
        self._chk_rx.setStyleSheet("color: #38bdf8; font-weight: bold;")
        filter_bar.addWidget(self._chk_rx)

        self._chk_info = QCheckBox("INFO")
        self._chk_info.setChecked(True)
        self._chk_info.setStyleSheet("color: #94a3b8; font-weight: bold;")
        filter_bar.addWidget(self._chk_info)

        self._chk_err = QCheckBox("ERR")
        self._chk_err.setChecked(True)
        self._chk_err.setStyleSheet("color: #ef4444; font-weight: bold;")
        filter_bar.addWidget(self._chk_err)

        filter_bar.addSpacing(15)
        filter_bar.addWidget(QLabel("Find:"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Filter hex or note...")
        filter_bar.addWidget(self._search_input)

        self._autoscroll_chk = QCheckBox("Auto-Scroll")
        self._autoscroll_chk.setChecked(True)
        filter_bar.addWidget(self._autoscroll_chk)

        self._clear_btn = QPushButton("Clear Log")
        self._clear_btn.clicked.connect(self._clear_log)
        filter_bar.addWidget(self._clear_btn)
        layout.addLayout(filter_bar)

        # Monospace Log View
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._log.setFont(font)
        self._log.setStyleSheet("background-color: #0b1120; color: #f8fafc; border: 1px solid #1e293b;")
        layout.addWidget(self._log)

        # Quick Macro Buttons Bar
        macro_bar = QHBoxLayout()
        macro_bar.addWidget(QLabel("Macros:"))
        macros = [
            ("01 00 (Supported PIDs)", "68 6A F1 01 00 C4"),
            ("01 0C (RPM)", "68 6A F1 01 0C D0"),
            ("03 (Read DTCs)", "68 6A F1 03 C6"),
            ("04 (Clear DTCs)", "68 6A F1 04 C7"),
            ("3E (TesterPresent)", "81 11 F1 3E C1"),
            ("81 (StartComm)", "81 11 F1 81 04"),
        ]
        for name, hex_str in macros:
            btn = QPushButton(name)
            btn.setStyleSheet("padding: 2px 6px; font-size: 11px;")
            btn.clicked.connect(lambda _, h=hex_str: self._set_input(h))
            macro_bar.addWidget(btn)
        macro_bar.addStretch()
        layout.addLayout(macro_bar)

        # Manual Transmit Bar
        cmd_bar = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("Raw hex bytes, e.g.: 68 6A F1 01 0C CF or 81 11 F1 81 04")
        self._input.returnPressed.connect(self._on_send)
        self._send_btn = QPushButton("Send Raw Hex")
        self._send_btn.setObjectName("primaryBtn")
        self._send_btn.clicked.connect(self._on_send)
        cmd_bar.addWidget(self._input, 1)
        cmd_bar.addWidget(self._send_btn)
        layout.addLayout(cmd_bar)

    def _clear_log(self) -> None:
        self._log.clear()

    def _set_input(self, text: str) -> None:
        self._input.setText(text)
        self._input.setFocus()

    def _on_send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        cleaned = text.replace(" ", "").replace("0x", "").replace(",", "")
        try:
            raw = bytes.fromhex(cleaned)
        except ValueError:
            QMessageBox.warning(self, "Invalid Hex", "Please enter valid space-separated hexadecimal bytes.")
            return
        self.raw_send_requested.emit(raw)

    def append_frame(self, frame: BusFrame) -> None:
        # Check filters
        d = frame.direction.upper()
        if d == "TX" and not self._chk_tx.isChecked():
            return
        if d == "RX" and not self._chk_rx.isChecked():
            return
        if d == "INFO" and not self._chk_info.isChecked():
            return
        if d == "ERR" and not self._chk_err.isChecked():
            return

        hex_str = frame.data.hex(" ").upper()
        note = f"  [{frame.note}]" if frame.note else ""
        delta_str = f"+{frame.delta_ms:>5.1f}ms"

        # Search filter
        search_term = self._search_input.text().strip().upper()
        line_text = f"{delta_str} | {d:<4} | {hex_str}{note}"
        if search_term and search_term not in line_text.upper():
            return

        self._log.appendPlainText(line_text)
        if self._autoscroll_chk.isChecked():
            self._log.moveCursor(QTextCursor.MoveOperation.End)
