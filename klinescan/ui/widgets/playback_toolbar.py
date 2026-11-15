"""
Session Recording & Playback Toolbar with Timeline Scrubber and Speed Control.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)


class PlaybackToolbar(QWidget):
    start_record_requested = pyqtSignal(str)
    stop_record_requested = pyqtSignal()
    start_playback_requested = pyqtSignal(str)
    pause_playback_requested = pyqtSignal()
    resume_playback_requested = pyqtSignal()
    stop_playback_requested = pyqtSignal()
    seek_requested = pyqtSignal(float)
    speed_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_recording = False
        self._is_playing = False
        self._is_paused = False
        self._is_user_scrubbing = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        # Record Buttons
        self._rec_btn = QPushButton("REC")
        self._rec_btn.setObjectName("dangerBtn")
        self._rec_btn.setToolTip("Record frames, live data and DTCs to a CSV file")
        self._rec_btn.clicked.connect(self._on_rec_clicked)
        layout.addWidget(self._rec_btn)

        # Separator
        line = QLabel("|")
        line.setStyleSheet("color: #475569;")
        layout.addWidget(line)

        # Playback Controls
        self._open_btn = QPushButton("Open REC...")
        self._open_btn.clicked.connect(self._on_open_clicked)
        layout.addWidget(self._open_btn)

        self._play_btn = QPushButton("Play")
        self._play_btn.setObjectName("successBtn")
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._on_play_clicked)
        layout.addWidget(self._play_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self._stop_btn)

        # Speed Selector
        layout.addWidget(QLabel("Speed:"))
        self._speed_cb = QComboBox()
        self._speed_cb.addItem("0.5x", 0.5)
        self._speed_cb.addItem("1.0x", 1.0)
        self._speed_cb.addItem("2.0x", 2.0)
        self._speed_cb.addItem("5.0x", 5.0)
        self._speed_cb.setCurrentIndex(1)
        self._speed_cb.currentIndexChanged.connect(self._on_speed_changed)
        layout.addWidget(self._speed_cb)

        # Timeline Scrubber Slider
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 1000)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self._slider, 1)

        # Time Label
        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setStyleSheet("font-family: monospace; font-size: 11px; color: #94a3b8;")
        layout.addWidget(self._time_label)

    def _on_rec_clicked(self) -> None:
        if not self._is_recording:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Diagnostic Recording", "session.csv", "Session CSV (*.csv)"
            )
            if path:
                self._is_recording = True
                self._rec_btn.setText("STOP REC")
                self.start_record_requested.emit(path)
        else:
            self._is_recording = False
            self._rec_btn.setText("REC")
            self.stop_record_requested.emit()

    def _on_open_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Session Recording", "", "Session CSV (*.csv)"
        )
        if path:
            self._play_btn.setEnabled(True)
            self._stop_btn.setEnabled(True)
            self.start_playback_requested.emit(path)

    def _on_play_clicked(self) -> None:
        if self._is_playing and not self._is_paused:
            self.pause_playback_requested.emit()
            self._play_btn.setText("Resume")
            self._is_paused = True
        elif self._is_paused:
            self.resume_playback_requested.emit()
            self._play_btn.setText("Pause")
            self._is_paused = False
        else:
            self._on_open_clicked()

    def _on_stop_clicked(self) -> None:
        self.stop_playback_requested.emit()
        self._play_btn.setText("Play")
        self._is_playing = False
        self._is_paused = False
        self._slider.setValue(0)
        self._time_label.setText("00:00 / 00:00")

    def _on_speed_changed(self) -> None:
        speed = self._speed_cb.currentData()
        self.speed_changed.emit(speed)

    def _on_slider_pressed(self) -> None:
        self._is_user_scrubbing = True

    def _on_slider_released(self) -> None:
        self._is_user_scrubbing = False
        pct = self._slider.value() / 1000.0
        self.seek_requested.emit(pct)

    def update_progress(self, pct: float, cur_s: float, tot_s: float) -> None:
        if not self._is_user_scrubbing:
            self._slider.setValue(int(pct * 1000))
        cur_min, cur_sec = divmod(int(cur_s), 60)
        tot_min, tot_sec = divmod(int(tot_s), 60)
        self._time_label.setText(f"{cur_min:02d}:{cur_sec:02d} / {tot_min:02d}:{tot_sec:02d}")

    def set_playback_state(self, is_playing: bool, is_paused: bool) -> None:
        self._is_playing = is_playing
        self._is_paused = is_paused
        if is_playing:
            self._play_btn.setText("Pause")
            self._play_btn.setEnabled(True)
            self._stop_btn.setEnabled(True)
        elif is_paused:
            self._play_btn.setText("Resume")
        else:
            self._play_btn.setText("Play")
