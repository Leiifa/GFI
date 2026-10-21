from __future__ import annotations

import argparse
import math
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QPointF, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .compare import CompareSidebar
from .draft import (
    SaveDraftWorker,
    allocate_draft,
    cleanup_drafts,
    copy_draft,
    is_draft,
    remove_draft,
)
from .ffmpeg import HDR_ENCODERS, TEN_BIT_ENCODERS, find_tool
from .paths import ensure_data_root, is_portable, resource_root
from .pipeline import CancelledError, Job, run_job
from .probe import MediaInfo, probe
from .rife import RifeModel

REPO_ROOT = resource_root()
ASSETS_DIR = REPO_ROOT / "assets"
LOGO_PATH = ASSETS_DIR / "gfi-logo.png"
DEFAULT_MODEL = REPO_ROOT / "models" / "RIFE_fp32_timestep.onnx"
ANIME_MODEL = REPO_ROOT / "models" / "RIFE_4.9.onnx"
PROFILE_MODELS = {"live": DEFAULT_MODEL, "anime": ANIME_MODEL}
PROFILE_LABELS = {
    "live": "Live action / general",
    "anime": "Anime / cartoon",
}
MAX_OUTPUT_FPS = 120.0  # GUI cap; the CLI stays flexible.
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v",
    ".ts", ".mts", ".m2ts", ".wmv", ".flv", ".mpg", ".mpeg",
}
ENCODERS = [
    ("H.264 — CPU (most compatible)", "libx264"),
    ("HEVC — CPU (10-bit / HDR)", "libx265"),
    ("HEVC — AMD (fast, 10-bit / HDR)", "hevc_amf"),
    ("AV1 — AMD (smallest, 10-bit / HDR)", "av1_amf"),
    ("H.264 — AMD (fastest, 8-bit only)", "h264_amf"),
]

STYLESHEET = """
QWidget {
    background: #0e1116;
    color: #e7ebf3;
    font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
    font-size: 10pt;
}
QLabel, QSlider, QCheckBox { background: transparent; }
QMainWindow { background: #0e1116; }
QFrame#card {
    background: #151a22;
    border: 1px solid #232a36;
    border-radius: 12px;
}
QFrame#dropZone {
    background: #12171f;
    border: 2px dashed #2c3646;
    border-radius: 12px;
}
QFrame#dropZone[dragActive="true"] {
    border-color: #4fd1c5;
    background: #14211f;
}
QLabel#title { font-size: 26pt; font-weight: 700; color: #f4f7fb; }
QLabel#subtitle { color: #8b95a7; font-size: 10pt; }
QLabel#dropTitle { font-size: 13pt; font-weight: 600; color: #dfe5ee; }
QLabel#dropHint { color: #8b95a7; font-size: 9pt; }
QLabel#fileName { font-size: 13pt; font-weight: 600; color: #f4f7fb; }
QLabel#fieldLabel { color: #aab4c5; font-weight: 500; }
QLabel#muted { color: #8b95a7; }
QLabel#hint { color: #7d8798; font-size: 8.5pt; }
QLabel#accent { color: #4fd1c5; font-weight: 600; }
QLabel#sectionTitle {
    color: #4fd1c5;
    font-size: 8.5pt;
    font-weight: 700;
}
QLabel#status { color: #aab4c5; }
QLabel#warning { color: #fbbf24; font-size: 9pt; }
QLabel#chip {
    background: #1b2230;
    border: 1px solid #2a3341;
    border-radius: 5px;
    color: #aab4c5;
    font-size: 8pt;
    font-weight: 600;
    padding: 2px 7px;
}
QLabel#chip[chipKind="depth"] {
    background: #14211f;
    border-color: #2f4a47;
    color: #4fd1c5;
}
QLabel#chip[chipKind="hdr"] {
    background: #2a2314;
    border-color: #59461e;
    color: #fbbf24;
}
QLabel#chip[chipKind="warn"] {
    background: #2a1a1c;
    border-color: #5a2a2e;
    color: #f87171;
}
QLabel#outputBadge {
    background: #14211f;
    border: 1px solid #2f4a47;
    border-radius: 6px;
    color: #4fd1c5;
    font-size: 8pt;
    font-weight: 600;
    padding: 3px 8px;
}
QComboBox, QLineEdit, QDoubleSpinBox {
    background: #0f141b;
    border: 1px solid #2a3341;
    border-radius: 7px;
    padding: 7px 10px;
    selection-background-color: #4fd1c5;
    selection-color: #062522;
}
QComboBox:hover, QLineEdit:hover, QDoubleSpinBox:hover { border-color: #3a4658; }
QComboBox:focus, QLineEdit:focus, QDoubleSpinBox:focus { border-color: #4fd1c5; }
QComboBox:disabled, QLineEdit:disabled, QDoubleSpinBox:disabled {
    color: #5f6878;
    border-color: #1f2632;
    background: #0c1015;
}
QSlider:disabled::groove:horizontal { background: #1f2632; }
QSlider:disabled::sub-page:horizontal { background: #2f4a47; }
QSlider:disabled::handle:horizontal { background: #5f6878; }
QPushButton:disabled { color: #5f6878; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #151a22;
    border: 1px solid #2a3341;
    selection-background-color: #1f3a38;
    selection-color: #e7ebf3;
    outline: none;
}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 0;
    height: 0;
    border: none;
}
QPushButton {
    background: #1b2230;
    border: 1px solid #2a3341;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 600;
}
QPushButton:hover { background: #222b3b; border-color: #3a4658; }
QPushButton:pressed { background: #161c27; }
QPushButton#primary {
    background: #4fd1c5;
    color: #062522;
    border: none;
    padding: 10px 26px;
    font-size: 11pt;
}
QPushButton#primary:hover { background: #63e0d4; }
QPushButton#primary:disabled { background: #263735; color: #6f7f7f; }
QPushButton#danger { color: #f87171; }
QPushButton#danger:hover { background: #2a1a1c; border-color: #5a2a2e; }
QPushButton#secondary {
    background: #14211f;
    border: 1px solid #2f4a47;
    color: #4fd1c5;
}
QPushButton#secondary:hover { background: #1a2b28; border-color: #4fd1c5; }
QPushButton#secondary[active="true"] {
    background: #14211f;
    border-color: #4fd1c5;
    color: #4fd1c5;
}
QPushButton#secondary:disabled {
    background: #10151c;
    border-color: #1f2632;
    color: #5f6878;
}
QPushButton#ghost {
    background: transparent;
    border: none;
    color: #8b95a7;
    padding: 4px 8px;
    font-weight: 500;
}
QPushButton#ghost:hover { color: #e7ebf3; }
QSlider::groove:horizontal {
    height: 4px;
    background: #2a3341;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #4fd1c5;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #e7ebf3;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSplitter#workspace::handle:horizontal {
    background: transparent;
    width: 10px;
}
QSplitter#workspace::handle:horizontal:hover {
    background: #1b2330;
    border-radius: 4px;
}
QPlainTextEdit {
    background: #0b0f14;
    border: 1px solid #1f2632;
    border-radius: 8px;
    color: #8b95a7;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 9pt;
    padding: 6px;
}
QMessageBox { background: #151a22; }
QMessageBox QLabel { color: #e7ebf3; }
QToolTip {
    background: #151a22;
    color: #e7ebf3;
    border: 1px solid #2a3341;
    padding: 4px 6px;
}
"""


