"""
ECU Identification, Freeze Frame Snapshot & I/M Readiness Monitors Tab.
"""

from typing import List, Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from klinescan.core.models import EcuIdItem, FreezeFrame, ReadinessMonitor


class EcuInfoTab(QWidget):
    read_ecu_id_requested = pyqtSignal()
    read_freeze_frame_requested = pyqtSignal()
    read_readiness_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Top Section: ECU Identification
        id_box = QGroupBox("ECU Identification & VIN (Mode 09 / KWP 0x1A / UDS 0x22)")
        id_layout = QVBoxLayout(id_box)
        id_btn_bar = QHBoxLayout()
        self._read_id_btn = QPushButton("Read ECU Identification")
        self._read_id_btn.setObjectName("primaryBtn")
        self._read_id_btn.clicked.connect(self.read_ecu_id_requested.emit)
        id_btn_bar.addWidget(self._read_id_btn)
        id_btn_bar.addStretch()
        id_layout.addLayout(id_btn_bar)

        self._id_table = QTableWidget(0, 3)
        self._id_table.setHorizontalHeaderLabels(["Parameter", "Value", "Raw Hex"])
        self._id_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._id_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        id_layout.addWidget(self._id_table)
        layout.addWidget(id_box, 1)

        # Bottom Section: Splitter with Freeze Frame & Readiness
        bottom_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Freeze Frame Panel
        ff_box = QGroupBox("Freeze Frame Snapshot (Mode 02 / UDS 0x19 0x04)")
        ff_layout = QVBoxLayout(ff_box)
        ff_btn_bar = QHBoxLayout()
        self._read_ff_btn = QPushButton("Read Freeze Frame")
        self._read_ff_btn.clicked.connect(self.read_freeze_frame_requested.emit)
        ff_btn_bar.addWidget(self._read_ff_btn)
        self._ff_trig_label = QLabel("Trigger DTC: None")
        self._ff_trig_label.setStyleSheet("font-weight: bold; color: #ef4444;")
        ff_btn_bar.addWidget(self._ff_trig_label)
        ff_btn_bar.addStretch()
        ff_layout.addLayout(ff_btn_bar)

        self._ff_table = QTableWidget(0, 4)
        self._ff_table.setHorizontalHeaderLabels(["PID", "Parameter", "Value", "Raw Hex"])
        self._ff_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._ff_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        ff_layout.addWidget(self._ff_table)
        bottom_splitter.addWidget(ff_box)

        # Readiness Monitors Panel
        rd_box = QGroupBox("I/M Emissions Readiness Status (Mode 01 PID 01)")
        rd_layout = QVBoxLayout(rd_box)
        rd_btn_bar = QHBoxLayout()
        self._read_rd_btn = QPushButton("Read Readiness")
        self._read_rd_btn.clicked.connect(self.read_readiness_requested.emit)
        rd_btn_bar.addWidget(self._read_rd_btn)
        rd_btn_bar.addStretch()
        rd_layout.addLayout(rd_btn_bar)

        self._rd_table = QTableWidget(0, 2)
        self._rd_table.setHorizontalHeaderLabels(["Monitor Name", "Status"])
        self._rd_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._rd_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        rd_layout.addWidget(self._rd_table)
        bottom_splitter.addWidget(rd_box)

        layout.addWidget(bottom_splitter, 2)

    def set_ecu_id(self, items: List[EcuIdItem]) -> None:
        self._id_table.setRowCount(len(items))
        for row, item in enumerate(items):
            self._id_table.setItem(row, 0, QTableWidgetItem(item.label))
            val_item = QTableWidgetItem(item.value)
            val_item.setFont(self.font())
            self._id_table.setItem(row, 1, val_item)
            self._id_table.setItem(row, 2, QTableWidgetItem(item.raw_hex))

    def set_freeze_frame(self, ff: Optional[FreezeFrame]) -> None:
        if not ff or not ff.pids:
            self._ff_trig_label.setText("Trigger DTC: None")
            self._ff_table.setRowCount(0)
            return

        self._ff_trig_label.setText(f"Trigger DTC: {ff.dtc_trigger}")
        self._ff_table.setRowCount(len(ff.pids))
        for row, p in enumerate(ff.pids):
            self._ff_table.setItem(row, 0, QTableWidgetItem(f"0x{p.pid:02X}"))
            self._ff_table.setItem(row, 1, QTableWidgetItem(p.name))
            val_str = f"{p.value} {p.unit}".strip()
            self._ff_table.setItem(row, 2, QTableWidgetItem(val_str))
            self._ff_table.setItem(row, 3, QTableWidgetItem(p.raw_hex))

    def set_readiness(self, monitors: List[ReadinessMonitor]) -> None:
        self._rd_table.setRowCount(len(monitors))
        for row, mon in enumerate(monitors):
            self._rd_table.setItem(row, 0, QTableWidgetItem(mon.name))
            status_item = QTableWidgetItem("PASSED / READY" if mon.complete else "NOT READY")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if mon.complete:
                status_item.setForeground(Qt.GlobalColor.green)
            else:
                status_item.setForeground(Qt.GlobalColor.yellow)
            self._rd_table.setItem(row, 1, status_item)
