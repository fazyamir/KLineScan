"""
Smart Contextual Hint Banner widget displaying real-time hints and action tips.
"""

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from klinescan.core.hints_engine import HintEvent


class HintBanner(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)

        # Level Badge
        self._badge = QLabel("INFO")
        self._badge.setFixedSize(65, 22)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setStyleSheet("font-weight: bold; font-size: 11px; border-radius: 4px;")
        layout.addWidget(self._badge)

        # Text and Action Tip
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        self._title = QLabel("Contextual Hint")
        self._title.setStyleSheet("font-weight: 600; font-size: 12px;")
        self._action = QLabel("")
        self._action.setStyleSheet("font-size: 11px; color: #94a3b8;")
        text_layout.addWidget(self._title)
        text_layout.addWidget(self._action)
        layout.addLayout(text_layout, 1)

        # Dismiss button
        self._dismiss_btn = QPushButton("×")
        self._dismiss_btn.setFixedSize(24, 24)
        self._dismiss_btn.setStyleSheet(
            "font-size: 16px; font-weight: bold; padding: 0; border: none; background: transparent; color: #94a3b8;"
        )
        self._dismiss_btn.clicked.connect(lambda: self.setVisible(False))
        layout.addWidget(self._dismiss_btn)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(lambda: self.setVisible(False))

    def show_hint(self, hint: HintEvent) -> None:
        self._title.setText(hint.text)
        if hint.action_tip:
            self._action.setText(f"Tip: {hint.action_tip}")
            self._action.setVisible(True)
        else:
            self._action.setVisible(False)

        if hint.level == "error":
            self.setStyleSheet("background-color: #450a0a; border: 1px solid #dc2626; border-radius: 6px;")
            self._badge.setText("CRITICAL")
            self._badge.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold; border-radius: 4px;")
            self._title.setStyleSheet("font-weight: bold; color: #fca5a5;")
        elif hint.level == "warning":
            self.setStyleSheet("background-color: #451a03; border: 1px solid #d97706; border-radius: 6px;")
            self._badge.setText("WARNING")
            self._badge.setStyleSheet("background-color: #f59e0b; color: black; font-weight: bold; border-radius: 4px;")
            self._title.setStyleSheet("font-weight: bold; color: #fde68a;")
        elif hint.level == "success":
            self.setStyleSheet("background-color: #052e16; border: 1px solid #16a34a; border-radius: 6px;")
            self._badge.setText("ONLINE")
            self._badge.setStyleSheet("background-color: #22c55e; color: black; font-weight: bold; border-radius: 4px;")
            self._title.setStyleSheet("font-weight: bold; color: #bbf7d0;")
        else:
            self.setStyleSheet("background-color: #082f49; border: 1px solid #0284c7; border-radius: 6px;")
            self._badge.setText("INFO")
            self._badge.setStyleSheet("background-color: #38bdf8; color: black; font-weight: bold; border-radius: 4px;")
            self._title.setStyleSheet("font-weight: bold; color: #bae6fd;")

        self.setVisible(True)
        if hint.timeout_s > 0:
            self._hide_timer.start(int(hint.timeout_s * 1000))
