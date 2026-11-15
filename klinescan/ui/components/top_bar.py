"""Compact command bar for connection, vehicle, recording and primary action."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget


class TopBar(QFrame):
    setup_requested = pyqtSignal()
    vehicle_requested = pyqtSignal()
    record_requested = pyqtSignal()
    primary_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(8)

        self._connection = QPushButton("Disconnected  \u00b7  Setup")
        self._connection.setObjectName("connectionPill")
        self._connection.setProperty("state", "offline")
        self._connection.setCursor(Qt.CursorShape.PointingHandCursor)
        self._connection.clicked.connect(self.setup_requested.emit)
        layout.addWidget(self._connection)

        self._vehicle = QPushButton("No vehicle selected  \u203a")
        self._vehicle.setObjectName("vehicleChip")
        self._vehicle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._vehicle.clicked.connect(self.vehicle_requested.emit)
        layout.addWidget(self._vehicle)

        layout.addStretch(1)

        self._record = QPushButton("REC")
        self._record.setObjectName("recordButton")
        self._record.setCheckable(True)
        self._record.setCursor(Qt.CursorShape.PointingHandCursor)
        self._record.clicked.connect(self.record_requested.emit)
        layout.addWidget(self._record)

        self._primary = QPushButton("Connect")
        self._primary.setObjectName("primaryBtn")
        self._primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self._primary.clicked.connect(self.primary_requested.emit)
        layout.addWidget(self._primary)

        self._protocol = QLabel("")
        self._protocol.setObjectName("chip")
        self._protocol.hide()
        layout.insertWidget(2, self._protocol)

    def set_connection(self, connected: bool, detail: str = "") -> None:
        self._connection.setProperty("state", "online" if connected else "offline")
        if connected:
            self._connection.setText(f"Connected  \u00b7  {detail}" if detail else "Connected")
        else:
            self._connection.setText("Disconnected  \u00b7  Setup")
        self._connection.style().unpolish(self._connection)
        self._connection.style().polish(self._connection)

    def set_vehicle(self, label: str) -> None:
        self._vehicle.setText(f"{label}  \u203a" if label else "No vehicle selected  \u203a")

    def set_protocol(self, text: str) -> None:
        self._protocol.setText(text)
        self._protocol.setVisible(bool(text))

    def set_recording(self, recording: bool) -> None:
        self._record.setChecked(recording)

    def set_primary(self, text: str) -> None:
        self._primary.setText(text)
