"""Generate the Windows icon from the GFI logo."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _png(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def make_icon(source: Path, destination: Path) -> None:
    image = QImage(str(source))
    if image.isNull():
        raise SystemExit(f"could not read {source}")

    payloads = []
    for size in SIZES:
        scaled = image.scaled(
            size,
            size,
            aspectMode=Qt.AspectRatioMode.KeepAspectRatio,
            mode=Qt.TransformationMode.SmoothTransformation,
        )
        payloads.append(_png(scaled))

    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = len(header) + 16 * len(payloads)
    directory = bytearray()
    for size, payload in zip(SIZES, payloads):
        dimension = 0 if size == 256 else size
        directory += struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(payload), offset
        )
        offset += len(payload)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(header + directory + b"".join(payloads))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "assets" / "gfi-logo.png")
    parser.add_argument("--output", type=Path, default=ROOT / "assets" / "gfi.ico")
    args = parser.parse_args()
    QApplication([])
    make_icon(args.source, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
