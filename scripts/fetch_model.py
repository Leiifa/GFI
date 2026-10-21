"""Download the RIFE ONNX models used by GFI.

The models are MIT-licensed. This script pins URLs and hashes so the checkout
is reproducible. ``live`` is the timestep model used for general footage;
``anime`` is RIFE 4.9, which keeps sharp animation lines and is paired with a
lower scene-cut threshold in the app.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

MODELS = {
    "live": {
        "name": "RIFE_fp32_timestep.onnx",
        "url": "https://huggingface.co/walterlow/RIFE_fp32_timestep/resolve/main/RIFE_fp32_timestep.onnx",
        "sha256": "4da60c1f20d42dba4f21503140940aa48a488585ead977532bf22e95a0319327",
    },
    "anime": {
        "name": "RIFE_4.9.onnx",
        "url": "https://huggingface.co/edgetools/rife/resolve/main/rife49.onnx",
        "sha256": "76e4cef9ab42fa7dd4e8f6e4aba47462051e3faa969e4bca6479784fbab0ac6f",
    },
}
DEST_DIR = Path(__file__).resolve().parents[1] / "models"


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "gfi/0.1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest.with_suffix(dest.suffix + ".part")
        seen = 0
        next_report = 0
        with temporary.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                seen += len(chunk)
                if seen >= next_report:
                    if total:
                        print(f"\r  {seen / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", file=sys.stderr)
                    else:
                        print(f"\r  {seen / 1e6:6.1f} MB", end="", file=sys.stderr)
                    next_report = seen + 4 * 1024 * 1024
        print(file=sys.stderr)
        temporary.replace(dest)


def _fetch(key: str, force: bool) -> bool:
    spec = MODELS[key]
    dest = DEST_DIR / spec["name"]
    expected = spec["sha256"]

    if dest.is_file() and not force:
        actual = _digest(dest)
        if actual == expected:
            print(f"{key:5s}: already present  {dest}")
            return True
        print(f"{key:5s}: hash mismatch, re-downloading  {dest}", file=sys.stderr)

    print(f"{key:5s}: downloading {spec['url']}")
    try:
        _download(spec["url"], dest)
    except Exception as exc:  # noqa: BLE001 - CLI helper
        print(f"error: download failed: {exc}", file=sys.stderr)
        return False

    actual = _digest(dest)
    if actual != expected:
        dest.unlink(missing_ok=True)
        print(f"error: sha256 mismatch\n  expected {expected}\n  got      {actual}", file=sys.stderr)
        return False

    print(f"{key:5s}: saved {dest} ({dest.stat().st_size / 1e6:.1f} MB, sha256 ok)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=["all", *MODELS],
        default="all",
        help="model to fetch (default: all)",
    )
    parser.add_argument("--force", action="store_true", help="re-download even if the hash matches")
    args = parser.parse_args()

    keys = list(MODELS) if args.profile == "all" else [args.profile]
    ok = True
    for key in keys:
        ok = _fetch(key, args.force) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
