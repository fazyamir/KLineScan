"""
MainWindow: Unified Professional Automotive Diagnostic Suite Dashboard.
"""

import datetime
import os
import sys
from typing import Dict, List, Optional
from PyQt6.QtCore import QSettings, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from klinescan import __version__
from klinescan.core.models import (
    ActuatorDefinition,
    BaudMode,
    DiagnosticTroubleCode,
    EcuIdItem,
    EcuTarget,
    FreezeFrame,
    LivePid,
    ReadinessMonitor,
    SpecialFunctionDefinition,
    TransportType,
    VehicleProfile,
)
from klinescan.core.pack_manager import PackManager
from klinescan.core.report_exporter import generate_html_report
from klinescan.core.settings import AppSettings
from klinescan.transport.auto_detect import AutoDetector, DiscoveredInterface
from klinescan.ui.widgets.actuators_tab import ActuatorsTab
from klinescan.ui.widgets.baud_panel import BaudDialog
from klinescan.ui.widgets.console_tab import ConsoleTab
from klinescan.ui.widgets.dtc_tab import DtcTab
from klinescan.ui.widgets.ecu_info_tab import EcuInfoTab
from klinescan.ui.widgets.hint_banner import HintBanner
from klinescan.ui.widgets.led_panel import LedPanel
from klinescan.ui.widgets.live_data_tab import LiveDataTab
from klinescan.ui.widgets.obd_pinout import ObdPinoutDialog
from klinescan.ui.widgets.playback_toolbar import PlaybackToolbar
from klinescan.ui.widgets.vehicle_selector import VehicleSelectorWidget
from klinescan.ui.components.nav_rail import NavRail
from klinescan.ui.components.top_bar import TopBar
from klinescan.ui.theme.theme import get_theme_manager
from klinescan.ui.widgets.home_tab import HomeTab
from klinescan.ui.update_dialog import UpdateApplyThread, UpdateCheckThread, UpdateDialog
from klinescan.workers.scanner_worker import ScannerWorker


