"""Print the source/output facts that affect interpolation quality.

This is a diagnostic helper, not part of the render pipeline. It reports
10-bit/HDR color handling, VFR timestamps, bitrate changes, and codec/pixel
format changes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from inbetween.ffmpeg import find_tool  # noqa: E402


def _fraction(value: str | None) -> Fraction:
    if not value:
        return Fraction(0)
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def _rate_text(value: Fraction) -> str:
    if value <= 0:
        return "?"
    number = float(value)
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _bit_depth(pix_fmt: str) -> int:
    for depth in (16, 12, 10):
        if str(depth) in pix_fmt:
            return depth
    return 8


def _bitrate(stream: dict, fmt: dict) -> int:
    for value in (stream.get("bit_rate"), fmt.get("bit_rate")):
        try:
            number = int(value)
            if number > 0:
                return number
        except (TypeError, ValueError):
            continue
    return 0


def inspect(path: Path, ffprobe: str) -> dict:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError(f"no video stream in {path}")

    fmt = data.get("format", {})
    pix_fmt = str(video.get("pix_fmt") or "unknown")
    avg = _fraction(video.get("avg_frame_rate"))
    real = _fraction(video.get("r_frame_rate"))
    transfer = str(video.get("color_transfer") or "unknown")
    primaries = str(video.get("color_primaries") or "unknown")
    colorspace = str(video.get("color_space") or "unknown")
    color_range = str(video.get("color_range") or "unknown")
    bitrate = _bitrate(video, fmt)
    depth = _bit_depth(pix_fmt)
    hdr = transfer.lower() in {"smpte2084", "arib-std-b67"}
    vfr = False
    if avg > 0 and real > 0 and abs(float(avg) - float(real)) / float(real) > 0.005:
        vfr = True

    warnings: list[str] = []
    if depth > 8 and not hdr:
        warnings.append(
            f"{depth}-bit SDR video: GFI keeps 10-bit precision and writes "
            "yuv420p10le by default."
        )
    if hdr:
        warnings.append(
            "HDR video: GFI writes 10-bit HDR and preserves the color tags "
            "by default; use --hdr tonemap for SDR output."
        )
    if vfr:
        warnings.append(
            f"VFR-looking timestamps (avg {_rate_text(avg)} vs real "
            f"{_rate_text(real)} fps): the decoder forces CFR, which can add "
            "duplicates or judder before interpolation."
        )

    return {
        "path": path,
        "codec": str(video.get("codec_name") or "?"),
        "profile": str(video.get("profile") or ""),
        "pix_fmt": pix_fmt,
        "depth": depth,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "avg_fps": avg,
        "real_fps": real,
        "frames": str(video.get("nb_frames") or "?"),
        "duration": float(fmt.get("duration") or 0),
        "bitrate": bitrate,
        "size": path.stat().st_size,
        "transfer": transfer,
        "primaries": primaries,
        "colorspace": colorspace,
        "color_range": color_range,
        "hdr": hdr,
        "vfr": vfr,
        "warnings": warnings,
        "audio": [s for s in streams if s.get("codec_type") == "audio"],
    }


def _print_one(item: dict, show_advice: bool = True) -> None:
    profile = f" ({item['profile']})" if item["profile"] else ""
    bitrate = f"{item['bitrate'] / 1e6:.2f} Mb/s" if item["bitrate"] else "?"
    duration = f"{item['duration']:.3f} s" if item["duration"] else "?"
    size = f"{item['size'] / 1e6:.1f} MB"
    print(item["path"])
    print(
        f"  video    {item['codec']}{profile}  "
        f"{item['width']}x{item['height']}  {item['pix_fmt']}  "
        f"{_rate_text(item['avg_fps'])} fps"
    )
    print(
        f"  timing   avg={_rate_text(item['avg_fps'])}  "
        f"real={_rate_text(item['real_fps'])}  "
        f"frames={item['frames']}  duration={duration}"
    )
    print(f"  bitrate  {bitrate}  |  file {size}")
    print(
        f"  color    {item['primaries']} / {item['transfer']} / "
        f"{item['colorspace']} / {item['color_range']}"
    )
    if item["audio"]:
        codecs = ", ".join(str(s.get("codec_name") or "?") for s in item["audio"])
        print(f"  audio    {len(item['audio'])} stream(s): {codecs}")
    else:
        print("  audio    none")
    if show_advice:
        for warning in item["warnings"]:
            print(f"  note     {warning}")


def _compare(source: dict, output: dict) -> None:
    print()
    print("comparison")
    print(
        f"  resolution  {source['width']}x{source['height']} -> "
        f"{output['width']}x{output['height']}"
    )
    print(
        f"  codec       {source['codec']} -> {output['codec']}  "
        f"({source['pix_fmt']} -> {output['pix_fmt']})"
    )
    print(
        f"  frame rate  {_rate_text(source['avg_fps'])} -> "
        f"{_rate_text(output['avg_fps'])} fps"
    )
    if source["bitrate"] and output["bitrate"]:
        ratio = output["bitrate"] / source["bitrate"]
        print(
            f"  bitrate     {source['bitrate'] / 1e6:.2f} -> "
            f"{output['bitrate'] / 1e6:.2f} Mb/s  ({ratio:.2f}x)"
        )
    if source["duration"] and output["duration"]:
        ratio = output["size"] / source["size"]
        print(
            f"  file size   {source['size'] / 1e6:.1f} -> "
            f"{output['size'] / 1e6:.1f} MB  ({ratio:.2f}x)"
        )
    if output["depth"] < source["depth"]:
        print(f"  warning     output bit depth is lower ({source['depth']} -> {output['depth']})")
    elif output["depth"] > 8:
        print(f"  note        {output['depth']}-bit output preserved")
    if source["hdr"] and output["hdr"]:
        print("  note        HDR color tags were preserved in a 10-bit output")
    elif source["hdr"]:
        print("  note        HDR source was tone-mapped to SDR BT.709")
    if source["vfr"]:
        print("  warning     source was VFR; cadence may have been changed by CFR decode")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="source and/or output videos")
    args = parser.parse_args()

    ffprobe = find_tool("ffprobe")
    items: list[dict] = []
    try:
        for index, path in enumerate(args.paths):
            item = inspect(path.expanduser().resolve(), ffprobe)
            items.append(item)
            # In a source/output pair, show the advice once, on the source.
            _print_one(item, show_advice=(len(args.paths) == 1 or index == 0))
            print()
    except Exception as exc:  # noqa: BLE001 - diagnostic helper
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if len(items) == 2:
        _compare(items[0], items[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