def _fmt_eta(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        return "--:--"
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def _fmt_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _fmt_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _fmt_bitrate(bits_per_second: int) -> str:
    if bits_per_second <= 0:
        return ""
    return f"{bits_per_second / 1_000_000:.1f} Mb/s"


def _is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def _path_signature(path: Path) -> tuple[int, int] | None:
    """Identify the file version used by a completed job."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _first_video_from_mime(mime) -> Path | None:
    if not mime.hasUrls():
        return None
    for url in mime.urls():
        if url.isLocalFile():
            path = Path(url.toLocalFile())
            if path.is_file() and _is_video(path):
                return path
    return None


def _make_icon() -> QIcon:
    """Fallback mark used only when the packaged logo asset is unavailable."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#4fd1c5"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setBrush(QColor("#062522"))
    painter.drawPolygon([QPointF(24, 18), QPointF(24, 46), QPointF(48, 32)])
    painter.end()
    return QIcon(pixmap)


def _app_icon() -> QIcon:
    if LOGO_PATH.is_file():
        icon = QIcon(str(LOGO_PATH))
        if not icon.isNull():
            return icon
    return _make_icon()


def _logo_pixmap(size: int) -> QPixmap:
    if LOGO_PATH.is_file():
        source = QPixmap(str(LOGO_PATH))
        if not source.isNull():
            return source.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    return _make_icon().pixmap(size, size)


class DropZone(QFrame):
    browseRequested = Signal()
    fileDropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(112)
        self.setProperty("dragActive", False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(4)

        title = QLabel("Drop a video here")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle = QLabel(
            "or click to browse  ·  MP4, MKV, MOV, WebM  ·  10-bit / HDR ready"
        )
        subtitle.setObjectName("dropHint")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch(1)

    def _set_drag(self, active: bool) -> None:
        self.setProperty("dragActive", active)
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    def _first_video(self, mime) -> Path | None:
        return _first_video_from_mime(mime)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.browseRequested.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._first_video(event.mimeData()) is not None:
            event.acceptProposedAction()
            self._set_drag(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_drag(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_drag(False)
        path = self._first_video(event.mimeData())
        if path is not None:
            event.acceptProposedAction()
            self.fileDropped.emit(str(path))
        else:
            event.ignore()


class Worker(QThread):
    progress = Signal(int, int, int, float)
    log = Signal(str)
    completed = Signal(dict)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        job: Job,
        info: MediaInfo,
        model_path: Path,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self._job = job
        self._info = info
        self._model_path = model_path
        self._cancel_event = cancel_event
        job.progress = self._emit_progress
        job.log = self.log.emit
        job.cancel = cancel_event

    def _emit_progress(self, done: int, total: int, produced: int, elapsed: float) -> None:
        self.progress.emit(done, total, produced, elapsed)

    def run(self) -> None:
        try:
            model = RifeModel(self._model_path, provider="DmlExecutionProvider")
            stats = run_job(self._job, model, self._info, find_tool("ffmpeg"))
            self.completed.emit(stats)
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - surfaced in the window
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self, autostart: bool = False, exit_on_finish: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("GFI — Generate Frame Inbetween")
        self.setWindowIcon(_app_icon())
        self.resize(1720, 860)
        self.setMinimumSize(1240, 700)
        self.setAcceptDrops(True)

        self.settings = (
            QSettings(str(ensure_data_root() / "settings.ini"), QSettings.Format.IniFormat)
            if is_portable()
            else QSettings("GFI", "GFI")
        )
        self._migrate_legacy_settings()
        self.input_path: Path | None = None
        self.info: MediaInfo | None = None
        self.worker: Worker | None = None
        self.cancel_event = threading.Event()
        self.last_output: Path | None = None
        self.last_job_source: Path | None = None
        self.last_job_info: MediaInfo | None = None
        self.last_job_stats: dict | None = None
        self.last_job_source_signature: tuple[int, int] | None = None
        self.last_job_output_signature: tuple[int, int] | None = None
        self._draft_saved_path: Path | None = None
        self._draft_display_name = ""
        self._pending_output: Path | None = None
        self._pending_source: Path | None = None
        self._pending_info: MediaInfo | None = None
        self._pending_source_signature: tuple[int, int] | None = None
        self._pending_display_name = ""
        self._save_worker: SaveDraftWorker | None = None
        self._save_destination: Path | None = None
        self._initial_panel_width = 960
        self.autostart = autostart
        self.exit_on_finish = exit_on_finish
        self.preferred_rate_mode = "mult"
        self.preferred_multiplier = 2
        self.preferred_profile = "live"
        self.preferred_hdr_mode = "preserve"

        self._build_ui()
        self._load_settings()
        self._install_shortcuts()

    def _migrate_legacy_settings(self) -> None:
        """Keep the previous app's saved window and encoder choices."""
        if is_portable():
            return
        legacy = QSettings("Inbetween", "Inbetween")
        legacy_keys = legacy.allKeys()
        if not legacy_keys:
            return
        for key in legacy_keys:
            if not self.settings.contains(key):
                self.settings.setValue(key, legacy.value(key))
        self.settings.sync()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 14)
        root.setSpacing(10)

        root.addLayout(self._build_header())

        self.workspace = QSplitter(Qt.Orientation.Horizontal)
        self.workspace.setObjectName("workspace")
        self.workspace.setChildrenCollapsible(False)
        self.workspace.setHandleWidth(10)

        self.left_panel = QWidget()
        self.left_panel.setObjectName("leftPanel")
        self.left_panel.setMinimumWidth(620)
        left = QVBoxLayout(self.left_panel)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)

        self.drop_zone = DropZone()
        self.drop_zone.browseRequested.connect(self.browse_input)
        self.drop_zone.fileDropped.connect(self._on_file_dropped)
        self.drop_zone.setToolTip(
            "Drop a video here, or click to browse. 10-bit and HDR sources are supported."
        )
        left.addWidget(self.drop_zone)

        self.info_card = self._build_info_card()
        self.info_card.setVisible(False)
        self.change_button.clicked.connect(self.browse_input)
        left.addWidget(self.info_card)

        left.addWidget(self._build_settings_card())
        left.addLayout(self._build_actions())
        left.addStretch(1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setPlaceholderText("Details will appear here.")
        self.log.setMaximumHeight(96)
        self.log.setMinimumHeight(72)
        self.log.setVisible(False)
        left.addWidget(self.log)
        left.addWidget(self._build_status_card())

        self.workspace.addWidget(self.left_panel)
        self.workspace.setStretchFactor(0, 0)

        self.preview_panel = CompareSidebar()
        self.preview_panel.saveRequested.connect(self.save_video)
        self.workspace.addWidget(self.preview_panel)
        self.workspace.setStretchFactor(1, 1)
        root.addWidget(self.workspace, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(2, 0, 2, 0)
        tagline = QLabel(
            "GFI · RIFE · 10-bit / HDR · DirectML · everything stays on this PC"
        )
        tagline.setObjectName("muted")
        front = QFont()
        front.setPointSizeF(8.5)
        tagline.setFont(front)
        version = QLabel(f"v{__version__}")
        version.setObjectName("muted")
        version.setFont(front)
        footer.addWidget(tagline)
        footer.addStretch(1)
        footer.addWidget(version)
        root.addLayout(footer)

        self.details_button.toggled.connect(self._toggle_details)

    def _install_shortcuts(self) -> None:
        for sequence, slot in (
            (QKeySequence.StandardKey.Open, self.browse_input),
            (QKeySequence.StandardKey.Save, self.save_video),
            (QKeySequence("Ctrl+Return"), self.start),
            (QKeySequence("Escape"), self._handle_escape),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(slot)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(13)

        icon = QLabel()
        icon.setFixedSize(48, 48)
        icon.setPixmap(_logo_pixmap(48))
        icon.setToolTip("GFI — Generate Frame Inbetween")

        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("GFI")
        title.setObjectName("title")
        subtitle = QLabel("Smoother video, locally on your GPU")
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)

        self.details_button = QPushButton("Details")
        self.details_button.setObjectName("ghost")
        self.details_button.setCheckable(True)
        self.details_button.setCursor(Qt.CursorShape.PointingHandCursor)

        header.addWidget(icon)
        header.addLayout(titles)
        header.addStretch(1)
        header.addWidget(self.details_button, 0, Qt.AlignmentFlag.AlignTop)
        return header

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _build_info_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(14)

        badge = QLabel("VIDEO")
        badge.setObjectName("accent")
        badge.setFixedWidth(52)

        text = QVBoxLayout()
        text.setSpacing(3)
        self.file_name_label = QLabel("")
        self.file_name_label.setObjectName("fileName")
        self.file_details_label = QLabel("")
        self.file_details_label.setObjectName("muted")
        self.source_chip_layout = QHBoxLayout()
        self.source_chip_layout.setContentsMargins(0, 0, 0, 0)
        self.source_chip_layout.setSpacing(6)
        self.source_warning_label = QLabel("")
        self.source_warning_label.setObjectName("warning")
        self.source_warning_label.setWordWrap(True)
        self.source_warning_label.setVisible(False)
        text.addWidget(self.file_name_label)
        text.addWidget(self.file_details_label)
        text.addLayout(self.source_chip_layout)
        text.addWidget(self.source_warning_label)

        self.change_button = QPushButton("Replace")
        self.change_button.setObjectName("ghost")
        self.change_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change_button.setToolTip("Choose a different video (Ctrl+O).")
        self.change_button.setVisible(False)

        layout.addWidget(badge)
        layout.addLayout(text, 1)
        layout.addWidget(self.change_button, 0, Qt.AlignmentFlag.AlignTop)
        return card

    def _build_settings_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(18, 14, 18, 16)
        outer.setSpacing(10)

        heading_row = QHBoxLayout()
        heading_row.setSpacing(8)
        heading = QLabel("OUTPUT SETTINGS")
        heading.setObjectName("sectionTitle")
        self.output_badge = QLabel("AUTO · 8-BIT")
        self.output_badge.setObjectName("outputBadge")
        self.output_badge.setToolTip("The engine picks the output depth automatically from the source.")
        heading_row.addWidget(heading)
        heading_row.addStretch(1)
        heading_row.addWidget(self.output_badge)
        outer.addLayout(heading_row)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)

        grid.addWidget(self._label("Style"), 0, 0)
        profile_box = QVBoxLayout()
        profile_box.setSpacing(2)
        self.profile_combo = QComboBox()
        for key, label in PROFILE_LABELS.items():
            self.profile_combo.addItem(label, key)
        self.profile_combo.setMinimumWidth(230)
        self.profile_combo.setMinimumHeight(34)
        self.profile_combo.setToolTip(
            "Live action uses the general RIFE model. Anime / cartoon uses RIFE 4.9 "
            "and a lower scene-cut threshold for hard animation cuts."
        )
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        self.profile_hint = QLabel("")
        self.profile_hint.setObjectName("hint")
        profile_box.addWidget(self.profile_combo)
        profile_box.addWidget(self.profile_hint)
        grid.addLayout(profile_box, 0, 1)

        grid.addWidget(self._label("Frame rate"), 1, 0)
        factor_row = QHBoxLayout()
        factor_row.setSpacing(10)
        self.factor_combo = QComboBox()
        self.factor_combo.addItem("2×", 2)
        self.factor_combo.addItem("3×", 3)
        self.factor_combo.addItem("4×", 4)
        self.factor_combo.addItem("Desired FPS…", None)
        self.factor_combo.setMinimumWidth(190)
        self.factor_combo.setMinimumHeight(34)
        self.factor_combo.setToolTip(
            f"Pick a multiplier or an exact output frame rate (up to {MAX_OUTPUT_FPS:g} fps)."
        )
        self.factor_combo.currentIndexChanged.connect(self._factor_changed)

        self.target_spin = QDoubleSpinBox()
        self.target_spin.setRange(1.0, MAX_OUTPUT_FPS)
        self.target_spin.setDecimals(0)
        self.target_spin.setSingleStep(1.0)
        self.target_spin.setValue(MAX_OUTPUT_FPS)
        self.target_spin.setSuffix(" fps")
        self.target_spin.setFixedWidth(100)
        self.target_spin.setVisible(False)
        self.target_spin.setToolTip(f"Output frame rate, up to {MAX_OUTPUT_FPS:g} fps.")
        self.target_spin.valueChanged.connect(self._factor_changed)

        self.result_label = QLabel("")
        self.result_label.setObjectName("accent")
        self.result_label.setMinimumWidth(180)

        factor_row.addWidget(self.factor_combo)
        factor_row.addWidget(self.target_spin)
        factor_row.addWidget(self.result_label)
        factor_row.addStretch(1)
        grid.addLayout(factor_row, 1, 1)

        grid.addWidget(self._label("Encoder"), 2, 0)
        encoder_box = QVBoxLayout()
        encoder_box.setSpacing(2)
        self.encoder_combo = QComboBox()
        for label, value in ENCODERS:
            self.encoder_combo.addItem(label, value)
        self.encoder_combo.setMinimumHeight(34)
        self.encoder_combo.setToolTip(
            "AMF encoders use the AMD hardware encoder. HEVC or AV1 is required for HDR."
        )
        self.encoder_combo.currentIndexChanged.connect(self._encoder_changed)
        self.encoder_hint = QLabel("")
        self.encoder_hint.setObjectName("hint")
        encoder_box.addWidget(self.encoder_combo)
        encoder_box.addWidget(self.encoder_hint)
        grid.addLayout(encoder_box, 2, 1)

        grid.addWidget(self._label("Quality"), 3, 0)
        quality_row = QHBoxLayout()
        quality_row.setSpacing(12)
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(10, 28)
        self.quality_slider.setValue(16)
        self.quality_slider.setToolTip(
            "Lower CRF means better quality and a larger file. "
            "16 is high quality; 14 or lower also uses a slower preset."
        )
        self.quality_value = QLabel("16 · high")
        self.quality_value.setObjectName("muted")
        self.quality_value.setFixedWidth(120)
        self.quality_slider.valueChanged.connect(self._quality_changed)
        quality_row.addWidget(self.quality_slider, 1)
        quality_row.addWidget(self.quality_value)
        grid.addLayout(quality_row, 3, 1)

        self.hdr_label = self._label("HDR output")
        self.hdr_combo = QComboBox()
        self.hdr_combo.addItem("Preserve HDR10 / HLG (10-bit)", "preserve")
        self.hdr_combo.addItem("Tone-map to SDR (10-bit)", "tonemap")
        self.hdr_combo.setMinimumHeight(34)
        self.hdr_combo.setToolTip(
            "Preserve keeps the source transfer function and HDR color tags. "
            "Tone-map converts to SDR BT.709 while keeping 10-bit precision."
        )
        self.hdr_combo.currentIndexChanged.connect(self._hdr_changed)
        self.hdr_label.setVisible(False)
        self.hdr_combo.setVisible(False)
        grid.addWidget(self.hdr_label, 4, 0)
        grid.addWidget(self.hdr_combo, 4, 1)

        return card

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.start_button = QPushButton("Start interpolation")
        self.start_button.setObjectName("primary")
        self.start_button.setEnabled(False)
        self.start_button.setMinimumWidth(170)
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setToolTip("Start interpolating the selected video (Ctrl+Enter).")
        self.start_button.clicked.connect(self.start)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("danger")
        self.cancel_button.setVisible(False)
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.setToolTip("Stop the job and remove the partial file (Esc).")
        self.cancel_button.clicked.connect(self.cancel)

        row.addWidget(self.start_button)
        row.addWidget(self.cancel_button)
        row.addStretch(1)
        return row

    def _build_status_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(0)

        self.status_label = QLabel("Ready — drop a video to begin.")
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        return card

    # -------------------------------------------------------------- settings

    def _load_settings(self) -> None:
        try:
            saved_panel = int(self.settings.value("compare_width", 0))
        except (TypeError, ValueError):
            saved_panel = 0
        self._initial_panel_width = (
            max(560, min(1100, saved_panel)) if saved_panel else 960
        )

        geometry = self.settings.value("geometry_v2")
        restored = bool(geometry) and self.restoreGeometry(geometry)
        if not restored:
            self._apply_default_geometry()
        elif (
            self.width() < self.minimumWidth()
            or self.height() < self.minimumHeight()
        ):
            self.resize(
                max(self.width(), self.minimumWidth()),
                max(self.height(), self.minimumHeight()),
            )

        workspace_state = self.settings.value("workspace_v2")
        if workspace_state:
            self.workspace.restoreState(workspace_state)
        else:
            QTimer.singleShot(0, self._apply_default_split)

        self.preferred_profile = str(self.settings.value("profile", "live"))
        profile_index = self.profile_combo.findData(self.preferred_profile)
        if profile_index >= 0:
            self.profile_combo.setCurrentIndex(profile_index)

        self.preferred_hdr_mode = str(self.settings.value("hdr_mode", "preserve"))
        hdr_index = self.hdr_combo.findData(self.preferred_hdr_mode)
        if hdr_index >= 0:
            self.hdr_combo.setCurrentIndex(hdr_index)

        encoder_name = self.settings.value("encoder_name", None)
        if encoder_name:
            encoder_index = self.encoder_combo.findData(str(encoder_name))
        else:
            # Migrate the old index-based setting. The previous list was
            # x264, hevc_amf, av1_amf, h264_amf.
            old_names = ["libx264", "hevc_amf", "av1_amf", "h264_amf"]
            try:
                old_index = int(self.settings.value("encoder", 0))
            except (TypeError, ValueError):
                old_index = 0
            encoder_index = self.encoder_combo.findData(
                old_names[old_index] if 0 <= old_index < len(old_names) else "libx264"
            )
        if encoder_index >= 0:
            self.encoder_combo.setCurrentIndex(encoder_index)

        quality = int(self.settings.value("quality", 16))
        self.quality_slider.setValue(max(10, min(28, quality)))
        self._quality_changed(self.quality_slider.value())

        self.preferred_rate_mode = str(self.settings.value("rate_mode", "mult"))
        try:
            self.preferred_multiplier = int(self.settings.value("rate_multiplier", 2))
        except (TypeError, ValueError):
            self.preferred_multiplier = 2
        try:
            target = float(self.settings.value("target_fps", MAX_OUTPUT_FPS))
        except (TypeError, ValueError):
            target = MAX_OUTPUT_FPS
        # Setting the spin box emits valueChanged, which would otherwise flip
        # the saved "target" mode back to multiplier mode before it is restored.
        self.target_spin.blockSignals(True)
        self.target_spin.setValue(max(1.0, min(MAX_OUTPUT_FPS, target)))
        self.target_spin.blockSignals(False)

        if self.preferred_rate_mode == "target":
            index = self._find_rate_index(None)
        else:
            index = self._find_rate_index(self.preferred_multiplier)
        if index >= 0:
            self.factor_combo.setCurrentIndex(index)
        self._factor_changed()
        self._profile_changed()
        self._refresh_output_capability()

    def _apply_default_geometry(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1830, 860)
            return
        available = screen.availableGeometry()
        width = min(1830, max(self.minimumWidth(), available.width() - 80))
        height = min(860, max(self.minimumHeight(), available.height() - 100))
        self.resize(width, height)
        x = available.x() + max(0, (available.width() - width) // 2)
        y = available.y() + max(0, (available.height() - height) // 2)
        self.move(x, y)

    def _apply_default_split(self) -> None:
        total = max(1, self.workspace.width() - self.workspace.handleWidth())
        left_min = self.left_panel.minimumWidth()
        panel_min = self.preview_panel.minimumWidth()
        max_panel = max(panel_min, total - left_min)
        panel = max(panel_min, min(self._initial_panel_width, max_panel))
        left = max(left_min, total - panel)
        self.workspace.setSizes([left, panel])

    def _save_settings(self) -> None:
        self.settings.setValue("geometry_v2", self.saveGeometry())
        self.settings.setValue("workspace_v2", self.workspace.saveState())
        if self.preview_panel is not None and self.preview_panel.width() > 0:
            self.settings.setValue("compare_width", self.preview_panel.width())
        self.settings.setValue("encoder", self.encoder_combo.currentIndex())
        self.settings.setValue("encoder_name", self.encoder_combo.currentData())
        self.settings.setValue("quality", self.quality_slider.value())
        self.settings.setValue("profile", self.profile_combo.currentData())
        self.settings.setValue("hdr_mode", self.hdr_combo.currentData())
        self.settings.setValue("rate_mode", self.preferred_rate_mode)
        self.settings.setValue("rate_multiplier", self.preferred_multiplier)
        self.settings.setValue("target_fps", self.target_spin.value())

    def _profile_changed(self) -> None:
        profile = self.profile_combo.currentData() or "live"
        self.preferred_profile = profile
        if profile == "anime":
            self.profile_hint.setText(
                "RIFE 4.9 · sharper animation lines · lower scene-cut threshold"
            )
        else:
            self.profile_hint.setText(
                "General RIFE · camera footage, mixed content and live action"
            )

    def _encoder_changed(self) -> None:
        self._refresh_output_capability()

    def _hdr_changed(self) -> None:
        self.preferred_hdr_mode = self.hdr_combo.currentData() or "preserve"
        self._refresh_output_capability()
        if self.info is not None:
            self._refresh_source_capabilities()

    def _select_encoder(self, name: str) -> None:
        index = self.encoder_combo.findData(name)
        if index < 0:
            return
        self.encoder_combo.blockSignals(True)
        self.encoder_combo.setCurrentIndex(index)
        self.encoder_combo.blockSignals(False)

    def _output_bit_depth(self) -> tuple[int, bool]:
        if self.info is None:
            return 8, False
        bit_depth = 10 if (self.info.is_hdr or self.info.bit_depth > 8) else 8
        hdr_output = bool(
            self.info.is_hdr
            and bit_depth > 8
            and self.hdr_combo.currentData() != "tonemap"
        )
        return bit_depth, hdr_output

    def _refresh_output_capability(self) -> None:
        bit_depth, hdr_output = self._output_bit_depth()
        encoder = self.encoder_combo.currentData()

        if hdr_output and encoder not in HDR_ENCODERS:
            self._select_encoder("hevc_amf")
            encoder = self.encoder_combo.currentData()
            if encoder not in HDR_ENCODERS:
                self._select_encoder("libx265")
                encoder = self.encoder_combo.currentData()
        elif bit_depth > 8 and encoder not in TEN_BIT_ENCODERS:
            self._select_encoder("hevc_amf")
            encoder = self.encoder_combo.currentData()
            if encoder not in TEN_BIT_ENCODERS:
                self._select_encoder("libx265")
                encoder = self.encoder_combo.currentData()

        if self.info is None:
            self.output_badge.setText("AUTO · 8-BIT")
            self.encoder_hint.setText(
                "10-bit / HDR sources switch to a 10-bit-capable encoder automatically."
            )
            return

        if hdr_output:
            self.output_badge.setText("10-BIT · HDR")
            self.encoder_hint.setText(
                "HEVC or AV1 is required to keep HDR color tags."
            )
        elif bit_depth > 8:
            self.output_badge.setText("10-BIT · SDR")
            self.encoder_hint.setText(
                "The source has 10-bit precision, so the output keeps it."
            )
        else:
            self.output_badge.setText("8-BIT · SDR")
            self.encoder_hint.setText(
                "8-bit source · H.264 is the most compatible choice."
            )

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _set_source_chips(self, chips: list[tuple[str, str]]) -> None:
        self._clear_layout(self.source_chip_layout)
        for text, kind in chips:
            chip = QLabel(text)
            chip.setObjectName("chip")
            chip.setProperty("chipKind", kind)
            self.source_chip_layout.addWidget(chip)
        self.source_chip_layout.addStretch(1)

    def _refresh_source_capabilities(self) -> None:
        if self.info is None:
            return
        info = self.info
        self.hdr_label.setVisible(info.is_hdr)
        self.hdr_combo.setVisible(info.is_hdr)

        # H.264 10-bit exists, but HEVC is the compatible default for 10-bit
        # and the only sane choice for HDR. Do this once when a source loads;
        # the user can still choose another encoder afterwards.
        if (info.bit_depth > 8 or info.is_hdr) and self.encoder_combo.currentData() in {
            "libx264",
            "h264_amf",
        }:
            self._select_encoder("hevc_amf")
            if self.encoder_combo.currentData() not in TEN_BIT_ENCODERS:
                self._select_encoder("libx265")

        chips: list[tuple[str, str]] = []
        if info.bit_depth > 8:
            chips.append((f"{info.bit_depth}-BIT", "depth"))
        if info.is_hdr:
            chips.append((info.hdr_kind or "HDR", "hdr"))
        if info.is_vfr:
            chips.append(("VFR → CFR", "warn"))
        if info.display_width >= 3840:
            chips.append(("4K", "depth"))
        self._set_source_chips(chips)

        warnings: list[str] = []
        if info.is_hdr:
            if self.hdr_combo.currentData() == "tonemap":
                warnings.append("HDR source — tone-mapped to SDR BT.709 at 10-bit.")
            else:
                warnings.append(
                    "HDR source — color tags preserved in a 10-bit output; "
                    "HDR10 mastering-display metadata is not copied."
                )
        if info.is_vfr:
            warnings.append(
                f"Variable frame rate — normalized to {float(info.fps):g} fps CFR."
            )
        self.source_warning_label.setText("   ·   ".join(warnings))
        self.source_warning_label.setVisible(bool(warnings))
        self._refresh_output_capability()

    def _toggle_details(self, visible: bool) -> None:
        self.log.setVisible(visible)
        self.details_button.setText("Hide details" if visible else "Details")

    def _quality_changed(self, value: int) -> None:
        if value <= 12:
            label = "near-lossless"
        elif value <= 16:
            label = "high"
        elif value <= 20:
            label = "balanced"
        else:
            label = "small file"
        self.quality_value.setText(f"{value} · {label}")

    def _find_rate_index(self, data) -> int:
        for index in range(self.factor_combo.count()):
            if self.factor_combo.itemData(index) == data:
                return index
        return -1

    def _factor_changed(self) -> None:
        is_target = self.factor_combo.count() > 0 and self.factor_combo.currentData() is None
        self.target_spin.setVisible(is_target)
        if self.factor_combo.count() > 0:
            if is_target:
                self.preferred_rate_mode = "target"
            else:
                self.preferred_rate_mode = "mult"
                self.preferred_multiplier = int(self.factor_combo.currentData())
        self._refresh_result()

    # ---------------------------------------------------------------- input

    def browse_input(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        start_dir = str(self.input_path.parent) if self.input_path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a video",
            start_dir,
            "Video files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.mts *.m2ts *.wmv *.flv *.mpg *.mpeg);;All files (*.*)",
        )
        # Native Windows dialogs can leave focus with the previous window.
        # Re-activate GFI before doing the (synchronous) probe.
        self._bring_to_front()
        if path:
            self.load_file(path)
        else:
            QTimer.singleShot(0, self._bring_to_front)
            QTimer.singleShot(120, self._bring_to_front)

    def _on_file_dropped(self, path: str) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self._bring_to_front()
        self.load_file(path)
        QTimer.singleShot(0, self._bring_to_front)
        QTimer.singleShot(120, self._bring_to_front)

    def _bring_to_front(self) -> None:
        """Restore focus after a native dialog or an Explorer drop."""
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        handle = self.windowHandle()
        if handle is not None:
            handle.requestActivate()
        if os.name == "nt":
            try:
                import ctypes

                ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
            except Exception:  # noqa: BLE001 - focus is best-effort
                pass

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
            return
        if _first_video_from_mime(event.mimeData()) is not None:
            event.acceptProposedAction()
            self.drop_zone._set_drag(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.drop_zone._set_drag(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self.drop_zone._set_drag(False)
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
            return
        path = _first_video_from_mime(event.mimeData())
        if path is not None:
            event.acceptProposedAction()
            self._on_file_dropped(str(path))
        else:
            event.ignore()

    def load_file(self, path: str) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        if self._save_worker is not None and self._save_worker.isRunning():
            QMessageBox.information(
                self,
                "Saving video",
                "Wait for the current Save video operation to finish.",
            )
            return
        candidate = Path(path).expanduser().resolve()
        if not candidate.is_file():
            QMessageBox.warning(self, "File not found", f"Could not find:\n{candidate}")
            return
        if not _is_video(candidate):
            QMessageBox.warning(self, "Unsupported file", "That does not look like a video file.")
            return
        try:
            info = probe(find_tool("ffprobe"), candidate)
        except Exception as exc:  # noqa: BLE001 - surfaced in the window
            QMessageBox.critical(self, "Could not read video", str(exc))
            return
        if not self._release_current_draft("loading another video"):
            return

        self._clear_current_draft()
        self.input_path = candidate
        self.info = info

        self.file_name_label.setText(candidate.name)
        audio = f"{info.audio_streams} audio track" if info.audio_streams else "no audio"
        if info.audio_streams > 1:
            audio += "s"
        codec = info.codec.upper() if info.codec else ""
        if info.profile and info.profile.lower() != "unknown":
            codec = f"{codec} {info.profile}".strip()
        bitrate = _fmt_bitrate(info.bit_rate)
        source_size = _fmt_size(info.path.stat().st_size) if info.path.is_file() else ""
        technical = "  ·  ".join(
            part for part in (codec, info.pix_fmt, bitrate, source_size) if part
        )
        self.file_details_label.setText(
            f"{info.display_width} × {info.display_height}   ·   "
            f"{float(info.fps):g} fps   ·   {_fmt_duration(info.duration)}   ·   "
            f"{info.frame_count:,} frames   ·   {audio}"
            + (f"\n{technical}" if technical else "")
        )
        self.info_card.setVisible(True)
        self.change_button.setVisible(True)
        # The source card now acts as the compact "drop / replace" area;
        # reclaiming this height keeps the HDR controls from being squeezed.
        self.drop_zone.setVisible(False)
        self._refresh_source_capabilities()

        source_fps = float(info.fps)
        self._rebuild_rate_choices()
        has_rate = self.factor_combo.count() > 0
        self._refresh_start_button()
        if has_rate:
            self._set_status(f"Ready — {candidate.name}")
        else:
            where = "above" if source_fps > MAX_OUTPUT_FPS + 0.01 else "at"
            self._set_status(
                f"Source is already {where} the {MAX_OUTPUT_FPS:g} fps cap."
            )
        self.log.appendPlainText(f"Loaded {candidate}")
        if info.bit_depth > 8 or info.is_hdr:
            self.log.appendPlainText(
                f"Source precision: {info.bit_depth}-bit {info.pix_fmt}; "
                f"output will use {self._output_bit_depth()[0]}-bit."
            )
        self._refresh_result()
        self.preview_panel.show_source(candidate, info)
        self._bring_to_front()
        QTimer.singleShot(0, self._bring_to_front)
        QTimer.singleShot(120, self._bring_to_front)

        should_autostart = self.autostart
        self.autostart = False
        if should_autostart and has_rate:
            QTimer.singleShot(150, self.start)

    def _rebuild_rate_choices(self) -> None:
        if self.info is None:
            return
        source_fps = float(self.info.fps)
        had_choices = self.factor_combo.count() > 0
        was_target = had_choices and self.factor_combo.currentData() is None
        old_multiplier = self.factor_combo.currentData() if had_choices else None
        if not had_choices:
            was_target = self.preferred_rate_mode == "target"
            old_multiplier = self.preferred_multiplier

        min_target = math.floor(source_fps + 1e-6) + 1
        has_target = min_target <= int(MAX_OUTPUT_FPS)
        current_target = self.target_spin.value()

        self.factor_combo.blockSignals(True)
        self.target_spin.blockSignals(True)
        self.factor_combo.clear()
        for multiplier in (2, 3, 4):
            output_fps = source_fps * multiplier
            if output_fps <= MAX_OUTPUT_FPS + 1e-6:
                self.factor_combo.addItem(
                    f"{multiplier}×  ·  {output_fps:g} fps", multiplier
                )
        if has_target:
            self.factor_combo.addItem("Desired FPS…", None)
            self.target_spin.setMinimum(float(min_target))
            self.target_spin.setMaximum(MAX_OUTPUT_FPS)
            if current_target < min_target or current_target > MAX_OUTPUT_FPS:
                self.target_spin.setValue(float(MAX_OUTPUT_FPS))

        target_index = self._find_rate_index(None)
        if was_target and target_index >= 0:
            self.factor_combo.setCurrentIndex(target_index)
        else:
            multiplier_index = self._find_rate_index(old_multiplier)
            if multiplier_index >= 0:
                self.factor_combo.setCurrentIndex(multiplier_index)
            elif self.factor_combo.count() > 0:
                self.factor_combo.setCurrentIndex(0)
        self.target_spin.blockSignals(False)
        self.factor_combo.blockSignals(False)
        self._factor_changed()

    def _compute(self) -> tuple[int, float, float] | None:
        if self.info is None or self.factor_combo.count() == 0:
            return None
        source_fps = float(self.info.fps)
        data = self.factor_combo.currentData()
        if data is None:
            target = float(self.target_spin.value())
            if target <= source_fps + 1e-6 or target > MAX_OUTPUT_FPS + 1e-6:
                return None
            multiplier = max(1, math.ceil(target / source_fps - 1e-9))
            generated = source_fps * multiplier
            return multiplier, generated, target
        multiplier = int(data)
        generated = source_fps * multiplier
        if generated > MAX_OUTPUT_FPS + 1e-6:
            return None
        return multiplier, generated, generated

    def _refresh_result(self) -> None:
        if self.info is None:
            self.result_label.setText("")
            return
        if self.factor_combo.count() == 0:
            where = "above" if float(self.info.fps) > MAX_OUTPUT_FPS + 0.01 else "at"
            self.result_label.setStyleSheet("color: #8b95a7;")
            self.result_label.setText(f"already {where} the {MAX_OUTPUT_FPS:g} fps cap")
            return
        computed = self._compute()
        if computed is None:
            if self.factor_combo.currentData() is None:
                self.result_label.setStyleSheet("color: #f87171;")
                self.result_label.setText("must be above the source")
            else:
                self.result_label.setStyleSheet("")
                self.result_label.setText("")
            return
        out_fps = computed[2]
        self.result_label.setStyleSheet("")
        self.result_label.setText(f"→ {out_fps:g} fps")

    # ----------------------------------------------------------------- drafts

    def _draft_is_saved(self) -> bool:
        return self._draft_saved_path is not None and self._draft_saved_path.is_file()

    def _clear_current_draft(self) -> None:
        remove_draft(self.last_output)
        self.last_output = None
        self.last_job_source = None
        self.last_job_info = None
        self.last_job_stats = None
        self.last_job_source_signature = None
        self.last_job_output_signature = None
        self._draft_saved_path = None
        self._draft_display_name = ""
        self.preview_panel.show_idle()

    def _release_current_draft(self, action: str) -> bool:
        """Ask before abandoning an unsaved draft; True means safe to proceed."""
        if self.exit_on_finish or self.last_output is None or self._draft_is_saved():
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved draft")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("Your interpolated draft has not been saved.")
        box.setInformativeText(
            f"Save a copy before {action}?\n\n"
            "Discarding removes the temporary draft."
        )
        save_button = box.addButton("Save video…", QMessageBox.ButtonRole.AcceptRole)
        discard_button = box.addButton(
            "Discard draft", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(save_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_button:
            return self._save_video_blocking()
        if clicked is discard_button:
            return True
        if clicked is cancel_button:
            return False
        return False

    def _get_save_destination(self) -> Path | None:
        if self.last_output is None or not self.last_output.is_file():
            QMessageBox.information(
                self,
                "Nothing to save",
                "Interpolate a video first; the result is kept as a temporary draft.",
            )
            return None
        if self.last_job_source is not None:
            name = self._draft_display_name or (
                f"{self.last_job_source.stem}_interpolated.mp4"
            )
            suggested = self.last_job_source.with_name(name)
        elif self._draft_display_name:
            suggested = self.last_output.with_name(self._draft_display_name)
        else:
            suggested = self.last_output.with_name("interpolated.mp4")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save interpolated video",
            str(suggested),
            "MP4 video (*.mp4);;All files (*.*)",
        )
        self._bring_to_front()
        QTimer.singleShot(0, self._bring_to_front)
        QTimer.singleShot(120, self._bring_to_front)
        if not path:
            return None
        destination = Path(path).expanduser()
        if not destination.suffix:
            destination = destination.with_suffix(".mp4")
        if destination.suffix.lower() != ".mp4":
            QMessageBox.warning(
                self,
                "MP4 output",
                "The draft is an MP4 file. Choose a name ending in .mp4.",
            )
            return None
        destination = destination.resolve()
        if is_draft(destination) or destination in {
            self.last_output.resolve(),
            self.input_path.resolve() if self.input_path else None,
        }:
            QMessageBox.warning(
                self,
                "Choose another name",
                "Choose a destination outside the temporary draft cache and "
                "different from the source video.",
            )
            return None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Could not create folder", str(exc))
            return None
        return destination

    def _mark_saved(self, destination: Path) -> None:
        self._draft_saved_path = destination
        self.preview_panel.finish_save(destination)
        self._set_status(f"Saved — {destination.name} · draft kept for tweaks")
        self.log.appendPlainText(f"Saved a copy to {destination}")
        self._save_settings()

    def _save_video_blocking(self) -> bool:
        destination = self._get_save_destination()
        if destination is None or self.last_output is None:
            return False
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            copy_draft(self.last_output, destination)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Could not save video", str(exc))
            return False
        else:
            QApplication.restoreOverrideCursor()
        self._mark_saved(destination)
        return True

    def save_video(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        if self._save_worker is not None and self._save_worker.isRunning():
            return
        destination = self._get_save_destination()
        if destination is None or self.last_output is None:
            return
        self._save_destination = destination
        worker = SaveDraftWorker(self.last_output, destination, self)
        self._save_worker = worker
        worker.progress.connect(self.preview_panel.set_save_progress)
        worker.saved.connect(self._on_save_succeeded)
        worker.failed.connect(self._on_save_failed)
        worker.finished.connect(self._on_save_worker_finished)
        self.preview_panel.set_save_progress(-1)
        self._set_status(f"Saving {destination.name}…")
        self._refresh_start_button()
        worker.start()

    def _on_save_succeeded(self, path: str) -> None:
        self._mark_saved(Path(path))

    def _on_save_failed(self, message: str) -> None:
        self.preview_panel.cancel_save()
        self._set_status("Save failed — draft is still available.")
        self.log.appendPlainText(f"Save failed: {message}")
        QMessageBox.critical(
            self,
            "Could not save video",
            f"The temporary draft is still available.\n\n{message}",
        )

    def _on_save_worker_finished(self) -> None:
        worker = self._save_worker
        self._save_worker = None
        self._save_destination = None
        if worker is not None:
            worker.deleteLater()
        self._refresh_start_button()

    # --------------------------------------------------------------- actions

    def start(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        if self._save_worker is not None and self._save_worker.isRunning():
            QMessageBox.information(
                self,
                "Saving video",
                "Wait for the current Save video operation to finish.",
            )
            return
        if self.input_path is None or self.info is None:
            return
        if self.factor_combo.count() == 0:
            QMessageBox.information(
                self,
                "Already at the cap",
                f"GFI tops out at {MAX_OUTPUT_FPS:g} fps, and this source is already there.",
            )
            return
        computed = self._compute()
        if computed is None:
            QMessageBox.warning(
                self,
                "Cannot interpolate",
                f"Choose an output rate above the source ({float(self.info.fps):g} fps) "
                f"and no higher than {MAX_OUTPUT_FPS:g} fps.",
            )
            return
        multiplier, generated_fps, out_fps = computed
        is_target = self.factor_combo.currentData() is None
        target_fps = out_fps if is_target else 0.0
        encode_fps = out_fps if is_target else generated_fps
        tag = f"{out_fps:g}fps" if is_target else f"{multiplier}x"
        display_name = f"{self.input_path.stem}_{tag}.mp4"

        profile = self.profile_combo.currentData() or "live"
        model_path = PROFILE_MODELS[profile]
        self._refresh_output_capability()
        bit_depth, hdr_output = self._output_bit_depth()
        encoder = self.encoder_combo.currentData()
        hdr_mode = (
            self.hdr_combo.currentData() if self.info.is_hdr else "preserve"
        ) or "preserve"

        if not model_path.is_file():
            hint = (
                "The RIFE model is missing.\n\n"
                "Official portable build: download GFI again from the official "
                "release page.\n"
                "Source checkout: run python scripts\\fetch_model.py"
            )
            if profile == "anime":
                hint = (
                    "The Anime / cartoon model is missing.\n\n"
                    "Official portable build: download GFI again from the official "
                    "release page.\n"
                    "Source checkout: run python scripts\\fetch_model.py --profile anime"
                )
            QMessageBox.critical(self, "Model missing", hint)
            return
        if hdr_output and encoder not in HDR_ENCODERS:
            QMessageBox.critical(
                self,
                "HDR encoder required",
                "HDR output needs HEVC or AV1. Choose libx265, hevc_amf or av1_amf.",
            )
            return
        if bit_depth > 8 and encoder not in TEN_BIT_ENCODERS:
            QMessageBox.critical(
                self,
                "10-bit encoder required",
                "This source needs a 10-bit-capable encoder. Choose libx264, "
                "libx265, hevc_amf or av1_amf.",
            )
            return

        try:
            output = allocate_draft(self.input_path)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Could not create the draft",
                "GFI could not create its temporary draft folder.\n\n"
                f"{exc}",
            )
            return

        job = Job(
            input=self.input_path,
            output=output,
            multiplier=multiplier,
            generated_fps=encode_fps,
            out_fps=out_fps,
            target_fps=target_fps,
            encoder=encoder,
            crf=self.quality_slider.value(),
            preset="medium" if self.quality_slider.value() <= 14 else "veryfast",
            scene_threshold=0.15 if profile == "anime" else 0.25,
            bit_depth=bit_depth,
            hdr_mode=hdr_mode,
            audio=True,
            overwrite=True,
            quiet=True,
        )

        self._pending_output = output
        self._pending_source = self.input_path
        self._pending_info = self.info
        self._pending_source_signature = _path_signature(self.input_path)
        self._pending_display_name = display_name
        self.cancel_event = threading.Event()
        self.worker = Worker(job, self.info, model_path, self.cancel_event)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self._on_log)
        self.worker.completed.connect(self._on_completed)
        self.worker.failed.connect(self._on_failed)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.finished.connect(self._on_worker_finished)

        self._set_running(True)
        self._set_status("Rendering draft…")
        if is_target:
            mode_text = f"exact {out_fps:g} fps"
        else:
            mode_text = f"{multiplier}× → {out_fps:g} fps"
        depth_text = f"{bit_depth}-bit"
        if self.info.is_hdr:
            depth_text += " HDR" if hdr_output else " SDR (tone-mapped)"
        self.preview_panel.begin_interpolation(
            self.input_path,
            self.info,
            display_name,
            f"{out_fps:g} fps   ·   {depth_text}",
        )
        self.log.appendPlainText(
            f"Starting {mode_text}  ·  {PROFILE_LABELS.get(profile, profile)}  ·  "
            f"{depth_text}  ·  {encoder} CRF {job.crf}  ·  temporary draft"
        )
        self._save_settings()
        self.worker.start()

    def cancel(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            return
        self.cancel_button.setEnabled(False)
        self._set_status("Cancelling…")
        self.cancel_event.set()

    def _refresh_start_button(self) -> None:
        self.start_button.setEnabled(
            self.worker is None
            and self._save_worker is None
            and self.input_path is not None
            and self.factor_combo.count() > 0
        )

    def _set_running(self, running: bool) -> None:
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(running)
        self.drop_zone.setEnabled(not running)
        self.change_button.setEnabled(not running)
        self.profile_combo.setEnabled(not running)
        self.factor_combo.setEnabled(not running)
        self.target_spin.setEnabled(not running)
        self.encoder_combo.setEnabled(not running)
        self.hdr_combo.setEnabled(not running)
        self.quality_slider.setEnabled(not running)
        self._refresh_start_button()

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    # --------------------------------------------------------------- signals

    def _on_progress(self, done: int, total: int, produced: int, elapsed: float) -> None:
        fraction = min(done / max(total, 1), 1.0)
        rate = produced / max(elapsed, 1e-6)
        eta = (total - done) / (done / max(elapsed, 1e-6)) if done else float("inf")
        detail = (
            f"Frame {done:,} of {total:,}   ·   {rate:.1f} fps output   ·   "
            f"ETA {_fmt_eta(eta)}"
        )
        self.preview_panel.update_interpolation(int(fraction * 100), detail)

    def _on_log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def _on_completed(self, stats: dict) -> None:
        self._set_running(False)
        pending = self._pending_output
        if pending is None or not pending.is_file():
            remove_draft(pending)
            self._clear_pending_render()
            self._set_status("Failed — the draft file is missing.")
            self.log.appendPlainText("The rendered draft disappeared before it could be used.")
            if not self._restore_previous_preview(
                "Failed — the new draft is missing; previous draft kept."
            ):
                self.preview_panel.show_error(
                    "Interpolation failed",
                    "The rendered draft file is missing. No output was kept.",
                )
            if not self.exit_on_finish:
                QMessageBox.critical(
                    self,
                    "Interpolation failed",
                    "The rendered draft file is missing. No output was kept.",
                )
            return

        previous = self.last_output
        self.last_output = pending
        self.last_job_source = self._pending_source or self.input_path
        self.last_job_info = self._pending_info or self.info
        self.last_job_stats = stats
        self.last_job_source_signature = self._pending_source_signature
        self.last_job_output_signature = _path_signature(pending)
        self._draft_display_name = self._pending_display_name or "interpolated.mp4"
        self._draft_saved_path = None
        if previous is not None and previous != pending:
            remove_draft(previous)
        self._clear_pending_render()

        self._set_status(
            f"Done — {stats['output_frames']:,} frames in {stats['seconds']:.1f}s   ·   "
            f"{_fmt_size(stats['bytes'])}   ·   draft ready"
        )
        self.log.appendPlainText("Temporary draft ready in managed storage.")
        self.log.appendPlainText("Use Save video… to keep a copy before closing.")
        if self.exit_on_finish:
            QTimer.singleShot(400, QApplication.instance().quit)
            return
        if (
            self.last_job_source is None
            or self.last_job_info is None
            or self.last_job_source_signature is None
            or self.last_job_output_signature is None
        ):
            self.preview_panel.show_error(
                "Interpolation complete",
                "The video is ready, but the comparison preview could not be configured.",
                draft_available=True,
            )
            return

        self._play_success_sound()
        self.preview_panel.load_comparison(
            self.last_job_source,
            self.last_output,
            self.last_job_info,
            stats,
            display_name=self._draft_display_name,
            saved_path=None,
            autoplay=False,
        )

    def _clear_pending_render(self) -> None:
        self._pending_output = None
        self._pending_source = None
        self._pending_info = None
        self._pending_source_signature = None
        self._pending_display_name = ""

    def _compare_available(self) -> bool:
        if (
            self.last_output is None
            or not self.last_output.is_file()
            or self.last_job_source is None
            or not self.last_job_source.is_file()
            or self.last_job_info is None
            or self.last_job_stats is None
            or self.last_job_source_signature is None
            or self.last_job_output_signature is None
        ):
            return False
        return (
            _path_signature(self.last_job_source) == self.last_job_source_signature
            and _path_signature(self.last_output) == self.last_job_output_signature
        )

    def _restore_previous_preview(self, status: str) -> bool:
        if not self._compare_available():
            return False
        assert self.last_job_source is not None
        assert self.last_job_info is not None
        assert self.last_job_stats is not None
        self.preview_panel.load_comparison(
            self.last_job_source,
            self.last_output,
            self.last_job_info,
            self.last_job_stats,
            display_name=self._draft_display_name,
            saved_path=self._draft_saved_path,
            autoplay=False,
        )
        self._set_status(status)
        return True

    def _on_failed(self, message: str) -> None:
        self._set_running(False)
        remove_draft(self._pending_output)
        self._clear_pending_render()
        self._set_status("Failed — see details.")
        self.log.appendPlainText(message)
        self.details_button.setChecked(True)
        restored = self._restore_previous_preview(
            "Failed — showing the previous draft; new render was not kept."
        )
        if not restored and self.input_path is not None and self.info is not None:
            self.preview_panel.show_source(
                self.input_path,
                self.info,
                title="Interpolation failed",
                detail=(
                    "No draft was kept. Check Details, adjust the settings, and "
                    "try again."
                ),
                error=True,
            )
        elif not restored:
            self.preview_panel.show_error(
                "Interpolation failed",
                "No draft was kept. Check Details, adjust the settings and try again.",
            )
        if not self.exit_on_finish:
            QMessageBox.critical(self, "Interpolation failed", message)
        else:
            QTimer.singleShot(400, QApplication.instance().quit)

    def _on_cancelled(self) -> None:
        self._set_running(False)
        remove_draft(self._pending_output)
        self._clear_pending_render()
        self.log.appendPlainText("Cancelled by user.")
        restored = self._restore_previous_preview(
            "Cancelled — previous draft kept."
        )
        if not restored:
            self._set_status("Cancelled — no draft was kept.")
            if self.input_path is not None and self.info is not None:
                self.preview_panel.show_source(
                    self.input_path,
                    self.info,
                    title="Interpolation cancelled",
                    detail="No draft was kept. Your source video is unchanged.",
                    error=False,
                )
            else:
                self.preview_panel.show_error(
                    "Interpolation cancelled",
                    "No draft was kept. Your source video is unchanged.",
                    error=False,
                )
        if self.exit_on_finish:
            QTimer.singleShot(400, QApplication.instance().quit)

    def _on_worker_finished(self) -> None:
        self.worker = None
        self._refresh_start_button()

    def _handle_escape(self) -> None:
        self.cancel()

    def _play_success_sound(self) -> None:
        if self.exit_on_finish:
            return
        if os.name == "nt":
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_ICONASTERISK)
                return
            except Exception:  # noqa: BLE001 - sound is best-effort
                pass
        QApplication.beep()

    def apply_dark_titlebar(self) -> None:
        if os.name != "nt":
            return
        try:
            import ctypes

            hwnd = int(self.winId())
            enabled = ctypes.c_int(1)
            for attribute in (20, 19):
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    attribute,
                    ctypes.byref(enabled),
                    ctypes.sizeof(enabled),
                )
                if result == 0:
                    break
        except Exception:  # noqa: BLE001 - cosmetic only
            pass

    # ----------------------------------------------------------------- close

    def closeEvent(self, event) -> None:
        if self._save_worker is not None and self._save_worker.isRunning():
            QMessageBox.information(
                self,
                "Still saving",
                "The video is still being copied. Wait for Save video to finish, "
                "then close GFI.",
            )
            event.ignore()
            return
        if self.worker is not None and self.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "Interpolation running",
                "A job is still running. Stop it and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.cancel_event.set()
            self.worker.wait(8000)
            if self.worker is not None and self.worker.isRunning():
                QMessageBox.warning(
                    self,
                    "Still stopping",
                    "GFI is still stopping the encoder. Try closing again in a moment.",
                )
                event.ignore()
                return
        if not self._release_current_draft("closing"):
            event.ignore()
            return
        self.preview_panel.stop_comparison()
        remove_draft(self._pending_output)
        remove_draft(self.last_output)
        self.preview_panel.show_idle()
        self._save_settings()
        self.settings.sync()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gfi",
        description="GFI (Generate Frame Inbetween). Smoother video, locally.",
    )
    parser.add_argument("input", nargs="?", type=Path, help="video to load")
    parser.add_argument("--autostart", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--exit-on-finish", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    args, qt_args = parser.parse_known_args(argv)

    app = QApplication([sys.argv[0], *qt_args])
    app.setStyle("Fusion")
    app.setApplicationName("GFI")
    app.setApplicationDisplayName("GFI")
    app.setOrganizationName("GFI")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(_app_icon())
    app.setFont(QFont("Segoe UI Variable", 10))
    app.setStyleSheet(STYLESHEET)
    try:
        ensure_data_root()
    except OSError as exc:
        QMessageBox.critical(None, "GFI cannot start", str(exc))
        return 1
    cleanup_drafts()

    window = MainWindow(autostart=args.autostart, exit_on_finish=args.exit_on_finish)
    window.show()
    window.apply_dark_titlebar()

    if args.input is not None:
        QTimer.singleShot(0, lambda: window.load_file(str(args.input)))

    if args.screenshot is not None:
        shot = args.screenshot.expanduser().resolve()
        shot.parent.mkdir(parents=True, exist_ok=True)

        def capture() -> None:
            window.grab().save(str(shot))

        QTimer.singleShot(1200, capture)
        if not args.autostart:
            QTimer.singleShot(1400, app.quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
