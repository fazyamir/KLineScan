"""Update check/apply dialogs and background workers."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from klinescan.updates import UpdateInfo, apply_update, check_for_update


class UpdateCheckThread(QThread):
    """Run the network check off the UI thread."""

    checked = pyqtSignal(object)

    def run(self) -> None:  # pragma: no cover - exercised through the UI
        self.checked.emit(check_for_update())


class UpdateApplyThread(QThread):
    """Apply an update off the UI thread."""

    applied = pyqtSignal(bool, str)

    def __init__(self, root: str | Path, parent=None):
        super().__init__(parent)
        self._root = Path(root)

    def run(self) -> None:  # pragma: no cover - exercised through the UI
        try:
            apply_update(self._root)
        except Exception as exc:  # noqa: BLE001 - reported in the dialog
            self.applied.emit(False, str(exc))
        else:
            self.applied.emit(True, "")


class UpdateDialog(QDialog):
    """Show the new version and commit messages; require explicit confirmation."""

    def __init__(self, info: UpdateInfo, parent=None):
        super().__init__(parent)
        self.setWindowTitle("KLineScan update available")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            f"A newer version is available: <b>{info.remote}</b> "
            f"(you have {info.local}).", self))

        self._commits = QPlainTextEdit(self)
        self._commits.setReadOnly(True)
        self._commits.setPlainText("\n".join(f"• {line}" for line in info.commits)
                                   or "No commit details were available.")
        layout.addWidget(self._commits, 1)

        layout.addWidget(QLabel(
            "The app will update from the public GitHub repository and then restart. "
            "If you installed with git, the working tree must be clean.", self))
        buttons = QDialogButtonBox(self)
        self._update_btn = buttons.addButton("Update now", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Later", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
