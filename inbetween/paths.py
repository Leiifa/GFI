from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def is_portable() -> bool:
    """True when running from the packaged portable build."""
    return bool(getattr(sys, "frozen", False))


def data_root() -> Path:
    if is_portable():
        return Path(sys.executable).resolve().parent / "gfi-data"
    local = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local:
        return Path(local) / "GFI"
    return Path(tempfile.gettempdir()) / "GFI"


def ensure_data_root() -> Path:
    """Create the data folder and prove it is writable."""
    root = data_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-test"
        probe.write_text("", encoding="ascii")
        probe.unlink()
    except OSError as exc:
        if is_portable():
            raise OSError(
                f"GFI cannot write to {root}.\n\n"
                "Extract GFI to a writable folder such as Desktop, Documents, "
                "or a USB drive, then run gfi.exe again."
            ) from exc
        raise
    return root


def resource_root() -> Path:
    """Folder that contains bundled assets, models and tools."""
    if is_portable():
        bundle = getattr(sys, "_MEIPASS", None)
        return Path(bundle) if bundle else Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]
