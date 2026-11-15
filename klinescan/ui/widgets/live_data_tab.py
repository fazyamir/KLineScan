"""
Live Data & Real-Time Waveform Plotter Tab:
Table with Min/Max/Avg/Current statistics, selectable PID checklist,
CSV logging, and embedded multi-trace pyqtgraph real-time oscilloscope.
"""

from collections import deque
import csv
import datetime
import os
from typing import Dict, List, Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
import pyqtgraph as pg
from klinescan.core.models import LivePid

# Distinct vibrant colors for pyqtgraph multi-trace plotting
PLOT_COLORS = [
    "#38bdf8",  # Cyan
    "#22c55e",  # Emerald Green
    "#f59e0b",  # Amber
    "#ef4444",  # Crimson
    "#a855f7",  # Purple
    "#ec4899",  # Pink
    "#06b6d4",  # Teal
    "#e2e8f0",  # White
]


class LiveDataTab(QWidget):
    polling_toggled = pyqtSignal(bool)
    poll_interval_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_pids: Dict[int, LivePid] = {}
        self._graph_selected_pids: List[int] = [0x0C, 0x0D, 0x05]  # Default: RPM, Speed, Temp
        self._metric_units = True
        self._graph_paused = False

        # CSV Logging
        self._csv_file = None
        self._csv_writer = None
        self._csv_path = ""

        # Waveform Buffers (up to 120 points per PID)
        self._plot_buffers: Dict[int, deque] = {}
        self._plot_curves: Dict[int, pg.PlotDataItem] = {}

        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # Top Control Bar
        top_bar = QHBoxLayout()
        self._poll_chk = QCheckBox("Enable Live Polling")
        self._poll_chk.setObjectName("pollCheck")
        self._poll_chk.setStyleSheet("font-weight: bold; font-size: 12px; color: #38bdf8;")
        self._poll_chk.toggled.connect(self._on_poll_toggled)
        top_bar.addWidget(self._poll_chk)

        top_bar.addWidget(QLabel("Rate:"))
        self._rate_cb = QComboBox()
        self._rate_cb.addItem("100 ms (Fast)", 100)
        self._rate_cb.addItem("250 ms (Standard)", 250)
        self._rate_cb.addItem("500 ms (Relaxed)", 500)
        self._rate_cb.addItem("1000 ms (Slow)", 1000)
        self._rate_cb.setCurrentIndex(1)
        self._rate_cb.currentIndexChanged.connect(self._on_rate_changed)
        top_bar.addWidget(self._rate_cb)

        self._unit_btn = QPushButton("Units: Metric (°C, km/h)")
        self._unit_btn.clicked.connect(self._toggle_units)
        top_bar.addWidget(self._unit_btn)

        self._log_btn = QPushButton("Start CSV Log")
        self._log_btn.clicked.connect(self._toggle_csv_logging)
        top_bar.addWidget(self._log_btn)

        top_bar.addStretch()

        self._pause_graph_btn = QPushButton("Pause Graph")
        self._pause_graph_btn.clicked.connect(self._toggle_graph_pause)
        top_bar.addWidget(self._pause_graph_btn)

        self._clear_graph_btn = QPushButton("Clear Graph")
        self._clear_graph_btn.clicked.connect(self._clear_graph)
        top_bar.addWidget(self._clear_graph_btn)

        main_layout.addLayout(top_bar)

        # Splitter: Left Table | Right Plotter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Live PIDs Table
        table_container = QWidget()
        t_layout = QVBoxLayout(table_container)
        t_layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(["Plot", "PID", "Parameter", "Current", "Unit", "Min", "Max", "Avg"])
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t_layout.addWidget(self._table)
        splitter.addWidget(table_container)

        # Right: pyqtgraph PlotWidget
        plot_container = QGroupBox("Real-Time Waveform Plotter")
        p_layout = QVBoxLayout(plot_container)
        p_layout.setContentsMargins(4, 8, 4, 4)

        pg.setConfigOption("background", "#0b1120")
        pg.setConfigOption("foreground", "#94a3b8")
        pg.setConfigOption("antialias", True)

        self._plot_widget = pg.PlotWidget()
        self._plot_widget.showGrid(x=True, y=True, alpha=0.25)
        self._plot_widget.addLegend(offset=(10, 10))
        self._plot_widget.setLabel("left", "Value")
        self._plot_widget.setLabel("bottom", "Samples (Rolling)")
        p_layout.addWidget(self._plot_widget)
        splitter.addWidget(plot_container)

        splitter.setSizes([450, 470])
        main_layout.addWidget(splitter)

    def _on_poll_toggled(self, checked: bool) -> None:
        self.polling_toggled.emit(checked)

    def _on_rate_changed(self) -> None:
        interval = self._rate_cb.currentData()
        self.poll_interval_changed.emit(interval)

    def set_polling_checked(self, checked: bool) -> None:
        self._poll_chk.blockSignals(True)
        self._poll_chk.setChecked(checked)
        self._poll_chk.blockSignals(False)

    def _toggle_units(self) -> None:
        self._metric_units = not self._metric_units
        if self._metric_units:
            self._unit_btn.setText("Units: Metric (°C, km/h)")
        else:
            self._unit_btn.setText("Units: Imperial (°F, mph)")
        self.update_pids(list(self._all_pids.values()))

    def _toggle_graph_pause(self) -> None:
        self._graph_paused = not self._graph_paused
        self._pause_graph_btn.setText("Resume Graph" if self._graph_paused else "Pause Graph")

    def _clear_graph(self) -> None:
        for b in self._plot_buffers.values():
            b.clear()
        for curve in self._plot_curves.values():
            curve.clear()

    def _toggle_csv_logging(self) -> None:
        if self._csv_file is None:
            # Start logging
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            log_dir = os.path.join(base_dir, "logs")
            os.makedirs(log_dir, exist_ok=True)
            t_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self._csv_path = os.path.join(log_dir, f"telemetry_{t_str}.csv")
            try:
                self._csv_file = open(self._csv_path, "w", newline="", encoding="utf-8")
                self._csv_writer = csv.writer(self._csv_file)
                self._csv_writer.writerow(["Timestamp", "PID", "Parameter", "Value", "Unit", "RawHex"])
                self._log_btn.setText("Stop CSV Log")
                self._log_btn.setObjectName("dangerBtn")
                self._log_btn.setStyle(self._log_btn.style())
            except Exception as exc:
                QMessageBox.critical(self, "Log Error", f"Failed to open log file: {exc}")
        else:
            # Stop logging
            try:
                self._csv_file.flush()
                self._csv_file.close()
            except Exception:
                pass
            self._csv_file = None
            self._csv_writer = None
            self._log_btn.setText("Start CSV Log")
            self._log_btn.setObjectName("")
            self._log_btn.setStyle(self._log_btn.style())
            QMessageBox.information(self, "Telemetry Saved", f"CSV Log saved to:\n{self._csv_path}")

    def update_pids(self, pids: List[LivePid]) -> None:
        now_str = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

        for pid in pids:
            # Maintain stats
            existing = self._all_pids.get(pid.pid)
            if existing:
                if pid.numeric_value is not None:
                    existing.update_stats(pid.numeric_value)
                existing.value = pid.value
                existing.unit = pid.unit
                existing.raw_hex = pid.raw_hex
                target_pid = existing
            else:
                if pid.numeric_value is not None:
                    pid.update_stats(pid.numeric_value)
                self._all_pids[pid.pid] = pid
                target_pid = pid

            # Log to CSV if active
            if self._csv_writer:
                self._csv_writer.writerow([now_str, f"0x{pid.pid:02X}", pid.name, pid.value, pid.unit, pid.raw_hex])

            # Update waveform buffer if graphed
            if not self._graph_paused and target_pid.numeric_value is not None and pid.pid in self._graph_selected_pids:
                if pid.pid not in self._plot_buffers:
                    self._plot_buffers[pid.pid] = deque(maxlen=120)
                self._plot_buffers[pid.pid].append(target_pid.numeric_value)

        self._refresh_table()
        if not self._graph_paused:
            self._refresh_plot()

    def _refresh_table(self) -> None:
        sorted_pids = sorted(self._all_pids.values(), key=lambda p: p.pid)
        self._table.setRowCount(len(sorted_pids))

        for row, p in enumerate(sorted_pids):
            # Column 0: Plot Checkbox
            chk = QCheckBox()
            chk.setChecked(p.pid in self._graph_selected_pids)
            chk.toggled.connect(lambda checked, pid=p.pid: self._on_plot_checked(pid, checked))
            self._table.setCellWidget(row, 0, chk)

            # Column 1: PID
            self._table.setItem(row, 1, QTableWidgetItem(f"0x{p.pid:02X}"))

            # Column 2: Parameter Name
            self._table.setItem(row, 2, QTableWidgetItem(p.name))

            # Unit Conversion if Imperial
            display_val = p.value
            display_unit = p.unit
            if not self._metric_units and p.numeric_value is not None:
                if "°C" in p.unit:
                    f_val = p.numeric_value * 9 / 5 + 32
                    display_val = f"{f_val:.1f}"
                    display_unit = "°F"
                elif "km/h" in p.unit:
                    mph = p.numeric_value * 0.621371
                    display_val = f"{mph:.1f}"
                    display_unit = "mph"

            # Column 3: Current Value
            v_item = QTableWidgetItem(display_val)
            v_item.setFont(self.font())
            v_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 3, v_item)

            # Column 4: Unit
            self._table.setItem(row, 4, QTableWidgetItem(display_unit))

            # Column 5: Min
            min_str = f"{p.min_val:.1f}" if p.min_val is not None else "--"
            self._table.setItem(row, 5, QTableWidgetItem(min_str))

            # Column 6: Max
            max_str = f"{p.max_val:.1f}" if p.max_val is not None else "--"
            self._table.setItem(row, 6, QTableWidgetItem(max_str))

            # Column 7: Avg
            avg_str = f"{p.avg_val:.1f}" if p.avg_val is not None else "--"
            self._table.setItem(row, 7, QTableWidgetItem(avg_str))

    def _on_plot_checked(self, pid: int, checked: bool) -> None:
        if checked:
            if pid not in self._graph_selected_pids:
                if len(self._graph_selected_pids) >= 8:
                    QMessageBox.warning(self, "Graph Limit", "Maximum of 8 simultaneous PID traces allowed.")
                    self._refresh_table()
                    return
                self._graph_selected_pids.append(pid)
        else:
            if pid in self._graph_selected_pids:
                self._graph_selected_pids.remove(pid)
                if pid in self._plot_curves:
                    self._plot_widget.removeItem(self._plot_curves[pid])
                    del self._plot_curves[pid]

    def _refresh_plot(self) -> None:
        for idx, pid in enumerate(self._graph_selected_pids):
            buf = self._plot_buffers.get(pid)
            if not buf:
                continue

            color = PLOT_COLORS[idx % len(PLOT_COLORS)]
            name = self._all_pids[pid].name if pid in self._all_pids else f"PID 0x{pid:02X}"

            if pid not in self._plot_curves:
                pen = pg.mkPen(color=color, width=2)
                curve = self._plot_widget.plot(name=name, pen=pen)
                self._plot_curves[pid] = curve

            self._plot_curves[pid].setData(list(buf))
