from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .paths import ensure_data_root

DRAFT_DIR_NAME = "drafts"
DRAFT_PREFIX = "draft_"
_COPY_CHUNK = 4 * 1024 * 1024


def draft_root() -> Path:
    """Return the portable or per-user folder used for temporary drafts."""
    root = ensure_data_root() / DRAFT_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_stem(source: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source.stem).strip("._-")
    return (stem or "video")[:48]


def allocate_draft(source: Path) -> Path:
    """Reserve a unique MP4 path for one interpolation draft."""
    name = f"{DRAFT_PREFIX}{_safe_stem(source)}_{uuid.uuid4().hex[:10]}.mp4"
    return draft_root() / name


def is_draft(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.parent.resolve() == draft_root().resolve() and path.name.startswith(
            DRAFT_PREFIX
        )
    except OSError:
        return False


def remove_draft(path: Path | None) -> None:
    """Delete a managed draft, never an arbitrary user file."""
    if not is_draft(path):
        return
    try:
        assert path is not None
        path.unlink(missing_ok=True)
    except OSError:
        pass


def cleanup_drafts() -> None:
    """Remove drafts left behind by an earlier session."""
    try:
        root = draft_root()
        entries = list(root.glob(f"{DRAFT_PREFIX}*"))
    except OSError:
        return
    for path in entries:
        try:
            if path.is_file():
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _part_path(destination: Path) -> Path:
    return destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.part"
    )


def copy_draft(source: Path, destination: Path) -> None:
    """Copy a draft to a user destination, replacing only after a complete copy."""
    temp = _part_path(destination)
    try:
        with source.open("rb") as src, temp.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=_COPY_CHUNK)
            dst.flush()
            os.fsync(dst.fileno())
        try:
            shutil.copystat(source, temp)
        except OSError:
            pass
        os.replace(temp, destination)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


class SaveDraftWorker(QThread):
    """Copy one draft without blocking the UI."""

    progress = Signal(int)
    saved = Signal(str)
    failed = Signal(str)

    def __init__(self, source: Path, destination: Path, parent=None) -> None:
        super().__init__(parent)
        self._source = source
        self._destination = destination

    def run(self) -> None:
        temp = _part_path(self._destination)
        try:
            total = max(1, self._source.stat().st_size)
            copied = 0
            last_percent = -1
            with self._source.open("rb") as src, temp.open("wb") as dst:
                while True:
                    chunk = src.read(_COPY_CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
                    copied += len(chunk)
                    percent = min(100, int(copied / total * 100))
                    if percent != last_percent:
                        last_percent = percent
                        self.progress.emit(percent)
                dst.flush()
                os.fsync(dst.fileno())
            try:
                shutil.copystat(self._source, temp)
            except OSError:
                pass
            os.replace(temp, self._destination)
            self.progress.emit(100)
            self.saved.emit(str(self._destination))
        except Exception as exc:  # noqa: BLE001 - surfaced as a save error dialog
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            self.failed.emit(str(exc) or exc.__class__.__name__)
