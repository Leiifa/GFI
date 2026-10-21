from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from collections import deque
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QKeySequence,
    QPainter,
    QPen,
    QShortcut,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .ffmpeg import CREATE_NO_WINDOW, build_compare_preview_cmd, find_tool
from .paths import ensure_data_root
from .probe import MediaInfo, probe

PREVIEW_VERSION = 4
PANE_WIDTH = 960
PANE_HEIGHT = 540
PREVIEW_FPS = 60.0
MAX_CACHE_FILES = 8
MAX_CACHE_BYTES = 3 * 1024**3

COMPARE_STYLESHEET = """
QFrame#compareSidebar {
    background: #0f141b;
    border: 1px solid #232a36;
    border-radius: 12px;
}
QFrame#videoStage {
    background: #05070a;
    border: 1px solid #1f2632;
    border-radius: 9px;
}
QWidget#videoPage, QWidget#statusPage { background: transparent; }
QVideoWidget#videoSurface { background: #000000; }
QLabel#compareTitle { font-size: 13pt; font-weight: 700; color: #f4f7fb; }
QLabel#compareSubtitle { color: #8b95a7; font-size: 9pt; }
QLabel#draftBadge {
    border-radius: 5px;
    font-size: 8pt;
    font-weight: 700;
    padding: 2px 7px;
}
QLabel#draftBadge[state="draft"] {
    background: #2a2314;
    border: 1px solid #59461e;
    color: #fbbf24;
}
QLabel#draftBadge[state="saved"] {
    background: #14211f;
    border: 1px solid #2f4a47;
    color: #4fd1c5;
}
QLabel#paneName {
    color: #8b95a7;
    font-size: 8.5pt;
    font-weight: 700;
}
QLabel#paneNameAccent {
    color: #4fd1c5;
    font-size: 8.5pt;
    font-weight: 700;
}
QLabel#paneMeta { color: #aab4c5; font-size: 9pt; }
QLabel#previewTitle { color: #f4f7fb; font-size: 14pt; font-weight: 600; }
QLabel#previewDetail {
    color: #8b95a7;
    font-size: 9.5pt;
}
QLabel#previewDetail[error="true"] { color: #f0a49a; }
QLabel#timeLabel {
    color: #aab4c5;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 9pt;
    min-width: 104px;
}
QLabel#compareHint { color: #7d8798; font-size: 8.5pt; }
QFrame#compareDivider { background: #2a3341; border: none; }
QPushButton#transport {
    background: #141a23;
    border: 1px solid #2a3341;
    border-radius: 7px;
    padding: 6px 12px;
    font-weight: 500;
}
QPushButton#transport:hover { background: #1b2330; border-color: #3a4658; }
QPushButton#transport:checked {
    background: #14211f;
    border-color: #2f4a47;
    color: #4fd1c5;
}
QPushButton#transport:disabled {
    color: #5f6878;
    border-color: #1f2632;
    background: #0c1015;
}
QPushButton#iconButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    color: #8b95a7;
    font-size: 16pt;
    font-weight: 400;
    padding: 0;
}
QPushButton#iconButton:hover { background: #1b2330; color: #f4f7fb; }
QLabel#compareWarning { color: #f0a49a; font-size: 8.5pt; }
QProgressBar { background: #1f2a38; border: none; border-radius: 4px; }
QProgressBar::chunk { background: #4fd1c5; border-radius: 4px; }
"""


def _fmt_clock(milliseconds: int) -> str:
    total = max(0, int(milliseconds)) // 1000
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _cache_root() -> Path:
    compare_root = ensure_data_root() / "compare"
    compare_root.mkdir(parents=True, exist_ok=True)
    return compare_root


def _file_signature(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        stat = resolved.stat()
    except OSError:
        return f"{resolved}|missing"
    return f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}"


