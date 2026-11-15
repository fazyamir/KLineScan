"""Vehicle & ECU selector: searchable brand → vehicle → system → ECU browser.

Sources are the curated diagnostic packs in ``packs/``.  The public API is
``target_selected(EcuTarget, VehicleProfile)``, ``refresh_packs()``,
``get_selected_target()``, ``get_selected_profile()`` and
``selected_record_ids()``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from klinescan.core.ids import stable_id
from klinescan.core.models import EcuTarget, VehicleProfile
from klinescan.core.pack_manager import PackManager

SOURCE_ALL, SOURCE_PACKS = "all", "packs"
MAX_SEARCH_RESULTS = 600
ROLE_KIND = Qt.ItemDataRole.UserRole
ROLE_KEY = Qt.ItemDataRole.UserRole + 1


@dataclass
class _Entry:
    """One selectable leaf: a system/variant of a vehicle with one or more ECUs."""
    source: str
    brand: str
    vehicle: str
    sub: str
    system: str
    variant: tuple
    targets: list = field(default_factory=list)       # ready EcuTargets (packs)
    profile: Optional[VehicleProfile] = None
    files: tuple = ()
    text: str = ""

    @property
    def vehicle_label(self) -> str:
        return f"{self.vehicle} · {self.sub}" if self.sub else self.vehicle

    @property
    def leaf_label(self) -> str:
        return " › ".join(v for v in self.variant if v) or self.system or "Module"


_COLUMN_GAP = re.compile(r"\s{2,}")


def _tidy(text) -> str:
    """ROM name lists pad columns with spaces ("XG 300/350      ALL"); show them as "XG 300/350 · ALL"."""
    return _COLUMN_GAP.sub(" · ", str(text or "").strip())


def _fmt_hex(value, width=2) -> str:
    return f"0x{value:0{width}X}" if isinstance(value, int) else "—"


class VehicleSelectorWidget(QWidget):
    target_selected = pyqtSignal(object, object)  # EcuTarget, VehicleProfile

    def __init__(self, pack_manager: PackManager, parent=None):
        super().__init__(parent)
        self.pack_manager = pack_manager
        self._entries: List[_Entry] = []
        self._groups: Dict[str, Dict[str, Dict[str, List[int]]]] = {}
        self._current_entry: Optional[_Entry] = None
        self._selected_target: Optional[EcuTarget] = None
        self._selected_profile: Optional[VehicleProfile] = None

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._rebuild_tree)

        self._build_ui()
        self.refresh_packs()

    # ---------------------------------------------------------------- build

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        panel = QFrame()
        panel.setObjectName("card")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 12, 14, 14)
        panel_layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Vehicle & ECU")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        self._count_label = QLabel("")
        self._count_label.setObjectName("muted")
        header.addWidget(self._count_label)
        header.addStretch(1)
        self._source_cb = QComboBox()
        self._source_cb.setMinimumWidth(190)
        self._source_cb.addItem("Curated packs", SOURCE_PACKS)
        self._source_cb.currentIndexChanged.connect(lambda _: self._rebuild_tree())
        header.addWidget(self._source_cb)
        panel_layout.addLayout(header)

        ident_row = QHBoxLayout()
        self._ident_label = QLabel("")
        self._ident_label.setObjectName("chip")
        self._ident_label.setProperty("tone", "ok")
        self._ident_label.hide()
        ident_row.addWidget(self._ident_label, 1)
        self._ident_cb = QComboBox()
        self._ident_cb.setMinimumWidth(260)
        self._ident_cb.hide()
        self._ident_cb.currentIndexChanged.connect(self._on_ident_candidate)
        ident_row.addWidget(self._ident_cb)
        panel_layout.addLayout(ident_row)

        self._search_input = QLineEdit()
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setPlaceholderText("Search brand, model, year, system, ECU ID or protocol — e.g. “sonata abs”, “7E0”")
        self._search_input.textChanged.connect(lambda _: self._search_timer.start())
        panel_layout.addWidget(self._search_input)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        self._tree = QTreeWidget()
        self._tree.setObjectName("catalogTree")
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Vehicle / system", ""])
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(False)
        self._tree.setMinimumHeight(300)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        self._tree.currentItemChanged.connect(self._on_current_item_changed)
        self._tree.itemDoubleClicked.connect(lambda item, _col: self._use_selected())
        splitter.addWidget(self._tree)

        details = QScrollArea()
        details.setWidgetResizable(True)
        details.setFrameShape(QFrame.Shape.NoFrame)
        details.setMinimumWidth(320)
        details.setWidget(self._build_details())
        splitter.addWidget(details)
        splitter.setMinimumHeight(340)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([560, 380])
        panel_layout.addWidget(splitter, 1)
        root.addWidget(panel)

    def _build_details(self) -> QWidget:
        box = QFrame()
        box.setObjectName("stepCard")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)

        self._detail_title = QLabel("Select a vehicle system")
        self._detail_title.setObjectName("detailTitle")
        self._detail_title.setWordWrap(True)
        layout.addWidget(self._detail_title)
        self._detail_path = QLabel("Browse or search on the left, then choose an ECU.")
        self._detail_path.setObjectName("muted")
        self._detail_path.setWordWrap(True)
        layout.addWidget(self._detail_path)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._chip_bus = QLabel("")
        self._chip_bus.setObjectName("chip")
        self._chip_conf = QLabel("")
        self._chip_conf.setObjectName("chip")
        self._chip_support = QLabel("")
        self._chip_support.setObjectName("chip")
        for chip in (self._chip_bus, self._chip_conf, self._chip_support):
            chip.hide()
            chips.addWidget(chip)
        chips.addStretch(1)
        layout.addLayout(chips)

        self._ecu_cb = QComboBox()
        self._ecu_cb.currentIndexChanged.connect(lambda _: self._show_ecu())
        self._ecu_cb.hide()
        layout.addWidget(self._ecu_cb)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(5)
        self._fields: Dict[str, QLabel] = {}
        for row, (key, label) in enumerate([
            ("protocol", "Protocol"), ("init", "Init"), ("baud", "Baud"), ("address", "Address"),
            ("tester", "Tester"), ("header", "Frame"), ("ecu", "ECU profile"), ("source", "Source"),
        ]):
            name = QLabel(label)
            name.setObjectName("detailKey")
            value = QLabel("—")
            value.setObjectName("detailValue")
            value.setWordWrap(True)
            value.setMinimumHeight(18)
            name.setMinimumHeight(18)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(name, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
            self._fields[key] = value
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addStretch(1)

        self._note = QLabel("")
        self._note.setObjectName("muted")
        self._note.setWordWrap(True)
        layout.addWidget(self._note)

        self._use_btn = QPushButton("Use this ECU")
        self._use_btn.setObjectName("primaryBtn")
        self._use_btn.setEnabled(False)
        self._use_btn.clicked.connect(self._use_selected)
        layout.addWidget(self._use_btn)
        return box

    # --------------------------------------------------------------- data

    def refresh_packs(self) -> None:
        entries: List[_Entry] = []
        for pack in self.pack_manager.packs.values():
            for profile in pack.vehicles:
                for target in profile.targets:
                    entries.append(_Entry(
                        source=SOURCE_PACKS, brand=profile.make or pack.name,
                        vehicle=profile.model or profile.label, sub=profile.year or "",
                        system=profile.system_type or "Modules", variant=(target.name,),
                        targets=[target], profile=profile, files=(pack.name,)))
        for e in entries:
            ecu_text = " ".join(t.protocol.value for t in e.targets)
            e.text = " ".join([e.brand, e.vehicle, e.sub, e.system, *e.variant, ecu_text]).lower()
        self._entries = entries
        self._rebuild_tree()

    def _visible_indices(self) -> List[int]:
        # words, or "quoted phrases" that must appear as a whole
        terms = [a or b for a, b in re.findall(r'"([^"]+)"|(\S+)', self._search_input.text().strip().lower())]
        out = []
        for i, e in enumerate(self._entries):
            if terms and not all(t in e.text for t in terms):
                continue
            out.append(i)
        return out

    def _rebuild_tree(self) -> None:
        indices = self._visible_indices()
        searching = bool(self._search_input.text().strip())
        groups: Dict[str, Dict[str, Dict[str, List[int]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for i in indices[:MAX_SEARCH_RESULTS] if searching else indices:
            e = self._entries[i]
            groups[e.brand][e.vehicle_label][e.system or "—"].append(i)
        self._groups = groups

        self._tree.setUpdatesEnabled(False)
        self._tree.clear()
        for brand in sorted(groups):
            vehicles = groups[brand]
            item = QTreeWidgetItem([brand, str(len(vehicles))])
            item.setData(0, ROLE_KIND, "brand")
            item.setData(0, ROLE_KEY, (brand,))
            item.setToolTip(0, f"{brand}: {len(vehicles)} vehicles")
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            self._tree.addTopLevelItem(item)
            if searching:
                item.setExpanded(True)
        if not groups:
            empty = QTreeWidgetItem(["No matches — try fewer or different words", ""])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._tree.addTopLevelItem(empty)
        self._tree.setUpdatesEnabled(True)

        n_vehicles = sum(len(v) for v in groups.values())
        if searching and len(indices) > MAX_SEARCH_RESULTS:
            self._count_label.setText(f"{len(indices):,} matches · showing first {MAX_SEARCH_RESULTS} — refine the search")
        else:
            self._count_label.setText(f"{len(groups)} brands · {n_vehicles:,} vehicles · {len(indices):,} systems")
        if searching and self._tree.topLevelItemCount() <= 3:
            for i in range(self._tree.topLevelItemCount()):
                self._expand_all(self._tree.topLevelItem(i))
        self._show_entry(None)

    def _expand_all(self, item: QTreeWidgetItem) -> None:
        item.setExpanded(True)
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, ROLE_KIND) in ("brand", "vehicle"):
                self._expand_all(child)

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        if item.childCount():
            return
        kind = item.data(0, ROLE_KIND)
        key = item.data(0, ROLE_KEY)
        if kind == "brand":
            for vehicle in sorted(self._groups[key[0]], key=lambda s: s.lower()):
                systems = self._groups[key[0]][vehicle]
                child = QTreeWidgetItem([vehicle, str(len(systems))])
                child.setData(0, ROLE_KIND, "vehicle")
                child.setData(0, ROLE_KEY, (key[0], vehicle))
                child.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                item.addChild(child)
        elif kind == "vehicle":
            for system, idxs in self._groups[key[0]][key[1]].items():
                if len(idxs) == 1 and not any(self._entries[idxs[0]].variant):
                    child = self._leaf(system, idxs[0])
                else:
                    child = QTreeWidgetItem([system, str(len(idxs))])
                    child.setData(0, ROLE_KIND, "system")
                    child.setData(0, ROLE_KEY, (key[0], key[1], system))
                    child.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                item.addChild(child)
        elif kind == "system":
            for idx in self._groups[key[0]][key[1]][key[2]]:
                item.addChild(self._leaf(self._entries[idx].leaf_label, idx))

    def _leaf(self, label: str, idx: int) -> QTreeWidgetItem:
        e = self._entries[idx]
        buses = sorted({"K-line" if t.pin != 6 else "CAN" for t in e.targets})
        leaf = QTreeWidgetItem([label, " / ".join(buses)])
        leaf.setData(0, ROLE_KIND, "entry")
        leaf.setData(0, ROLE_KEY, idx)
        return leaf

    # ------------------------------------------------------------ details

    def _on_current_item_changed(self, current: Optional[QTreeWidgetItem], _previous) -> None:
        if current is not None and current.data(0, ROLE_KIND) == "entry":
            self._show_entry(self._entries[current.data(0, ROLE_KEY)])
        else:
            self._show_entry(None)

    def _show_entry(self, entry: Optional[_Entry]) -> None:
        self._current_entry = entry
        self._ecu_cb.blockSignals(True)
        self._ecu_cb.clear()
        if entry is None:
            self._detail_title.setText("Select a vehicle system")
            self._detail_path.setText("Browse or search on the left, then choose an ECU.")
            self._ecu_cb.hide()
            self._ecu_cb.blockSignals(False)
            self._show_ecu()
            return
        self._detail_title.setText(f"{entry.brand} {entry.vehicle_label}")
        self._detail_path.setText(" › ".join(p for p in (entry.system, *entry.variant) if p))
        for t in entry.targets:
            self._ecu_cb.addItem(t.name, ("target", t))
        self._ecu_cb.setVisible(self._ecu_cb.count() > 1)
        self._ecu_cb.blockSignals(False)
        self._show_ecu()

    def _set_chip(self, chip: QLabel, text: str, tone: str) -> None:
        chip.setText(text)
        chip.setProperty("tone", tone)
        chip.style().unpolish(chip)
        chip.style().polish(chip)
        chip.setVisible(bool(text))

    def _show_ecu(self) -> None:
        entry = self._current_entry
        data = self._ecu_cb.currentData() if entry else None
        for value in self._fields.values():
            value.setText("—")
        for chip in (self._chip_bus, self._chip_conf, self._chip_support):
            chip.hide()
        self._note.setText("")
        self._pending = None
        if not entry or not data:
            self._use_btn.setEnabled(False)
            return
        kind, obj = data
        if kind == "target":
            target: EcuTarget = obj
            profile = entry.profile
            self._fill_target(target)
            self._fields["source"].setText(", ".join(entry.files))
            self._set_chip(self._chip_bus, "CAN" if target.pin == 6 else "K-line", "info")
            self._set_chip(self._chip_conf, target.confidence.upper(), "ok" if target.confidence == "curated" else "warn")
            self._set_chip(self._chip_support, "Ready", "ok")
            self._pending = (target, profile)
        self._use_btn.setEnabled(self._pending is not None)

    def _fill_target(self, target: EcuTarget) -> None:
        self._fields["protocol"].setText(target.protocol.value)
        self._fields["baud"].setText(f"{target.baudrate:,} bps")
        if target.pin == 6:
            rx = _fmt_hex(target.can_rx_id, 3) if target.can_rx_id else "auto-detect"
            ext = f" · ext {_fmt_hex(target.can_ext_address)}" if target.can_ext_address is not None else ""
            self._fields["address"].setText(f"CAN TX {_fmt_hex(target.can_tx_id, 3)} / RX {rx}{ext}")
            self._fields["init"].setText("None")
        else:
            self._fields["address"].setText(f"{_fmt_hex(target.ecu_addr)} on pin {target.pin}")
            self._fields["init"].setText(
                f"5-baud slow init → {_fmt_hex(target.slow_init_addr)}" if "Slow" in target.protocol.value else "Fast init")
        self._fields["tester"].setText(_fmt_hex(target.tester_addr))
        self._fields["header"].setText("68 6A F1 (OBD)" if target.use_obd_header else _fmt_hex(target.header_format))
        self._fields["ecu"].setText(target.name)

    def _use_selected(self) -> None:
        if not getattr(self, "_pending", None):
            return
        target, profile = self._pending
        self._selected_target, self._selected_profile = target, profile
        self.target_selected.emit(target, profile)

    # ------------------------------------------------------------ public API
    def set_search(self, text: str) -> None:
        self._search_input.setText(text)
        self._rebuild_tree()

    def apply_identification(self, result) -> None:
        """Show what auto-identification found and narrow the tree to it."""
        if result.vin and result.vin.manufacturer:
            self._ident_label.setText(
                f"Identified: {result.vin.manufacturer} {result.vin.likely_year or ''} · VIN {result.vin.vin}"
                + (f" · {result.ecu_name}" if result.ecu_name else "")
                + f" · {len(result.candidates)} catalogue matches")
            self._ident_label.show()
            self._ident_cb.blockSignals(True)
            self._ident_cb.clear()
            self._ident_cb.addItem("Matching vehicles…", None)
            for c in result.candidates:
                label = f"{c['brand']} — {_tidy(c['vehicle'])}" + (f" · {_tidy(c['sub'])}" if c.get("sub") else "")
                self._ident_cb.addItem(label, c)
            self._ident_cb.setVisible(bool(result.candidates))
            self._ident_cb.blockSignals(False)
            if result.candidates:
                self._on_ident_candidate(1)
                self._ident_cb.setCurrentIndex(1)
        elif result.connected:
            self._ident_label.setText("Connected with generic OBD-II, but no VIN was reported — pick the vehicle manually.")
            self._ident_label.show()

    def _on_ident_candidate(self, index: int) -> None:
        cand = self._ident_cb.itemData(index)
        if cand:
            self.set_search(f'"{cand["brand"]}" "{_tidy(cand["vehicle"])}"')


    def selected_record_ids(self) -> tuple[str, str]:
        """Return stable IDs for catalog/provenance and diagnostics logging."""
        profile = self.get_selected_profile()
        target = self.get_selected_target()
        if not profile or not target:
            return "", ""
        vehicle_id = stable_id(profile.make, profile.model, profile.year, profile.label)
        module_id = stable_id(vehicle_id, target.name, target.protocol.value, str(target.ecu_addr))
        return vehicle_id, module_id

    def get_selected_target(self) -> Optional[EcuTarget]:
        return self._selected_target

    def get_selected_profile(self) -> Optional[VehicleProfile]:
        return self._selected_profile