class MainWindow(QMainWindow):
    _sig_connect = pyqtSignal(object, str, object, object, int)
    _sig_disconnect = pyqtSignal()
    _sig_read_ecu_id = pyqtSignal()
    _sig_identify = pyqtSignal(object, str)
    _sig_detect_done = pyqtSignal(list)
    # Every call into the worker goes through a queued signal: the worker lives in
    # its own QThread and owns the serial port.
    _sig_full_scan = pyqtSignal()
    _sig_break = pyqtSignal(int)
    _sig_start_recording = pyqtSignal(str, dict)
    _sig_shutdown = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("KLineScan — Native Automotive Diagnostic Suite")
        self.resize(1180, 780)

        self._settings = AppSettings()
        self._pack_manager = PackManager()

        self._current_target: Optional[EcuTarget] = None
        self._current_profile: Optional[VehicleProfile] = None
        self._current_baud_mode: BaudMode = BaudMode.AUTOMATIC
        self._custom_baud: int = 10400

        # Cached diagnostic report data
        self._last_ecu_ids: List[EcuIdItem] = []
        self._last_dtcs: List[DiagnosticTroubleCode] = []
        self._last_ff: Optional[FreezeFrame] = None
        self._last_readiness: List[ReadinessMonitor] = []
        self._last_pids: List[LivePid] = []

        self._setup_worker_thread()
        self._build_ui()
        self._wire_signals()
        self._apply_theme(self._settings.get("theme", "dark_workshop"))
        self._refresh_ports()

        # Start auto-detect hotplug background poller
        self._autodetector = AutoDetector(self._on_interfaces_discovered)
        self._autodetector.start(interval_s=2.5)

        self._update_thread = None
        self._update_apply_thread = None
        if self._settings.get("check_for_updates", True) and not os.environ.get("KLINESCAN_NO_UPDATE_CHECK"):
            QTimer.singleShot(1500, lambda: self._check_updates(manual=False))

    def _toggle_setup_drawer(self) -> None:
        self._setup_drawer.setVisible(not self._setup_drawer.isVisible())

    def _navigate(self, key: str) -> None:
        page = self._page_by_key.get(key)
        if page is None:
            return
        self._pages.setCurrentWidget(page)
        self._nav.set_active(key)

    def _on_page_changed(self, index: int) -> None:
        page = self._pages.widget(index)
        for key, candidate in self._page_by_key.items():
            if candidate is page:
                self._nav.set_active(key)
                break

    def _on_primary_action(self) -> None:
        if self._worker and self._connect_btn.isEnabled():
            self._toggle_setup_drawer()
        else:
            self._run_full_scan()

    def _run_full_scan(self) -> None:
        if not self._current_target:
            self._toggle_setup_drawer()
            self.statusBar().showMessage("Select a vehicle module before running a scan.")
            return
        self._sig_full_scan.emit()
        self._navigate("faults")
        self.statusBar().showMessage("Full health scan requested…")

    def _setup_worker_thread(self) -> None:
        self._worker_thread = QThread(self)
        self._worker = ScannerWorker()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.initialize)
        self._worker_thread.start()

    def _build_ui(self) -> None:
        self._build_menus()

        central = QWidget()
        self.setCentralWidget(central)
        shell = QHBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self._nav = NavRail()
        shell.addWidget(self._nav)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        shell.addWidget(content, 1)

        self._top_bar = TopBar()
        root.addWidget(self._top_bar)

        # Hardware controls live in a collapsible drawer rather than in the
        # permanent chrome.  The controls themselves remain the existing,
        # tested controls, so connection behaviour is unchanged.
        self._setup_drawer = QFrame()
        self._setup_drawer.setObjectName("setupDrawer")
        drawer_layout = QVBoxLayout(self._setup_drawer)
        drawer_layout.setContentsMargins(12, 8, 12, 8)
        drawer_layout.setSpacing(8)
        self._conn_box = QFrame()
        self._conn_box.setObjectName("connectionControls")
        conn_layout = QHBoxLayout(self._conn_box)
        conn_layout.setContentsMargins(0, 0, 0, 0)
        conn_layout.setSpacing(8)

        conn_layout.addWidget(QLabel("ADAPTER"))
        self._transport_cb = QComboBox()
        for t in TransportType:
            self._transport_cb.addItem(t.value, t)
        last_t_val = self._settings.get("transport", TransportType.FAKE.value)
        for i in range(self._transport_cb.count()):
            if self._transport_cb.itemText(i) == last_t_val:
                self._transport_cb.setCurrentIndex(i)
                break
        conn_layout.addWidget(self._transport_cb, 2)
        conn_layout.addWidget(QLabel("PORT"))
        self._port_cb = QComboBox()
        self._port_cb.setEditable(True)  # also accepts tcp://, bt://, ble://, doip:// addresses
        self._port_cb.lineEdit().setPlaceholderText("COM5, /dev/ttyUSB0, tcp://192.168.0.10:35000, ble://…")
        self._port_cb.setToolTip(
            "Serial/USB/Bluetooth port, or tcp://host:port (Wi-Fi ELM), bt://MAC[/channel] (Bluetooth RFCOMM), "
            "ble://MAC (Bluetooth LE, needs 'bleak'), doip://[ip[/logical]] (DoIP, needs 'doipclient')")
        conn_layout.addWidget(self._port_cb, 2)
        self._refresh_btn = QPushButton("Detect")
        self._refresh_btn.setToolTip("Probe adapters: ELM327 / KKL / Bluetooth / Wi-Fi / BLE / DoIP")
        self._refresh_btn.clicked.connect(self._detect_adapters)
        conn_layout.addWidget(self._refresh_btn)
        self._identify_btn = QPushButton("Identify vehicle")
        self._identify_btn.setToolTip("Connect with generic OBD-II, read VIN / ECU IDs and find the vehicle in the catalogue")
        self._identify_btn.clicked.connect(self._identify_vehicle)
        conn_layout.addWidget(self._identify_btn)
        self._baud_config_btn = QPushButton("Baud: Auto")
        self._baud_config_btn.clicked.connect(self._show_baud_dialog)
        conn_layout.addWidget(self._baud_config_btn)
        self._pinout_btn = QPushButton("Pinout")
        self._pinout_btn.clicked.connect(self._show_pinout_dialog)
        conn_layout.addWidget(self._pinout_btn)
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setProperty("variant", "primary")
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        conn_layout.addWidget(self._connect_btn)
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.setProperty("variant", "danger")
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.clicked.connect(self._sig_disconnect.emit)
        conn_layout.addWidget(self._disconnect_btn)
        drawer_layout.addWidget(self._conn_box)

        self._veh_selector = VehicleSelectorWidget(self._pack_manager)
        self._veh_selector.target_selected.connect(self._on_vehicle_target_selected)
        drawer_layout.addWidget(self._veh_selector)
        self._setup_drawer.hide()
        root.addWidget(self._setup_drawer)

        # Pages are deliberately kept as the existing functional widgets;
        # the shell and navigation change independently of diagnostic logic.
        self._pages = QStackedWidget()
        self._home = HomeTab()
        self._dtc_tab = DtcTab()
        self._live_tab = LiveDataTab()
        self._ecu_tab = EcuInfoTab()
        self._act_tab = ActuatorsTab()
        self._console_tab = ConsoleTab()
        self._page_by_key = {
            "home": self._home,
            "faults": self._dtc_tab,
            "live": self._live_tab,
            "vehicle": self._ecu_tab,
            "tests": self._act_tab,
            "bus": self._console_tab,
        }
        for page in self._page_by_key.values():
            self._pages.addWidget(page)
        self._home.setup_requested.connect(self._toggle_setup_drawer)
        self._home.scan_requested.connect(self._run_full_scan)
        self._home.faults_requested.connect(lambda: self._navigate("faults"))
        self._pages.currentChanged.connect(self._on_page_changed)
        self._nav.page_requested.connect(self._navigate)
        self._top_bar.setup_requested.connect(self._toggle_setup_drawer)
        self._top_bar.vehicle_requested.connect(self._toggle_setup_drawer)
        self._top_bar.primary_requested.connect(self._on_primary_action)
        root.addWidget(self._pages, 1)

        self._hint_banner = HintBanner()
        self._hint_banner.hide()
        root.insertWidget(2, self._hint_banner)

        self._playback_bar = PlaybackToolbar()
        self._top_bar.record_requested.connect(self._playback_bar._on_rec_clicked)
        self._playback_bar.hide()
        root.addWidget(self._playback_bar)

        self._leds = LedPanel()
        self._leds.setObjectName("statusStrip")
        root.addWidget(self._leds)

        # Drawer-only hardware toggles; the bottom strip stays observational.
        hardware_row = QHBoxLayout()
        hardware_row.addWidget(QLabel("HARDWARE"))
        for control in (self._leds._echo_chk, self._leds._l_line_btn, self._leds._break_btn):
            hardware_row.addWidget(control)
        hardware_row.addStretch(1)
        drawer_layout.addLayout(hardware_row)

        self.statusBar().showMessage("Ready — choose a vehicle and adapter to begin.")

    def _build_menus(self) -> None:
        mb = self.menuBar()

        # File Menu
        file_menu = mb.addMenu("&File")

        load_pack_act = QAction("&Load Diagnostic Pack...", self)
        load_pack_act.triggered.connect(self._on_load_pack_dialog)
        file_menu.addAction(load_pack_act)

        open_rec_act = QAction("&Open Session Recording (CSV)...", self)
        open_rec_act.triggered.connect(lambda: self._playback_bar._on_open_clicked())
        file_menu.addAction(open_rec_act)

        file_menu.addSeparator()

        export_report_act = QAction("&Export Health Report (HTML)...", self)
        export_report_act.setShortcut(QKeySequence("Ctrl+P"))
        export_report_act.triggered.connect(self._export_health_report)
        file_menu.addAction(export_report_act)

        screenshot_act = QAction("&Save Window Screenshot...", self)
        screenshot_act.setShortcut(QKeySequence("F12"))
        screenshot_act.triggered.connect(self._take_screenshot)
        file_menu.addAction(screenshot_act)

        file_menu.addSeparator()
        exit_act = QAction("E&xit", self)
        exit_act.triggered.connect(self.close)
        file_menu.addAction(exit_act)

        # View Menu
        view_menu = mb.addMenu("&View")
        theme_menu = view_menu.addMenu("&Theme")
        self._theme_group = QActionGroup(self)

        self._dark_theme_act = QAction("Dark Workshop (Default)", self, checkable=True)
        self._dark_theme_act.setChecked(self._settings.get("theme") == "dark_workshop")
        self._dark_theme_act.triggered.connect(lambda: self._apply_theme("dark_workshop"))
        self._theme_group.addAction(self._dark_theme_act)
        theme_menu.addAction(self._dark_theme_act)

        self._light_theme_act = QAction("Light Clean (Daylight)", self, checkable=True)
        self._light_theme_act.setChecked(self._settings.get("theme") == "light_clean")
        self._light_theme_act.triggered.connect(lambda: self._apply_theme("light_clean"))
        self._theme_group.addAction(self._light_theme_act)
        theme_menu.addAction(self._light_theme_act)

        # Tools Menu
        tools_menu = mb.addMenu("&Tools")
        baud_act = QAction("&Baud Rate Control...", self)
        baud_act.triggered.connect(self._show_baud_dialog)
        tools_menu.addAction(baud_act)

        pinout_act = QAction("&OBD-II 16-Pin Inspector...", self)
        pinout_act.triggered.connect(self._show_pinout_dialog)
        tools_menu.addAction(pinout_act)

        # Help Menu
        help_menu = mb.addMenu("&Help")
        update_act = QAction("Check for &Updates...", self)
        update_act.triggered.connect(lambda: self._check_updates(manual=True))
        help_menu.addAction(update_act)
        self._auto_update_act = QAction("Check Automatically on Startup", self, checkable=True)
        self._auto_update_act.setChecked(bool(self._settings.get("check_for_updates", True)))
        self._auto_update_act.toggled.connect(self._set_auto_update)
        help_menu.addAction(self._auto_update_act)
        help_menu.addSeparator()
        about_act = QAction("&About KLineScan", self)
        about_act.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_act)

    def _wire_signals(self) -> None:
        # Worker Connect/Disconnect signals
        self._sig_connect.connect(self._worker.connect_ecu)
        self._sig_disconnect.connect(self._worker.disconnect_ecu)
        self._sig_read_ecu_id.connect(self._worker.read_ecu_id)
        self._sig_full_scan.connect(self._worker.run_full_scan)
        self._sig_break.connect(self._worker.send_break_pulse)
        self._sig_start_recording.connect(self._worker.start_session_recording)
        self._sig_shutdown.connect(self._worker.shutdown)
        self._sig_identify.connect(self._worker.identify_vehicle)
        self._worker.identify_result.connect(self._on_identify_result)
        self._sig_detect_done.connect(self._on_detect_done)

        # Tab Actions -> Worker
        self._dtc_tab.read_requested.connect(self._worker.read_dtcs)
        self._dtc_tab.clear_requested.connect(self._worker.clear_dtcs)

        self._live_tab.polling_toggled.connect(self._worker.set_polling)
        self._live_tab.poll_interval_changed.connect(self._worker.set_poll_interval)

        self._ecu_tab.read_ecu_id_requested.connect(self._worker.read_ecu_id)
        self._ecu_tab.read_freeze_frame_requested.connect(self._worker.read_freeze_frame)
        self._ecu_tab.read_readiness_requested.connect(self._worker.read_readiness)

        self._act_tab.actuator_requested.connect(self._worker.run_actuator)
        self._act_tab.special_func_requested.connect(self._worker.run_special_function)

        self._console_tab.raw_send_requested.connect(self._worker.send_raw_bytes)

        # Hardware manual controls
        self._leds.l_line_toggled.connect(self._worker.toggle_l_line)
        self._leds.echo_toggled.connect(self._worker.toggle_echo_cancel)
        self._leds.break_requested.connect(lambda: self._sig_break.emit(25))

        # Playback Toolbar signals
        self._playback_bar.start_record_requested.connect(
            lambda path: self._sig_start_recording.emit(
                path, {"vehicle": self._current_profile.label if self._current_profile else ""})
        )
        self._playback_bar.stop_record_requested.connect(self._worker.stop_session_recording)
        self._playback_bar.start_playback_requested.connect(self._worker.start_session_playback)
        self._playback_bar.pause_playback_requested.connect(self._worker.pause_session_playback)
        self._playback_bar.resume_playback_requested.connect(self._worker.resume_session_playback)
        self._playback_bar.stop_playback_requested.connect(self._worker.stop_session_playback)
        self._playback_bar.seek_requested.connect(self._worker.seek_playback)
        self._playback_bar.speed_changed.connect(self._worker.set_playback_speed)

        # Worker -> GUI updates
        self._worker.connected_changed.connect(self._on_connected_changed)
        self._worker.led_changed.connect(self._leds.set_led)
        self._worker.baud_changed.connect(self._leds.set_baud)
        self._worker.frame_logged.connect(self._console_tab.append_frame)
        self._worker.dtc_results.connect(self._on_dtc_results)
        self._worker.pid_results.connect(self._on_pid_results)
        self._worker.ecu_id_result.connect(self._on_ecu_id_results)
        self._worker.freeze_frame_result.connect(self._on_ff_results)
        self._worker.readiness_result.connect(self._on_readiness_results)
        self._worker.hint_emitted.connect(self._hint_banner.show_hint)
        self._worker.playback_progress.connect(self._playback_bar.update_progress)
        self._worker.playback_state_changed.connect(self._playback_bar.set_playback_state)
        self._worker.playback_state_changed.connect(self._on_playback_state)
        self._worker.status_message.connect(self.statusBar().showMessage)
        self._worker.error_occurred.connect(self._on_error)

    def _on_vehicle_target_selected(self, target: EcuTarget, profile: VehicleProfile) -> None:
        self._current_target = target
        self._current_profile = profile
        self._leds.set_pin(target.pin)
        self._leds.set_baud(target.baudrate, target.baudrate)
        self._act_tab.set_target_capabilities(
            target.actuators, target.special_functions, target.confidence, target
        )
        self._top_bar.set_vehicle(f"{profile.label} › {target.name}")
        self._home.update_vehicle(profile.label, target.name, target.protocol.value)
        self._setup_drawer.hide()

    def _refresh_ports(self) -> None:
        self._port_cb.clear()
        interfaces = AutoDetector.scan_now()
        for iface in interfaces:
            self._port_cb.addItem(str(iface), iface.port)

    def _on_interfaces_discovered(self, interfaces: List[DiscoveredInterface]) -> None:
        # Update combo box without resetting current selection
        current_data = self._port_cb.currentData()
        self._port_cb.blockSignals(True)
        self._port_cb.clear()
        selected_idx = 0
        for idx, iface in enumerate(interfaces):
            self._port_cb.addItem(str(iface), iface.port)
            if iface.port == current_data:
                selected_idx = idx
        self._port_cb.setCurrentIndex(selected_idx)
        self._port_cb.blockSignals(False)

    def _current_port(self) -> str:
        data = self._port_cb.currentData()
        text = self._port_cb.currentText().strip()
        if data and (text == self._port_cb.itemText(self._port_cb.currentIndex())):
            return data
        return text.split(" — ")[0].strip() or "MOCK_PORT"

    # Adapter type reported by detection -> transport to use
    _DETECTED_TRANSPORT = {"ELM327": TransportType.ELM327, "KKL": TransportType.KL_SERIAL,
                           "KKL?": TransportType.KL_SERIAL, "MOCK": TransportType.FAKE,
                           "CAN": TransportType.SOCKETCAN, "J2534": TransportType.J2534,
                           "DOIP": TransportType.DOIP}

    def _detect_adapters(self) -> None:
        import threading

        self._refresh_btn.setEnabled(False)
        self.statusBar().showMessage("Detecting adapters (probing ports, Wi-Fi, BLE, DoIP)…")
        threading.Thread(target=lambda: self._sig_detect_done.emit(AutoDetector.detect(include_network=True)),
                         daemon=True).start()

    def _on_detect_done(self, interfaces: List[DiscoveredInterface]) -> None:
        self._refresh_btn.setEnabled(True)
        self._on_interfaces_discovered(interfaces)
        real = [i for i in interfaces if i.interface_type in self._DETECTED_TRANSPORT and i.interface_type != "MOCK"]
        if not real:
            self.statusBar().showMessage("No diagnostic adapter found — check USB/Bluetooth pairing or Wi-Fi.")
            return
        best = real[0]
        self._port_cb.setCurrentIndex(max(0, self._port_cb.findData(best.port)))
        transport = self._DETECTED_TRANSPORT[best.interface_type]
        self._transport_cb.setCurrentIndex(max(0, self._transport_cb.findData(transport)))
        self.statusBar().showMessage(f"Detected {best.interface_type} on {best.port} {best.firmware} {best.voltage}".strip())

    def _identify_vehicle(self) -> None:
        self._identify_btn.setEnabled(False)
        self.statusBar().showMessage("Identifying vehicle…")
        self._sig_identify.emit(self._transport_cb.currentData(), self._current_port())

    def _on_identify_result(self, result) -> None:
        self._identify_btn.setEnabled(True)
        lines = list(result.log)
        if result.ids:
            lines += [""] + [f"{i.label}: {i.value}" for i in result.ids]
        if result.supported_pids:
            lines.append(f"Live sensors supported: {len(result.supported_pids)}")
        if result.candidates:
            lines += ["", "Catalogue matches:"] + [
                f"  {c['brand']} {c['vehicle']}{' · ' + c['sub'] if c.get('sub') else ''} — {c['reason']}"
                for c in result.candidates[:8]]
        self._veh_selector.apply_identification(result)
        if result.connected and result.target is not None:
            label = "Generic OBD-II"
            if result.vin and result.vin.manufacturer:
                label = f"{result.vin.manufacturer} {result.vin.likely_year or ''} (VIN {result.vin.vin[-6:]})".strip()
            profile = VehicleProfile(label=label, make=result.vin.manufacturer if result.vin else "",
                                     year=str(result.vin.likely_year or "") if result.vin else "",
                                     system_type="OBD-II", targets=[result.target])
            self._on_vehicle_target_selected(result.target, profile)
        box = QMessageBox(self)
        box.setWindowTitle("Vehicle identification")
        box.setText("Connected — vehicle identified." if result.vin else
                    ("Connected, but the ECU did not report a VIN." if result.connected else "No OBD-II response."))
        box.setDetailedText("\n".join(lines))
        online = None
        if result.vin and result.vin.valid_format:
            online = box.addButton("Look up exact model online…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if online is not None and box.clickedButton() is online:
            self._lookup_vin_online(result.vin.vin)

    def _lookup_vin_online(self, vin: str) -> None:
        from klinescan.core.vin import vpic_lookup

        consent = QMessageBox.question(
            self, "Send VIN to NHTSA?",
            f"This sends the VIN {vin} to the US NHTSA vPIC web service (vpic.nhtsa.dot.gov) "
            "to decode make, model and trim. Continue?")
        if consent != QMessageBox.StandardButton.Yes:
            return
        try:
            data = vpic_lookup(vin)
        except Exception as exc:
            QMessageBox.warning(self, "Online lookup failed", str(exc))
            return
        QMessageBox.information(self, "NHTSA vPIC", "\n".join(f"{k}: {v}" for k, v in data.items()) or "No data")
        if data.get("Model"):
            self._veh_selector.set_search(f"{data.get('Make', '')} {data['Model']}")

    def _on_connect_clicked(self) -> None:
        t_type: TransportType = self._transport_cb.currentData()
        port: str = self._current_port()
        target: Optional[EcuTarget] = self._veh_selector.get_selected_target()
        if not target:
            QMessageBox.warning(self, "No Target", "Please select an ECU module target first.")
            return

        self._sig_connect.emit(t_type, port, target, self._current_baud_mode, self._custom_baud)

    def _on_connected_changed(self, connected: bool, msg: str) -> None:
        self._connect_btn.setEnabled(not connected)
        self._disconnect_btn.setEnabled(connected)
        self._transport_cb.setEnabled(not connected)
        self._port_cb.setEnabled(not connected)
        if not connected:
            self._live_tab.set_polling_checked(False)
        port = self._port_cb.currentData() or "adapter"
        self._top_bar.set_connection(connected, str(port))
        self._top_bar.set_primary("Run full health scan" if connected else "Connect")
        if connected:
            self._sig_read_ecu_id.emit()
        if self._current_profile and self._current_target:
            self._top_bar.set_vehicle(f"{self._current_profile.label} › {self._current_target.name}")
        self._home.update_connection(connected, str(port))
        self.statusBar().showMessage(msg)

    def _on_dtc_results(self, dtcs: List[DiagnosticTroubleCode]) -> None:
        self._last_dtcs = dtcs
        self._dtc_tab.set_dtcs(dtcs)
        self._home.update_health(len(dtcs))

    def _on_pid_results(self, pids: List[LivePid]) -> None:
        self._last_pids = pids
        self._live_tab.update_pids(pids)

    def _on_ecu_id_results(self, items: List[EcuIdItem]) -> None:
        self._last_ecu_ids = items
        self._ecu_tab.set_ecu_id(items)

    def _on_ff_results(self, ff: Optional[FreezeFrame]) -> None:
        self._last_ff = ff
        self._ecu_tab.set_freeze_frame(ff)

    def _on_readiness_results(self, monitors: List[ReadinessMonitor]) -> None:
        self._last_readiness = monitors
        self._ecu_tab.set_readiness(monitors)

    def _on_error(self, msg: str) -> None:
        self._live_tab.set_polling_checked(False)
        QMessageBox.critical(self, "Diagnostic Failure", msg)

    def _on_playback_state(self, is_playing: bool, is_paused: bool) -> None:
        active = is_playing or is_paused
        self._playback_bar.setVisible(active)
        self._dtc_tab.set_replay_locked(active)
        self._act_tab.set_replay_locked(active)
        if active:
            self.statusBar().showMessage("REPLAY — destructive actions are disabled.")

    def _show_baud_dialog(self) -> None:
        dlg = BaudDialog(self._current_baud_mode, self._custom_baud, self)
        if dlg.exec():
            self._current_baud_mode = dlg.panel.get_mode()
            self._custom_baud = dlg.panel.get_custom_baud()
            self._baud_config_btn.setText(f"Baud: {self._current_baud_mode.name[:4]}")

    def _show_pinout_dialog(self) -> None:
        pin = self._current_target.pin if self._current_target else 7
        dlg = ObdPinoutDialog(pin, self)
        dlg.exec()

    def _on_load_pack_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Diagnostic Pack", "", "Diagnostic Packs (*.json *.zip)"
        )
        if path:
            if path.endswith(".zip"):
                self._pack_manager._load_zip_pack(path)
            else:
                self._pack_manager._load_json_pack(path)
            self._veh_selector.refresh_packs()
            QMessageBox.information(self, "Pack Loaded", f"Loaded diagnostic pack from:\n{path}")

    def _export_health_report(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Diagnostic Report", "diagnostic_report.html", "HTML Files (*.html)"
        )
        if path:
            veh_name = self._current_profile.label if self._current_profile else "Generic Vehicle"
            mod_name = self._current_target.name if self._current_target else "Engine"
            html = generate_html_report(
                vehicle_label=veh_name,
                ecu_name=mod_name,
                ecu_ids=self._last_ecu_ids,
                dtcs=self._last_dtcs,
                freeze_frame=self._last_ff,
                readiness=self._last_readiness,
                live_pids=self._last_pids,
            )
            try:
                from klinescan.core.fileio import atomic_write_text

                atomic_write_text(path, html)
                QMessageBox.information(self, "Report Exported", f"Diagnostic report saved successfully to:\n{path}")
            except Exception as exc:
                QMessageBox.critical(self, "Export Failed", str(exc))

    def _take_screenshot(self) -> None:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        ss_dir = os.path.join(base_dir, "screenshots")
        os.makedirs(ss_dir, exist_ok=True)
        t_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ss_path = os.path.join(ss_dir, f"screenshot_{t_str}.png")

        screen = QApplication.primaryScreen()
        if screen:
            pixmap = screen.grabWindow(self.winId())
            pixmap.save(ss_path, "png")
            self.statusBar().showMessage(f"Screenshot saved: {os.path.basename(ss_path)}", 4000)

    def _apply_theme(self, theme_name: str) -> None:
        try:
            get_theme_manager().apply(theme_name)
            self._settings.set("theme", theme_name)
        except Exception as exc:
            self.statusBar().showMessage(f"Theme could not be loaded: {exc}")

    def _set_auto_update(self, enabled: bool) -> None:
        self._settings.set("check_for_updates", bool(enabled))

    def _check_updates(self, manual: bool) -> None:
        if self._update_thread is not None and self._update_thread.isRunning():
            return
        self._update_thread = UpdateCheckThread(self)
        self._update_thread.checked.connect(lambda info: self._on_update_checked(info, manual))
        self._update_thread.finished.connect(self._update_thread.deleteLater)
        self._update_thread.start()

    def _on_update_checked(self, info, manual: bool) -> None:
        if info.error:
            if manual:
                QMessageBox.information(self, "Update check", f"Could not check for updates.\n\n{info.error}")
            else:
                self.statusBar().showMessage("Update check skipped (offline or unavailable).", 4000)
            return
        if not info.available:
            if manual:
                QMessageBox.information(self, "Update check",
                                        f"You are running the latest version ({info.local}).")
            return
        dialog = UpdateDialog(info, self)
        if dialog.exec() != UpdateDialog.DialogCode.Accepted:
            return
        self._apply_update()

    def _apply_update(self) -> None:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._update_apply_thread = UpdateApplyThread(root, self)
        self._update_apply_thread.applied.connect(self._on_update_applied)
        self._update_apply_thread.finished.connect(self._update_apply_thread.deleteLater)
        self.statusBar().showMessage("Updating KLineScan...", 0)
        self._update_apply_thread.start()

    def _on_update_applied(self, ok: bool, message: str) -> None:
        if not ok:
            self.statusBar().clearMessage()
            QMessageBox.critical(self, "Update failed", message or "The update could not be applied.")
            return
        answer = QMessageBox.question(
            self, "Update complete",
            "KLineScan was updated. Restart now to use the new version?")
        if answer == QMessageBox.StandardButton.Yes:
            from klinescan.updates import restart_app
            QApplication.instance().quit()
            restart_app()
        else:
            self.statusBar().showMessage("Update installed; restart the app to use it.", 8000)

    def _show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            "About KLineScan",
            f"<h3>KLineScan v{__version__}</h3>"
            "<p>Native Python + PyQt6 automotive diagnostic suite for K-line, CAN and DoIP vehicles.</p>"
            "<p>Supports ISO 9141-2, KWP2000, ISO-TP, UDS, and KWP1281 protocols across "
            "KL Serial, FTDI Direct, ELM327, VAG K+CAN 1.4, J2534 PassThru, SocketCAN and DoIP hardware.</p>"
            "<p>Built for diagnostics, data logging and oscilloscope-style waveform inspection.</p>"
            f"<p>Version {__version__} · MIT License</p>",
        )

    def closeEvent(self, event) -> None:
        """Cooperative shutdown: abort blocking I/O, end the session, then stop the thread.

        ``cancel_current`` only sets a flag (safe across threads); transport read
        loops check it every <=100 ms, so the queued shutdown runs promptly and the
        serial port is released before the window goes away.
        """
        self._autodetector.stop()
        self._worker.cancel_current()
        self._sig_shutdown.emit()
        self._worker_thread.quit()
        if not self._worker_thread.wait(8000):
            self.statusBar().showMessage("Adapter did not release in time; closing anyway.")
            self._worker_thread.wait(2000)
        super().closeEvent(event)