def preview_cache_path(source: Path, output: Path) -> Path:
    """Return the cache path for this exact source/output pair and preset."""
    key = "|".join(
        (
            str(PREVIEW_VERSION),
            _file_signature(source),
            _file_signature(output),
            str(PANE_WIDTH),
            str(PANE_HEIGHT),
            f"{PREVIEW_FPS:g}",
        )
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return _cache_root() / f"compare_{digest}.mp4"


def _prune_cache(keep: Path | None = None) -> None:
    """Keep the cache bounded without touching the preview being opened."""
    try:
        files = [path for path in _cache_root().glob("compare_*.mp4") if path.is_file()]
    except OSError:
        return

    def modified(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    files.sort(key=modified, reverse=True)
    total = 0
    for index, path in enumerate(files):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total += size
        if index < MAX_CACHE_FILES and total <= MAX_CACHE_BYTES:
            continue
        if keep is not None and path == keep:
            continue
        try:
            path.unlink()
        except OSError:
            pass


class _PreviewCancelled(Exception):
    pass


class ElidedLabel(QLabel):
    """A one-line label that shrinks with the sidebar instead of forcing it wider."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = text or ""
        self._refresh_text()

    def _refresh_text(self) -> None:
        width = max(0, self.contentsRect().width())
        super().setText(
            self.fontMetrics().elidedText(
                self._full_text, Qt.TextElideMode.ElideMiddle, width
            )
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_text()


class PreviewBuilder(QThread):
    progress = Signal(int)
    ready = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        source_info: MediaInfo,
        output: Path,
        destination: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source_info = source_info
        self._output = output
        self._destination = destination
        self._stop = threading.Event()
        self._process: subprocess.Popen[str] | None = None

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def run(self) -> None:
        destination = self._destination
        temp = destination.with_name(
            f"{destination.stem}.{os.getpid()}.{threading.get_ident()}.part.mp4"
        )
        errors: deque[str] = deque(maxlen=30)
        process: subprocess.Popen[str] | None = None
        try:
            if self._stop.is_set():
                raise _PreviewCancelled()
            output_info = probe(find_tool("ffprobe"), self._output)
            if self._stop.is_set():
                raise _PreviewCancelled()

            destination.parent.mkdir(parents=True, exist_ok=True)
            command = build_compare_preview_cmd(
                find_tool("ffmpeg"),
                self._source_info,
                output_info,
                temp,
                pane_width=PANE_WIDTH,
                pane_height=PANE_HEIGHT,
                fps=PREVIEW_FPS,
            )
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            self._process = process
            if self._stop.is_set():
                process.terminate()

            duration = max(self._source_info.duration, output_info.duration, 0.0)
            last_percent = -1
            assert process.stdout is not None
            for raw in process.stdout:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("out_time_us="):
                    try:
                        seconds = int(line.split("=", 1)[1]) / 1_000_000
                    except (TypeError, ValueError):
                        continue
                    if duration > 0:
                        percent = max(0, min(100, int(seconds / duration * 100)))
                        if percent != last_percent:
                            last_percent = percent
                            self.progress.emit(percent)
                elif line.startswith(
                    (
                        "frame=",
                        "fps=",
                        "stream_",
                        "bitrate=",
                        "total_size=",
                        "out_time=",
                        "out_time_ms=",
                        "dup_frames=",
                        "drop_frames=",
                        "speed=",
                        "progress=",
                    )
                ):
                    continue
                else:
                    errors.append(line)

            return_code = process.wait()
            self._process = None
            if self._stop.is_set():
                raise _PreviewCancelled()
            if return_code != 0:
                detail = "\n".join(list(errors)[-8:])
                raise RuntimeError(
                    detail or f"FFmpeg preview exited with code {return_code}."
                )
            if not temp.is_file() or temp.stat().st_size <= 0:
                raise RuntimeError("FFmpeg did not write a preview file.")

            os.replace(temp, destination)
            _prune_cache(keep=destination)
            self.progress.emit(100)
            self.ready.emit(str(destination))
        except _PreviewCancelled:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - shown in the compare window
            self.failed.emit(str(exc) or exc.__class__.__name__)
        finally:
            self._process = None
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        process.kill()
                    except OSError:
                        pass
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


class LoadingSpinner(QWidget):
    """Small self-contained spinner; its timer only runs while preview is busy."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(46, 46)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._advance)
        self.hide()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _advance(self) -> None:
        self._angle = (self._angle + 11) % 360
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(5, 5, self.width() - 10, self.height() - 10)
        track = QPen(QColor("#1f2a38"), 4)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawArc(bounds, 0, 360 * 16)
        arc = QPen(QColor("#4fd1c5"), 4)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        painter.drawArc(bounds, -self._angle * 16, 105 * 16)
        painter.end()


class CompareSidebar(QFrame):
    """Permanent preview pane embedded beside the interpolation controls."""

    saveRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("compareSidebar")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Preview pane")
        self.setStyleSheet(COMPARE_STYLESHEET)

        self._source: Path | None = None
        self._output: Path | None = None
        self._source_info: MediaInfo | None = None
        self._stats: dict = {}
        self._active = False
        self._state = "idle"
        self._builder: PreviewBuilder | None = None
        self._preview_path: Path | None = None
        self._duration_ms = 0
        self._updating_timeline = False
        self._autoplay = False
        self._has_draft = False
        self._saved_path: Path | None = None
        self._display_name = ""
        self._save_active = False

        self._build_ui()
        self._setup_player()
        self._install_shortcuts()
        self.show_idle()

    # ------------------------------------------------------------------ API

    def show_idle(self) -> None:
        """Reset the pane to its permanent, no-video state."""
        self.stop_comparison()
        self._state = "idle"
        self._source = None
        self._output = None
        self._source_info = None
        self._stats = {}
        self._preview_path = None
        self._has_draft = False
        self._saved_path = None
        self._display_name = ""
        self._save_active = False

        self.file_label.setText("No video loaded")
        self.file_label.setToolTip("")
        self.pane_header_widget.hide()
        self.status_title.setText("Preview appears here")
        self._set_status_detail(
            "Load a video, interpolate it, and compare the original with the "
            "result without choosing an output file first.",
            error=False,
        )
        self._set_status_busy(False)
        self._set_video_controls_enabled(False)
        self.stack.setCurrentWidget(self.status_page)
        self._set_badge(False)
        self.save_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        self._set_hint_text(
            "Your preview stays on this PC.",
            "GFI renders a temporary draft and a lightweight 60 fps comparison proxy.",
        )
        self._apply_mute(self.mute_button.isChecked())

    def show_source(
        self,
        source: Path,
        source_info: MediaInfo,
        *,
        title: str = "Ready to preview",
        detail: str = (
            "Start interpolation to create a temporary draft and load the "
            "side-by-side comparison here."
        ),
        error: bool = False,
    ) -> None:
        """Show the loaded source before the first draft exists."""
        self.stop_comparison()
        self._state = "idle"
        self._source = source
        self._output = None
        self._source_info = source_info
        self._stats = {}
        self._preview_path = None
        self._has_draft = False
        self._saved_path = None
        self._display_name = ""
        self._save_active = False

        self.pane_header_widget.show()
        self.source_meta_label.setText(self._source_meta())
        self.output_meta_label.setText("No draft yet")
        self.file_label.setText(f"{source.name}   →   No draft yet")
        self.file_label.setToolTip(f"Source:\n{source}\n\nNo draft has been rendered yet.")
        self.status_title.setText(title)
        self._set_status_detail(detail, error=error)
        self._set_status_busy(False)
        self._set_video_controls_enabled(False)
        self.stack.setCurrentWidget(self.status_page)
        self._set_badge(False)
        self.save_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        self._set_hint_text(
            "The preview pane will fill automatically after the first render.",
            "The comparison is generated from the temporary draft.",
        )

    def begin_interpolation(
        self,
        source: Path,
        source_info: MediaInfo,
        display_name: str = "",
        output_hint: str = "",
    ) -> None:
        """Show progress in the pane while a new draft is being rendered."""
        self.stop_comparison()
        self._state = "interpolating"
        self._source = source
        self._output = None
        self._source_info = source_info
        self._stats = {}
        self._preview_path = None
        self._has_draft = False
        self._saved_path = None
        self._display_name = display_name
        self._save_active = False

        self.pane_header_widget.show()
        self.source_meta_label.setText(self._source_meta())
        self.output_meta_label.setText(output_hint or "Waiting for render")
        self._refresh_file_label()
        self.file_label.setToolTip(f"Original:\n{source}")
        self.status_title.setText("Interpolating video")
        self._set_status_detail(
            "Loading the model and preparing the first frames…", error=False
        )
        self.preview_progress.setValue(0)
        self._set_status_busy(True, busy=True)
        self._set_video_controls_enabled(False)
        self.stack.setCurrentWidget(self.status_page)
        self._set_badge(False)
        self.save_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        self._set_hint_text(
            "Interpolation is running locally. The preview loads automatically.",
            "A temporary draft is kept in GFI's data folder until you save or close.",
        )
        self._apply_mute(self.mute_button.isChecked())

    def update_interpolation(self, percent: int, detail: str) -> None:
        if self._state != "interpolating":
            return
        percent = max(0, min(100, int(percent)))
        self.preview_progress.setValue(percent)
        self.preview_percent.setText(f"{percent}%")
        self._set_status_detail(detail, error=False)

    def load_comparison(
        self,
        source: Path,
        output: Path,
        source_info: MediaInfo,
        stats: dict,
        *,
        display_name: str = "",
        saved_path: Path | None = None,
        autoplay: bool = False,
    ) -> None:
        """Configure the pane for one completed draft and start its proxy."""
        self.stop_comparison()
        self._source = source
        self._output = output
        self._source_info = source_info
        self._stats = dict(stats or {})
        self._preview_path = None
        self._duration_ms = 0
        self._active = True
        self._state = "preparing"
        self._autoplay = bool(autoplay)
        self._has_draft = True
        self._saved_path = saved_path
        self._display_name = display_name or output.name
        self._save_active = False

        self.pane_header_widget.show()
        self.source_meta_label.setText(self._source_meta())
        self.output_meta_label.setText(self._output_meta())
        self._refresh_file_label()
        self.set_draft_available(True, saved_path, self._display_name)
        self.preview_progress.setValue(0)
        self.preview_percent.setText("0%")
        self.status_title.setText("Building comparison preview")
        self._set_status_detail(
            "Generating a synchronized 60 fps SDR proxy — the preview will be "
            "paused when it is ready.",
            error=False,
        )
        self._set_status_busy(True, busy=True)
        self._set_video_controls_enabled(False)
        self.stack.setCurrentWidget(self.status_page)
        self.play_button.setText("Play")
        self.timeline.setRange(0, 0)
        self.time_label.setText("0:00 / 0:00")
        self._set_hint()
        self._apply_mute(self.mute_button.isChecked())
        QTimer.singleShot(0, self._start_preview)

    def show_error(
        self,
        title: str,
        detail: str,
        *,
        draft_available: bool = False,
        saved_path: Path | None = None,
        error: bool = True,
    ) -> None:
        """Show a failed or cancelled operation without hiding an existing draft."""
        self.stop_comparison()
        self._state = "error" if error else "idle"
        self._has_draft = draft_available
        self._saved_path = saved_path if draft_available else None
        self._save_active = False
        self._set_status_detail(detail, error=error)
        self.status_title.setText(title)
        self._set_status_busy(False)
        self._set_video_controls_enabled(False)
        self.stack.setCurrentWidget(self.status_page)
        self._set_badge(draft_available)
        self.save_button.setEnabled(draft_available)
        self.folder_button.setEnabled(bool(draft_available and saved_path))
        if draft_available:
            self.pane_header_widget.show()
            self._refresh_file_label()
            self._set_hint_text(
                "The draft is still available. Save a copy or try again.",
                "A failed comparison proxy never removes the rendered draft.",
            )
        else:
            self.pane_header_widget.hide()
            self.file_label.setText("No video loaded")
            self.file_label.setToolTip("")
            self._set_hint_text(
                "No draft was kept. Adjust the settings and try again.",
                "The previous draft, if any, is protected until a new render succeeds.",
            )
        self._apply_mute(self.mute_button.isChecked())

    def set_draft_available(
        self,
        available: bool,
        saved_path: Path | None = None,
        display_name: str = "",
    ) -> None:
        self._has_draft = bool(available)
        self._saved_path = saved_path if available else None
        if display_name:
            self._display_name = display_name
        self._refresh_file_label()
        self._set_badge(self._has_draft)
        if not self._save_active:
            self.save_button.setEnabled(self._has_draft)
            self.save_button.setText("Save video…")
        self.folder_button.setEnabled(bool(self._has_draft and self._saved_path))

    def set_save_progress(self, percent: int) -> None:
        self._save_active = True
        self.save_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        if percent < 0:
            self.save_button.setText("Saving…")
        else:
            self.save_button.setText(f"Saving {max(0, min(100, percent))}%")

    def finish_save(self, path: Path) -> None:
        self._save_active = False
        self.save_button.setText("Save video…")
        self._saved_path = path
        self._has_draft = True
        self._refresh_file_label()
        self._set_badge(True)
        self.save_button.setEnabled(True)
        self.folder_button.setEnabled(True)

    def cancel_save(self) -> None:
        self._save_active = False
        self.save_button.setText("Save video…")
        self.save_button.setEnabled(self._has_draft)
        self.folder_button.setEnabled(bool(self._has_draft and self._saved_path))

    def stop_comparison(self) -> None:
        """Stop playback and any preview build without destroying the panel."""
        self._active = False
        self._duration_ms = 0
        self._player_safe_stop()
        builder = self._builder
        self._builder = None
        if builder is not None:
            builder.stop()
            if builder.isRunning():
                if not builder.wait(2000):
                    builder.terminate()
                    builder.wait(1000)
            builder.deleteLater()

    def focus_panel(self) -> None:
        if self.video_page.isVisible():
            self.video_widget.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _player_safe_stop(self) -> None:
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except RuntimeError:
            pass

    def _set_status_busy(self, visible: bool, *, busy: bool = False) -> None:
        self.preview_progress.setVisible(visible)
        self.preview_percent.setVisible(visible)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _set_video_controls_enabled(self, enabled: bool) -> None:
        self.transport_widget.setVisible(enabled)
        self.play_button.setEnabled(enabled)
        self.timeline.setEnabled(enabled)
        self.shortcut_hint.setVisible(enabled)

    def _set_status_detail(self, text: str, *, error: bool) -> None:
        if text:
            self.status_detail.setText(text)
            wrapped_height = self.status_detail.heightForWidth(430)
            if wrapped_height > 0:
                self.status_detail.setMinimumHeight(min(260, wrapped_height))
        if self.status_detail.property("error") == error:
            return
        self.status_detail.setProperty("error", error)
        self.status_detail.style().unpolish(self.status_detail)
        self.status_detail.style().polish(self.status_detail)

    def _set_hint_text(self, text: str, tooltip: str = "") -> None:
        self.hint_label.setObjectName("compareHint")
        self.hint_label.setText(text)
        self.hint_label.setToolTip(tooltip)
        self.hint_label.style().unpolish(self.hint_label)
        self.hint_label.style().polish(self.hint_label)

    def _set_badge(self, visible: bool) -> None:
        if not visible:
            self.draft_badge.hide()
            return
        state = "saved" if self._saved_path is not None else "draft"
        self.draft_badge.setText("SAVED" if state == "saved" else "DRAFT")
        if self.draft_badge.property("state") != state:
            self.draft_badge.setProperty("state", state)
            self.draft_badge.style().unpolish(self.draft_badge)
            self.draft_badge.style().polish(self.draft_badge)
        if self._saved_path is not None:
            tooltip = f"Saved to:\n{self._saved_path}\n\nThe temporary draft is kept."
        else:
            tooltip = "Temporary draft — use Save video… to keep a copy."
        self.draft_badge.setToolTip(tooltip)
        self.draft_badge.show()

    def _refresh_file_label(self) -> None:
        if self._source is None:
            self.file_label.setText("No video loaded")
            return
        if self._saved_path is not None:
            target = self._saved_path.name
        else:
            target = self._display_name or (
                self._output.name if self._output is not None else "Interpolated draft"
            )
        self.file_label.setText(f"{self._source.name}   →   {target}")
        lines = [f"Original:\n{self._source}"]
        if self._saved_path is not None:
            lines.append(f"Saved copy:\n{self._saved_path}")
            lines.append("The temporary draft is still available for more tweaks.")
        else:
            lines.append("Saved copy: not created yet")
            lines.append("The interpolated result is a temporary draft.")
        self.file_label.setToolTip("\n\n".join(lines))

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 13)
        root.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        title = QLabel("Preview")
        title.setObjectName("compareTitle")
        self.draft_badge = QLabel("DRAFT")
        self.draft_badge.setObjectName("draftBadge")
        self.draft_badge.setProperty("state", "draft")
        self.draft_badge.hide()
        title_row.addWidget(title)
        title_row.addWidget(self.draft_badge)
        title_row.addStretch(1)
        self.file_label = ElidedLabel("")
        self.file_label.setObjectName("compareSubtitle")
        titles.addLayout(title_row)
        titles.addWidget(self.file_label)
        header.addLayout(titles, 1)

        self.folder_button = QPushButton("Open folder")
        self.folder_button.setObjectName("ghost")
        self.folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_button.setToolTip("Show the saved file in Explorer.")
        self.folder_button.clicked.connect(self._open_folder)
        header.addWidget(self.folder_button, 0, Qt.AlignmentFlag.AlignTop)

        self.save_button = QPushButton("Save video…")
        self.save_button.setObjectName("secondary")
        self.save_button.setMinimumWidth(108)
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_button.setToolTip("Save a copy of the draft (Ctrl+S).")
        self.save_button.setAccessibleName("Save interpolated video")
        self.save_button.clicked.connect(self.saveRequested.emit)
        header.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.pane_header_widget = QWidget()
        pane_header = QHBoxLayout(self.pane_header_widget)
        pane_header.setContentsMargins(0, 0, 0, 0)
        pane_header.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(1)
        left_name = QLabel("ORIGINAL")
        left_name.setObjectName("paneName")
        self.source_meta_label = QLabel("")
        self.source_meta_label.setObjectName("paneMeta")
        left.addWidget(left_name)
        left.addWidget(self.source_meta_label)

        divider = QFrame()
        divider.setObjectName("compareDivider")
        divider.setFixedWidth(1)
        divider.setFixedHeight(30)

        right = QVBoxLayout()
        right.setSpacing(1)
        right_name = QLabel("INTERPOLATED")
        right_name.setObjectName("paneNameAccent")
        self.output_meta_label = QLabel("")
        self.output_meta_label.setObjectName("paneMeta")
        right.addWidget(right_name)
        right.addWidget(self.output_meta_label)

        pane_header.addLayout(left, 1)
        pane_header.addWidget(divider, 0, Qt.AlignmentFlag.AlignVCenter)
        pane_header.addLayout(right, 1)
        root.addWidget(self.pane_header_widget)

        stage = QFrame()
        stage.setObjectName("videoStage")
        stage_layout = QVBoxLayout(stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.status_page = self._build_status_page()
        self.video_page = QWidget()
        self.video_page.setObjectName("videoPage")
        video_layout = QVBoxLayout(self.video_page)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(0)
        self.video_widget = QVideoWidget()
        self.video_widget.setObjectName("videoSurface")
        self.video_widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.video_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.video_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        video_layout.addWidget(self.video_widget)
        self.stack.addWidget(self.status_page)
        self.stack.addWidget(self.video_page)
        self.stack.setCurrentWidget(self.status_page)
        self.stack.setMinimumHeight(280)
        stage_layout.addWidget(self.stack)
        root.addWidget(stage, 1)

        root.addWidget(self._build_transport())

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.hint_label = ElidedLabel("")
        self.hint_label.setObjectName("compareHint")
        self.shortcut_hint = QLabel("Space play · ←/→ seek · M mute")
        self.shortcut_hint.setObjectName("compareHint")
        self.shortcut_hint.setAlignment(Qt.AlignmentFlag.AlignRight)
        footer.addWidget(self.hint_label, 1)
        footer.addWidget(self.shortcut_hint)
        root.addLayout(footer)

    def _build_status_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("statusPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(9)
        layout.addStretch(1)

        self.spinner = LoadingSpinner(page)
        self.status_title = QLabel("Preview appears here")
        self.status_title.setObjectName("previewTitle")
        self.status_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.status_detail = QLabel(
            "Load a video, interpolate it, and compare the original with the result."
        )
        self.status_detail.setObjectName("previewDetail")
        self.status_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_detail.setWordWrap(True)
        self.status_detail.setFixedWidth(430)

        self.preview_progress = QProgressBar()
        self.preview_progress.setRange(0, 100)
        self.preview_progress.setValue(0)
        self.preview_progress.setTextVisible(False)
        self.preview_progress.setFixedWidth(320)
        self.preview_progress.setFixedHeight(8)

        self.preview_percent = QLabel("0%")
        self.preview_percent.setObjectName("paneMeta")
        self.preview_percent.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.status_title, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.status_detail, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(3)
        layout.addWidget(self.preview_progress, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.preview_percent, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        return page

    def _build_transport(self) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)

        self.play_button = QPushButton("Play")
        self.play_button.setObjectName("transport")
        self.play_button.setMinimumWidth(72)
        self.play_button.setEnabled(False)
        self.play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_button.setToolTip("Play or pause the synchronized preview (Space).")
        self.play_button.setAccessibleName("Play or pause comparison")
        self.play_button.clicked.connect(self.toggle_playback)
        row.addWidget(self.play_button)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("timeLabel")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.time_label)

        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.timeline.setPageStep(5000)
        self.timeline.setSingleStep(1000)
        self.timeline.setEnabled(False)
        self.timeline.setToolTip("Seek through the comparison.")
        self.timeline.setAccessibleName("Comparison timeline")
        self.timeline.valueChanged.connect(self._timeline_changed)
        row.addWidget(self.timeline, 1)

        self.mute_button = QPushButton("Muted")
        self.mute_button.setObjectName("transport")
        self.mute_button.setCheckable(True)
        self.mute_button.setChecked(True)
        self.mute_button.setMinimumWidth(72)
        self.mute_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute_button.setToolTip("Toggle preview audio.")
        self.mute_button.setAccessibleName("Mute comparison audio")
        self.mute_button.toggled.connect(self._apply_mute)
        row.addWidget(self.mute_button)

        self.loop_button = QPushButton("Loop")
        self.loop_button.setObjectName("transport")
        self.loop_button.setCheckable(True)
        self.loop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.loop_button.setToolTip("Restart automatically at the end.")
        self.loop_button.setAccessibleName("Loop comparison")
        row.addWidget(self.loop_button)

        self.transport_widget = widget
        widget.setVisible(False)
        return widget

    def _source_meta(self) -> str:
        info = self._source_info
        if info is None:
            return ""
        parts = [
            f"{float(info.fps):g} fps",
            f"{info.display_width} × {info.display_height}",
            f"{info.bit_depth}-bit",
        ]
        if info.is_hdr:
            parts.append(info.hdr_kind or "HDR")
        return "   ·   ".join(parts)

    def _output_meta(self) -> str:
        info = self._source_info
        if info is None:
            return ""
        width = int(self._stats.get("width") or info.display_width)
        height = int(self._stats.get("height") or info.display_height)
        out_fps = float(self._stats.get("out_fps") or 0.0)
        if out_fps <= 0 and info.duration > 0:
            out_fps = float(self._stats.get("output_frames") or 0) / info.duration
        bit_depth = int(self._stats.get("bit_depth") or info.bit_depth)
        parts = []
        if out_fps > 0:
            parts.append(f"{out_fps:g} fps")
        parts.append(f"{width} × {height}")
        parts.append(f"{bit_depth}-bit")
        if self._stats.get("hdr_output"):
            parts.append(info.hdr_kind or "HDR")
        return "   ·   ".join(parts)

    def _set_hint(self) -> None:
        if self.hint_label.objectName() != "compareHint":
            self.hint_label.setObjectName("compareHint")
            self.hint_label.style().unpolish(self.hint_label)
            self.hint_label.style().polish(self.hint_label)
        self.hint_label.setToolTip("")
        if self._source_info is None:
            self.hint_label.setText("")
            return
        if self._source_info.is_hdr or self._stats.get("hdr_output"):
            self.hint_label.setText("HDR tone-mapped for preview · output unchanged")
            self.hint_label.setToolTip(
                "The 10-bit/HDR output is untouched; only this proxy is tone-mapped."
            )
        else:
            self.hint_label.setText("60 fps SDR preview · output unchanged")
            self.hint_label.setToolTip(
                "The rendered output is untouched; this is a lightweight SDR proxy."
            )

    # -------------------------------------------------------------- playback

    def _setup_player(self) -> None:
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.8)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.errorOccurred.connect(self._on_player_error)
        self._apply_mute(True)

    def _install_shortcuts(self) -> None:
        for sequence, slot in (
            ("Space", self.toggle_playback),
            ("Left", lambda: self._seek_by(-5000)),
            ("Right", lambda: self._seek_by(5000)),
            ("M", lambda: self.mute_button.toggle()),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

    def _seek_by(self, milliseconds: int) -> None:
        if self._duration_ms <= 0:
            return
        position = max(
            0, min(self._duration_ms, self.player.position() + milliseconds)
        )
        self.player.setPosition(position)

    def toggle_playback(self) -> None:
        if not self.play_button.isEnabled():
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        duration = self.player.duration()
        if duration > 0 and self.player.position() >= duration - 100:
            self.player.setPosition(0)
        self.player.play()

    def _apply_mute(self, muted: bool) -> None:
        if not hasattr(self, "audio_output"):
            return
        self.audio_output.setMuted(bool(muted))
        self.mute_button.setText("Muted" if muted else "Sound")

    def _timeline_changed(self, value: int) -> None:
        if self._updating_timeline:
            return
        self.player.setPosition(value)
        total = self._duration_ms
        self.time_label.setText(f"{_fmt_clock(value)} / {_fmt_clock(total)}")

    def _on_position_changed(self, position: int) -> None:
        if not self.timeline.isSliderDown():
            self._updating_timeline = True
            self.timeline.setValue(position)
            self._updating_timeline = False
        total = self._duration_ms
        self.time_label.setText(f"{_fmt_clock(position)} / {_fmt_clock(total)}")

    def _on_duration_changed(self, duration: int) -> None:
        self._duration_ms = max(0, duration)
        self._updating_timeline = True
        self.timeline.setRange(0, self._duration_ms)
        self.timeline.setPageStep(max(1000, self._duration_ms // 20))
        self._updating_timeline = False
        self.time_label.setText(
            f"{_fmt_clock(self.player.position())} / {_fmt_clock(self._duration_ms)}"
        )

    def _on_playback_state_changed(self, state) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText("Pause")
        else:
            self.play_button.setText("Play")

    def _on_media_status_changed(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.loop_button.isChecked():
                self.player.setPosition(0)
                self.player.play()
            else:
                self.play_button.setText("Play")
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._show_playback_error("The preview file could not be played.")

    def _on_player_error(self, error, error_string: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            self._show_playback_error(error_string)

    def _show_playback_error(self, message: str) -> None:
        if not self._active or not self.isVisible():
            return
        self.hint_label.setObjectName("compareWarning")
        self.hint_label.setText(f"Playback error — {message}")
        self.hint_label.setToolTip(message)
        self.hint_label.style().unpolish(self.hint_label)
        self.hint_label.style().polish(self.hint_label)

    # --------------------------------------------------------------- preview

    def _start_preview(self) -> None:
        if not self._active or not self.isVisible():
            return
        if self._source is None or self._output is None:
            return
        try:
            path = preview_cache_path(self._source, self._output)
        except OSError as exc:
            self._show_preview_error(f"Could not prepare the preview cache.\n{exc}")
            return

        self._preview_path = path
        try:
            _prune_cache(keep=path)
        except OSError:
            pass
        try:
            if path.is_file():
                if path.stat().st_size > 0:
                    self._preview_ready(str(path))
                    return
                path.unlink(missing_ok=True)
        except OSError:
            pass

        self._builder = PreviewBuilder(
            self._source_info,
            self._output,
            path,
            self,
        )
        self._builder.progress.connect(self._on_preview_progress)
        self._builder.ready.connect(self._preview_ready)
        self._builder.failed.connect(self._show_preview_error)
        self._builder.cancelled.connect(self._on_preview_cancelled)
        self._builder.finished.connect(self._on_builder_finished)
        self._builder.start()

    def _on_preview_progress(self, percent: int) -> None:
        if not self._active or not self.isVisible():
            return
        percent = max(0, min(100, int(percent)))
        self.preview_progress.setValue(percent)
        self.preview_percent.setText(f"{percent}%")
        self._set_status_detail(
            f"Generating the side-by-side preview  ·  {percent}%\n"
            "The draft is already safe; the preview loads when this finishes.",
            error=False,
        )

    def _preview_ready(self, path: str) -> None:
        if not self._active or not self.isVisible():
            return
        self._preview_path = Path(path)
        self._state = "ready"
        self._set_status_busy(False)
        self.stack.setCurrentWidget(self.video_page)
        self._set_video_controls_enabled(True)
        self.play_button.setText("Play")
        self.player.setSource(QUrl.fromLocalFile(path))
        if self._autoplay:
            self.player.play()
        else:
            self.player.pause()
        self._set_hint()

    def _show_preview_error(self, message: str) -> None:
        if not self._active or not self.isVisible():
            return
        detail = message.strip() or "Unknown FFmpeg error."
        if len(detail) > 1600:
            detail = detail[:1600].rstrip() + "…"
        self._state = "error"
        self._active = False
        self._set_status_busy(False)
        self._set_video_controls_enabled(False)
        self.stack.setCurrentWidget(self.status_page)
        self.status_title.setText("Preview unavailable")
        self._set_status_detail(
            "The interpolation draft is still available. Only the comparison "
            "proxy could not be generated.\n\n"
            f"{detail}",
            error=True,
        )
        self._set_badge(self._has_draft)
        self.save_button.setEnabled(self._has_draft and not self._save_active)
        self.folder_button.setEnabled(bool(self._has_draft and self._saved_path))
        self._set_hint_text(
            "The draft is safe; save it or start another render.",
            "The generated preview proxy is separate from the draft.",
        )

    def _on_preview_cancelled(self) -> None:
        if self._active and self.isVisible():
            self._show_preview_error("Preview generation was cancelled.")

    def _on_builder_finished(self) -> None:
        builder = self.sender()
        if builder is self._builder:
            self._builder = None
        if isinstance(builder, PreviewBuilder):
            builder.deleteLater()

    # ---------------------------------------------------------------- misc

    def _open_folder(self) -> None:
        if self._saved_path is None:
            return
        folder = self._saved_path.parent
        if os.name == "nt":
            try:
                os.startfile(str(folder))  # type: ignore[attr-defined]
                return
            except OSError:
                pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
