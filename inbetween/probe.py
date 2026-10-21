from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    width: int
    height: int
    display_width: int
    display_height: int
    fps: Fraction
    duration: float
    frame_count: int
    pix_fmt: str
    color_primaries: str
    color_transfer: str
    colorspace: str
    color_range: str
    rotation: int
    has_audio: bool
    audio_streams: int
    subtitle_streams: int
    real_fps: Fraction = Fraction(0)
    bit_rate: int = 0
    codec: str = ""
    profile: str = ""

    @property
    def bit_depth(self) -> int:
        """Return the source bit depth encoded in the pixel format."""
        for depth in (16, 12, 10):
            if str(depth) in self.pix_fmt:
                return depth
        return 8

    @property
    def is_8bit(self) -> bool:
        return self.bit_depth == 8

    @property
    def is_hdr(self) -> bool:
        return self.color_transfer.lower() in {"smpte2084", "arib-std-b67"}

    @property
    def hdr_kind(self) -> str:
        transfer = self.color_transfer.lower()
        if transfer == "smpte2084":
            return "HDR10"
        if transfer == "arib-std-b67":
            return "HLG"
        return ""

    @property
    def is_vfr(self) -> bool:
        if self.fps <= 0 or self.real_fps <= 0:
            return False
        expected = float(self.real_fps)
        return abs(float(self.fps) - expected) > max(0.01, expected * 0.002)


def _fraction(value: str | None) -> Fraction:
    if not value:
        return Fraction(0)
    try:
        num, den = value.split("/")
        if int(den) == 0 or int(num) == 0:
            return Fraction(0)
        return Fraction(int(num), int(den))
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def _rotation(stream: dict) -> int:
    for side_data in stream.get("side_data_list", []) or []:
        if "rotation" in side_data:
            try:
                return int(round(float(side_data["rotation"])))
            except (TypeError, ValueError):
                pass
    try:
        return int(round(float(stream.get("tags", {}).get("rotate", 0))))
    except (TypeError, ValueError):
        return 0


def probe(ffprobe: str, path: Path) -> MediaInfo:
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        creationflags=CREATE_NO_WINDOW,
    )
    data = json.loads(result.stdout)

    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError(f"no video stream found in {path}")

    audio_streams = sum(1 for s in streams if s.get("codec_type") == "audio")
    subtitle_streams = sum(1 for s in streams if s.get("codec_type") == "subtitle")

    fmt = data.get("format", {})
    duration = 0.0
    for candidate in (video.get("duration"), fmt.get("duration")):
        try:
            duration = float(candidate)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue

    fps = _fraction(video.get("avg_frame_rate"))
    if fps <= 0:
        fps = _fraction(video.get("r_frame_rate"))
    if fps <= 0:
        fps = Fraction(30, 1)
    real_fps = _fraction(video.get("r_frame_rate"))
    if real_fps <= 0:
        real_fps = fps

    bit_rate = 0
    for candidate in (video.get("bit_rate"), fmt.get("bit_rate")):
        try:
            bit_rate = int(candidate)
            if bit_rate > 0:
                break
        except (TypeError, ValueError):
            continue

    frame_count = 0
    for candidate in (video.get("nb_frames"), fmt.get("nb_frames")):
        try:
            frame_count = int(candidate)
            if frame_count > 0:
                break
        except (TypeError, ValueError):
            continue
    if frame_count <= 0 and duration > 0:
        frame_count = max(1, round(duration * float(fps)))

    rotation = _rotation(video)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError(f"could not read video dimensions from {path}")

    swap = abs(rotation) % 180 == 90
    display_width, display_height = (height, width) if swap else (width, height)

    return MediaInfo(
        path=path,
        width=width,
        height=height,
        display_width=display_width,
        display_height=display_height,
        fps=fps,
        duration=duration,
        frame_count=frame_count,
        pix_fmt=str(video.get("pix_fmt") or "yuv420p"),
        color_primaries=str(video.get("color_primaries") or "unknown"),
        color_transfer=str(video.get("color_transfer") or "unknown"),
        colorspace=str(video.get("color_space") or "unknown"),
        color_range=str(video.get("color_range") or "unknown"),
        rotation=rotation,
        has_audio=audio_streams > 0,
        audio_streams=audio_streams,
        subtitle_streams=subtitle_streams,
        real_fps=real_fps,
        bit_rate=bit_rate,
        codec=str(video.get("codec_name") or ""),
        profile=str(video.get("profile") or ""),
    )
