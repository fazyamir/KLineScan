"""
Fault Codes (DTC) Tab: Stored, Pending, Permanent fault codes with severity badges,
descriptions from local DTC database, and safety-gated clear function.
"""

from typing import List
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from klinescan.core.models import DiagnosticTroubleCode


class DtcTab(QWidget):
    read_requested = pyqtSignal(str)   # filter ("all", "stored", "pending")
    clear_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Toolbar
        btn_bar = QHBoxLayout()
        self._read_btn = QPushButton("Read DTCs")
        self._read_btn.setObjectName("primaryBtn")
        self._read_btn.clicked.connect(self._on_read_clicked)
        btn_bar.addWidget(self._read_btn)

        self._clear_btn = QPushButton("Clear DTCs (Mode 04 / 0x14)")
        self._clear_btn.setObjectName("dangerBtn")
        self._clear_btn.clicked.connect(self._confirm_clear)
        btn_bar.addWidget(self._clear_btn)

        btn_bar.addWidget(QLabel("Filter:"))
        self._filter_cb = QComboBox()
        self._filter_cb.addItems(["All Fault Codes", "Stored Only", "Pending Only", "Permanent Only"])
        self._filter_cb.currentIndexChanged.connect(self._apply_filter)
        btn_bar.addWidget(self._filter_cb)

        self._export_btn = QPushButton("Export CSV")
        self._export_btn.clicked.connect(self._export_csv)
        btn_bar.addWidget(self._export_btn)

        btn_bar.addStretch()
        self._count_label = QLabel("0 DTCs Detected")
        self._count_label.setStyleSheet("font-weight: bold; color: #94a3b8;")
        btn_bar.addWidget(self._count_label)
        layout.addLayout(btn_bar)

        # Table
        self._all_dtcs: List[DiagnosticTroubleCode] = []
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Code", "Status", "Severity", "Raw Hex", "Description"])
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

    def _on_read_clicked(self) -> None:
        idx = self._filter_cb.currentIndex()
        filter_str = "all" if idx == 0 else ("stored" if idx == 1 else ("pending" if idx == 2 else "permanent"))
        self.read_requested.emit(filter_str)

    def _confirm_clear(self) -> None:
        ans = QMessageBox.question(
            self,
            "Clear Diagnostic Trouble Codes",
            "Are you sure you want to send the Clear Diagnostic Information command (Mode 04 / KWP 0x14 / UDS 0x14) to the ECU?\n\n"
            "This will erase stored DTCs, freeze frame data, and reset emissions readiness monitors.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self.clear_requested.emit()

    def set_dtcs(self, dtcs: List[DiagnosticTroubleCode]) -> None:
        self._all_dtcs = dtcs
        self._apply_filter()

    def set_replay_locked(self, locked: bool) -> None:
        """Prevent destructive commands while viewing a recorded session."""
        self._clear_btn.setEnabled(not locked)
        self._clear_btn.setToolTip("Unavailable during session replay." if locked else "")

    def _apply_filter(self) -> None:
        idx = self._filter_cb.currentIndex()
        filtered = self._all_dtcs
        if idx == 1:
            filtered = [d for d in self._all_dtcs if "stored" in d.status.lower() or "confirmed" in d.status.lower()]
        elif idx == 2:
            filtered = [d for d in self._all_dtcs if "pending" in d.status.lower()]
        elif idx == 3:
            filtered = [d for d in self._all_dtcs if "permanent" in d.status.lower()]

        self._table.setRowCount(len(filtered))
        for row, dtc in enumerate(filtered):
            # Code Item
            code_item = QTableWidgetItem(dtc.code)
            code_item.setFont(self.font())
            code_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, code_item)

            # Status Item
            status_item = QTableWidgetItem(dtc.status)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 1, status_item)

            # Severity Item with Color
            sev_item = QTableWidgetItem(dtc.severity)
            sev_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if dtc.severity == "Critical":
                sev_item.setForeground(Qt.GlobalColor.red)
            elif dtc.severity == "Warning":
                sev_item.setForeground(Qt.GlobalColor.yellow)
            else:
                sev_item.setForeground(Qt.GlobalColor.cyan)
            self._table.setItem(row, 2, sev_item)

            # Raw Hex Item
            hex_item = QTableWidgetItem(dtc.raw_hex)
            hex_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, hex_item)

            # Description Item
            desc_item = QTableWidgetItem(dtc.description)
            self._table.setItem(row, 4, desc_item)

        total = len(filtered)
        self._count_label.setText(f"{total} DTCs Detected")
        if total > 0:
            self._count_label.setStyleSheet("font-weight: bold; color: #ef4444;")
        else:
            self._count_label.setStyleSheet("font-weight: bold; color: #22c55e;")

    def _export_csv(self) -> None:
        if not self._all_dtcs:
            QMessageBox.information(self, "Export", "No DTCs to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save DTC Report", "dtc_report.csv", "CSV Files (*.csv)")
        if path:
            try:
                import csv
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Code", "Status", "Severity", "Raw Hex", "Description"])
                    for d in self._all_dtcs:
                        writer.writerow([d.code, d.status, d.severity, d.raw_hex, d.description])
                QMessageBox.information(self, "Export", f"Exported {len(self._all_dtcs)} DTCs successfully.")
            except Exception as exc:
                QMessageBox.critical(self, "Export Error", str(exc))
