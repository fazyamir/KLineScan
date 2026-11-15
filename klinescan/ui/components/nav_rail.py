"""Primary navigation rail for the diagnostic workstation shell."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str


NAV_ITEMS = (
    NavItem("home", "Home"),
    NavItem("faults", "Faults"),
    NavItem("live", "Live"),
    NavItem("vehicle", "Vehicle"),
    NavItem("tests", "Tests"),
    NavItem("bus", "Bus"),
)


class NavRail(QWidget):
    page_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("navRail")
        self.setFixedWidth(84)
        self._buttons: Dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        brand = QLabel("KLINE\nSCAN")
        brand.setObjectName("brandMark")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setFixedHeight(64)
        layout.addWidget(brand)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for index, item in enumerate(NAV_ITEMS):
            if item.key == "bus":
                divider = QFrame()
                divider.setObjectName("navDivider")
                divider.setFixedHeight(1)
                layout.addWidget(divider)

            button = QPushButton(item.label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, key=item.key: self.page_requested.emit(key))
            self._group.addButton(button)
            self._buttons[item.key] = button
            layout.addWidget(button)

        layout.addStretch(1)
        self.set_active("home")

    def set_active(self, key: str) -> None:
        button = self._buttons.get(key)
        if button is not None:
            button.setChecked(True)
