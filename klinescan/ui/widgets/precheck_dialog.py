"""
Guided Pre-Check Wizard Dialog: Prompts users with safety and prerequisite checklists
prior to executing health scans, sensor diagnostic tests, or actuator activations.
"""

from typing import List
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class PrecheckDialog(QDialog):
    def __init__(
        self,
        title: str,
        operation_name: str,
        checklist_items: List[str],
        warning_text: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(480, 320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel(f"Pre-Check: {operation_name}")
        header.setStyleSheet("font-size: 14px; font-weight: bold; color: #38bdf8;")
        layout.addWidget(header)

        sub = QLabel("Please confirm each prerequisite condition below before proceeding:")
        sub.setStyleSheet("color: #94a3b8; font-size: 11px;")
        layout.addWidget(sub)

        if warning_text:
            warn_frame = QFrame()
            warn_frame.setStyleSheet(
                "background-color: #451a03; border: 1px solid #d97706; border-radius: 6px; padding: 8px;"
            )
            w_layout = QVBoxLayout(warn_frame)
            warn_label = QLabel(f"<strong>SAFETY WARNING:</strong> {warning_text}")
            warn_label.setWordWrap(True)
            warn_label.setStyleSheet("color: #fde68a; font-size: 11px;")
            w_layout.addWidget(warn_label)
            layout.addWidget(warn_frame)

        # Checkboxes container
        self._checkboxes: List[QCheckBox] = []
        chk_container = QFrame()
        chk_container.setStyleSheet(
            "background-color: #0b1120; border: 1px solid #1e293b; border-radius: 6px; padding: 10px;"
        )
        chk_layout = QVBoxLayout(chk_container)
        chk_layout.setSpacing(8)

        for item in checklist_items:
            chk = QCheckBox(item)
            chk.setStyleSheet("color: #f8fafc; font-size: 12px;")
            chk.toggled.connect(self._check_all_ticked)
            self._checkboxes.append(chk)
            chk_layout.addWidget(chk)

        layout.addWidget(chk_container)

        # Buttons
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn: QPushButton = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("Proceed")
        self._ok_btn.setObjectName("primaryBtn")
        self._ok_btn.setEnabled(False)

        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _check_all_ticked(self) -> None:
        all_checked = all(chk.isChecked() for chk in self._checkboxes)
        self._ok_btn.setEnabled(all_checked)
