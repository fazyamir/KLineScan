"""Guided landing page for the diagnostic workflow."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class _StepCard(QFrame):
    def __init__(self, number: str, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("stepCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        number_label = QLabel(number)
        number_label.setObjectName("caption")
        top.addWidget(number_label)
        top.addStretch(1)
        self.chip = QLabel("")
        self.chip.setObjectName("chip")
        self.chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.chip)
        layout.addLayout(top)

        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        layout.addWidget(title_label)

        self.detail = QLabel("")
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

    def set_state(self, detail: str, chip: str, tone: str = "") -> None:
        self.detail.setText(detail)
        self.chip.setText(chip)
        self.chip.setProperty("tone", tone)
        self.chip.style().unpolish(self.chip)
        self.chip.style().polish(self.chip)


class HomeTab(QWidget):
    setup_requested = pyqtSignal()
    scan_requested = pyqtSignal()
    faults_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(18)

        header = QVBoxLayout()
        header.setSpacing(4)
        eyebrow = QLabel("DIAGNOSTIC WORKFLOW")
        eyebrow.setObjectName("caption")
        header.addWidget(eyebrow)
        title = QLabel("Ready to diagnose")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        subtitle = QLabel("Choose a vehicle, connect the adapter, then run a health scan.")
        subtitle.setObjectName("muted")
        header.addWidget(subtitle)
        root.addLayout(header)

        hero = QFrame()
        hero.setObjectName("heroCard")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(24, 22, 24, 22)
        hero_layout.setSpacing(24)

        hero_text = QVBoxLayout()
        hero_text.setSpacing(8)
        hero_title = QLabel("Start with a clean baseline")
        hero_title.setObjectName("sectionTitle")
        hero_text.addWidget(hero_title)
        hero_body = QLabel(
            "The health scan reads stored faults, ECU identity and readiness in one pass. "
            "Live data keeps running after the scan so you can verify the repair."
        )
        hero_body.setObjectName("muted")
        hero_body.setWordWrap(True)
        hero_body.setMaximumWidth(560)
        hero_text.addWidget(hero_body)
        hero_text.addSpacing(6)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        setup_button = QPushButton("Set up vehicle & adapter")
        setup_button.setObjectName("primaryBtn")
        setup_button.clicked.connect(self.setup_requested.emit)
        actions.addWidget(setup_button)
        scan_button = QPushButton("Run full health scan")
        scan_button.clicked.connect(self.scan_requested.emit)
        actions.addWidget(scan_button)
        faults_button = QPushButton("Open fault codes")
        faults_button.setObjectName("ghostBtn")
        faults_button.clicked.connect(self.faults_requested.emit)
        actions.addWidget(faults_button)
        actions.addStretch(1)
        hero_text.addLayout(actions)
        hero_layout.addLayout(hero_text, 1)

        self._connection_chip = QLabel("OFFLINE")
        self._connection_chip.setObjectName("chip")
        self._connection_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(self._connection_chip, 0, Qt.AlignmentFlag.AlignTop)

        root.addWidget(hero)

        steps = QGridLayout()
        steps.setHorizontalSpacing(12)
        steps.setVerticalSpacing(12)
        self._vehicle_step = _StepCard("01", "Vehicle")
        self._adapter_step = _StepCard("02", "Adapter")
        self._fault_step = _StepCard("03", "Faults")
        steps.addWidget(self._vehicle_step, 0, 0)
        steps.addWidget(self._adapter_step, 0, 1)
        steps.addWidget(self._fault_step, 0, 2)
        root.addLayout(steps)

        root.addStretch(1)

        note = QLabel(
            "Safety: live actuators and fault clearing stay disabled until the adapter is connected."
        )
        note.setObjectName("caption")
        note.setWordWrap(True)
        root.addWidget(note)

        self.update_vehicle("", "", "")
        self.update_connection(False, "")
        self.update_health(None)

    def update_vehicle(self, profile: str, target: str, protocol: str) -> None:
        if profile:
            detail = profile
            if target:
                detail = f"{profile}  \u00b7  {target}"
            if protocol:
                detail = f"{detail}\n{protocol}"
            self._vehicle_step.set_state(detail, "SELECTED", "info")
        else:
            self._vehicle_step.set_state("No vehicle profile selected.", "MISSING", "warn")

    def update_connection(self, connected: bool, port: str) -> None:
        if connected:
            self._connection_chip.setText("CONNECTED")
            self._connection_chip.setProperty("tone", "ok")
            self._adapter_step.set_state(port or "Adapter online.", "READY", "ok")
        else:
            self._connection_chip.setText("OFFLINE")
            self._connection_chip.setProperty("tone", "")
            self._adapter_step.set_state("Adapter is not connected.", "OFFLINE", "")
        self._connection_chip.style().unpolish(self._connection_chip)
        self._connection_chip.style().polish(self._connection_chip)

    def update_health(self, dtc_count: int | None) -> None:
        if dtc_count is None:
            self._fault_step.set_state("Run a health scan to read stored faults.", "NOT READ", "")
        elif dtc_count == 0:
            self._fault_step.set_state("No stored fault codes reported.", "HEALTHY", "ok")
        elif dtc_count <= 2:
            self._fault_step.set_state(f"{dtc_count} stored fault code(s) reported.", "ATTENTION", "warn")
        else:
            self._fault_step.set_state(f"{dtc_count} stored fault codes reported.", "CRITICAL", "danger")
