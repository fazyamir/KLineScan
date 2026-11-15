"""
Actuator Tests & Special Functions / Service Resets Tab.
Bi-directional controls (KWP 0x30, UDS 0x2F) and routine calibrations.
"""

from typing import List, Optional
from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from klinescan.core.models import ActuatorDefinition, SpecialFunctionDefinition


class ActuatorsTab(QWidget):
    actuator_requested = pyqtSignal(object, bool)  # ActuatorDefinition, start
    special_func_requested = pyqtSignal(object)    # SpecialFunctionDefinition

    def __init__(self, parent=None):
        super().__init__(parent)
        self._actuators: List[ActuatorDefinition] = []
        self._special_funcs: List[SpecialFunctionDefinition] = []
        self._active_actuator: Optional[ActuatorDefinition] = None
        self._countdown_s: int = 0
        self._target_confidence = "curated"
        self._target = None
        self._replay_locked = False

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_timer_tick)

        self._build_ui()
        self._refresh_enabled()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Panel: Actuator Tests
        act_box = QGroupBox("Bi-Directional Actuator Tests (KWP 0x30 / UDS 0x2F)")
        act_layout = QVBoxLayout(act_box)

        self._act_list = QListWidget()
        self._act_list.currentItemChanged.connect(self._on_actuator_selected)
        act_layout.addWidget(self._act_list)

        self._act_desc = QLabel("Select an actuator component test above.")
        self._act_desc.setWordWrap(True)
        self._act_desc.setStyleSheet("color: #94a3b8; font-size: 11px;")
        act_layout.addWidget(self._act_desc)

        self._act_warning = QLabel("")
        self._act_warning.setWordWrap(True)
        self._act_warning.setStyleSheet(
            "background-color: #451a03; color: #fde68a; border: 1px solid #d97706; "
            "border-radius: 4px; padding: 6px; font-weight: bold; font-size: 11px;"
        )
        self._act_warning.setVisible(False)
        act_layout.addWidget(self._act_warning)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._progress.setVisible(False)
        act_layout.addWidget(self._progress)

        # Action Buttons
        btn_layout = QHBoxLayout()
        self._start_btn = QPushButton("Start Actuator Test")
        self._start_btn.setObjectName("primaryBtn")
        self._start_btn.clicked.connect(self._on_start_actuator)
        btn_layout.addWidget(self._start_btn)

        self._abort_btn = QPushButton("Abort / Stop Test")
        self._abort_btn.setObjectName("dangerBtn")
        self._abort_btn.setEnabled(False)
        self._abort_btn.clicked.connect(self._on_abort_actuator)
        btn_layout.addWidget(self._abort_btn)
        act_layout.addLayout(btn_layout)

        splitter.addWidget(act_box)

        # Right Panel: Special Functions / Service Resets
        sp_box = QGroupBox("Special Functions & Calibrations (Adaptive Resets)")
        sp_layout = QVBoxLayout(sp_box)

        self._sp_list = QListWidget()
        self._sp_list.currentItemChanged.connect(self._on_special_selected)
        sp_layout.addWidget(self._sp_list)

        self._sp_steps = QTextEdit()
        self._sp_steps.setReadOnly(True)
        self._sp_steps.setPlaceholderText("Instructions and prerequisites will appear here.")
        sp_layout.addWidget(self._sp_steps)

        self._exec_sp_btn = QPushButton("Execute Special Function")
        self._exec_sp_btn.setObjectName("successBtn")
        self._exec_sp_btn.clicked.connect(self._on_exec_special)
        sp_layout.addWidget(self._exec_sp_btn)

        splitter.addWidget(sp_box)
        layout.addWidget(splitter)

    def set_target_capabilities(
        self, actuators: List[ActuatorDefinition], specials: List[SpecialFunctionDefinition],
        confidence: str = "curated", target=None
    ) -> None:
        self._target_confidence = confidence
        self._target = target
        self._actuators = actuators
        self._special_funcs = specials

        self._act_list.clear()
        for act in actuators:
            item = QListWidgetItem(act.name)
            item.setData(Qt.ItemDataRole.UserRole, act)
            self._act_list.addItem(item)

        self._sp_list.clear()
        for sp in specials:
            item = QListWidgetItem(sp.name)
            item.setData(Qt.ItemDataRole.UserRole, sp)
            self._sp_list.addItem(item)

        if actuators:
            self._act_list.setCurrentRow(0)
        if specials:
            self._sp_list.setCurrentRow(0)
        self._refresh_enabled()

    def set_replay_locked(self, locked: bool) -> None:
        self._replay_locked = locked
        self._refresh_enabled()

    def _block_reason(self, operation: str, definition) -> str:
        """Same rules the worker/service enforce (klinescan.core.safety)."""
        from klinescan.core.models import EcuTarget, ProtocolType
        from klinescan.core.safety import write_block_reason

        target = getattr(self, "_target", None)
        if target is None:  # legacy callers: judge by confidence only
            target = EcuTarget("target", ProtocolType.KWP2000_FAST, confidence=self._target_confidence)
        return write_block_reason(target, operation, replaying=getattr(self, "_replay_locked", False),
                                  definition=definition)

    def _refresh_enabled(self) -> None:
        act_item = self._act_list.currentItem()
        sp_item = self._sp_list.currentItem()
        act = act_item.data(Qt.ItemDataRole.UserRole) if act_item else None
        sp = sp_item.data(Qt.ItemDataRole.UserRole) if sp_item else None
        act_reason = self._block_reason("actuator", act) if act else "No actuator test defined for this module."
        sp_reason = self._block_reason("special_function", sp) if sp else "No special function defined for this module."
        running = self._active_actuator is not None
        self._start_btn.setEnabled(not act_reason and not running)
        self._exec_sp_btn.setEnabled(not sp_reason)
        self._start_btn.setToolTip(act_reason)
        self._exec_sp_btn.setToolTip(sp_reason)
        if act_reason and act is not None:
            self._act_warning.setText(f"SAFETY: {act_reason}")
            self._act_warning.setVisible(True)

    def _on_actuator_selected(self, current: Optional[QListWidgetItem], previous=None) -> None:
        if not current:
            return
        act: ActuatorDefinition = current.data(Qt.ItemDataRole.UserRole)
        self._act_desc.setText(f"Description: {act.description or 'No description provided.'}\nDuration: {act.duration_seconds} seconds")
        if act.warning:
            self._act_warning.setText(f"SAFETY WARNING: {act.warning}")
            self._act_warning.setVisible(True)
        else:
            self._act_warning.setVisible(False)
        self._refresh_enabled()

    def _on_special_selected(self, current: Optional[QListWidgetItem], previous=None) -> None:
        if not current:
            return
        sp: SpecialFunctionDefinition = current.data(Qt.ItemDataRole.UserRole)
        steps_text = f"Function: {sp.name}\n\nDescription:\n{sp.description}\n\nPrerequisites & Steps:\n"
        for i, step in enumerate(sp.steps, 1):
            steps_text += f"{i}. {step}\n"
        self._sp_steps.setPlainText(steps_text)
        self._refresh_enabled()

    def _on_start_actuator(self) -> None:
        item = self._act_list.currentItem()
        if not item:
            return
        act: ActuatorDefinition = item.data(Qt.ItemDataRole.UserRole)

        confirm_msg = f"Activate {act.name}?"
        if act.warning:
            confirm_msg += f"\n\nWARNING: {act.warning}"
        ans = QMessageBox.warning(
            self,
            "Confirm Actuator Activation",
            confirm_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self._active_actuator = act
        self._countdown_s = int(act.duration_seconds)
        self._progress.setMaximum(self._countdown_s)
        self._progress.setValue(self._countdown_s)
        self._progress.setVisible(True)
        self._start_btn.setEnabled(False)
        self._abort_btn.setEnabled(True)
        self._timer.start()

        self.actuator_requested.emit(act, True)

    def _on_abort_actuator(self) -> None:
        self._timer.stop()
        if self._active_actuator:
            self.actuator_requested.emit(self._active_actuator, False)
        self._progress.setVisible(False)
        self._abort_btn.setEnabled(False)
        self._active_actuator = None
        self._refresh_enabled()

    def _on_timer_tick(self) -> None:
        self._countdown_s -= 1
        self._progress.setValue(self._countdown_s)
        if self._countdown_s <= 0:
            self._on_abort_actuator()

    def _on_exec_special(self) -> None:
        item = self._sp_list.currentItem()
        if not item:
            return
        sp: SpecialFunctionDefinition = item.data(Qt.ItemDataRole.UserRole)
        ans = QMessageBox.question(
            self,
            "Execute Function",
            f"Are all prerequisites met to execute {sp.name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self.special_func_requested.emit(sp)
