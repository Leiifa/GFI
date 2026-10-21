from __future__ import annotations

import os
import shutil
from pathlib import Path

from .paths import resource_root
from .probe import MediaInfo

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
SDR_COLOR_FALLBACK = ("bt709", "bt709", "bt709")
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
HDR_ENCODERS = {"libx265", "hevc_amf", "av1_amf"}
TEN_BIT_ENCODERS = {"libx264", "libx265", "hevc_amf", "av1_amf"}
TONEMAP_TO_SDR = (
    "zscale=t=linear:npl=100,"
    "format=gbrpf32le,"
    "tonemap=tonemap=hable:desat=0,"
    "zscale=p=bt709:t=bt709:m=bt709:r=tv"
)


def _candidates(name: str) -> list[Path]:
    exe = f"{name}.exe" if os.name == "nt" else name
    here = resource_root()
    paths: list[Path] = []

    env = os.environ.get(f"{name.upper()}_BIN")
    if env:
        paths.append(Path(env))

    local = os.environ.get("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        paths.extend(sorted(packages.glob(f"Gyan.FFmpeg_*/**/bin/{exe}")))

    paths.extend(
        [
            here / "tools" / "ffmpeg" / "bin" / exe,
            Path(r"C:\ffmpeg\bin") / exe,
            Path(r"C:\Program Files\ffmpeg\bin") / exe,
        ]
    )
    return paths


def find_tool(name: str) -> str:
    exe = f"{name}.exe" if os.name == "nt" else name
    bundled = resource_root() / "tools" / "ffmpeg" / "bin" / exe
    if bundled.is_file():
        return str(bundled)
    found = shutil.which(name)
    if found:
        return found
    for candidate in _candidates(name):
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(
        f"{name} not found. Install it (winget install Gyan.FFmpeg) or set "
        f"{name.upper()}_BIN to the executable."
    )


def _sdr_triplet(info: MediaInfo) -> tuple[str, str, str]:
    """Return consistent SDR color tags.

    Some 10-bit sources have a matrix tag (often bt2020nc from the encoder)
    but no primaries or transfer. Letting those through makes hardware
    encoders guess, sometimes labelling ordinary SDR as HDR. A complete
    BT.709 triplet is the safe fallback.
    """
    primaries = info.color_primaries.lower()
    transfer = info.color_transfer.lower()
    matrix = info.colorspace.lower()
    if (
        primaries in {"bt709", "bt470bg", "smpte170m", "bt2020"}
        and transfer in {"bt709", "bt470bg", "smpte170m", "bt2020-10", "bt2020-12"}
        and matrix in {"bt709", "bt470bg", "smpte170m", "bt2020nc", "bt2020c"}
    ):
        return primaries, transfer, matrix
    return SDR_COLOR_FALLBACK


def _output_color_args(info: MediaInfo, hdr_output: bool) -> list[str]:
    if hdr_output:
        primaries = (
            info.color_primaries
            if info.color_primaries.lower() == "bt2020"
            else "bt2020"
        )
        transfer = (
            info.color_transfer
            if info.color_transfer.lower() in HDR_TRANSFERS
            else "smpte2084"
        )
        # The output is always 4:2:0, so non-constant-luminance BT.2020 is the
        # correct matrix even if the source carried a 4:4:4 constant-luminance
        # tag.
        colorspace = "bt2020nc"
        return [
            "-color_primaries",
            primaries,
            "-color_trc",
            transfer,
            "-colorspace",
            colorspace,
            "-color_range",
            "tv",
        ]

    if info.is_hdr:
        # A tone-mapped HDR source is ordinary SDR BT.709.
        primaries, transfer, matrix = SDR_COLOR_FALLBACK
    else:
        primaries, transfer, matrix = _sdr_triplet(info)
    return [
        "-color_primaries",
        primaries,
        "-color_trc",
        transfer,
        "-colorspace",
        matrix,
        "-color_range",
        "tv",
    ]


def _raw_input_color_args(info: MediaInfo, hdr_output: bool) -> list[str]:
    """Color tags for the raw RGB pipe.

    The decoder emits full-range RGB. Supplying the tags here keeps the
    properties on the frames through the scale filter and into the encoder;
    without them some encoders drop primaries/transfer metadata.
    """
    args = _output_color_args(info, hdr_output)
    for index, value in enumerate(args):
        if value == "-color_range" and index + 1 < len(args):
            args[index + 1] = "pc"
            break
    return args


def _output_matrix(info: MediaInfo, hdr_output: bool) -> str:
    if hdr_output:
        return "bt2020nc"
    if info.is_hdr:
        return SDR_COLOR_FALLBACK[2]
    return _sdr_triplet(info)[2]


def build_decode_cmd(
    ffmpeg: str,
    info: MediaInfo,
    bit_depth: int = 8,
    hdr_output: bool = False,
) -> list[str]:
    """Build the decoder command.

    ``bit_depth`` is the requested output depth. 16-bit RGB is used whenever
    the encode side needs more than 8 bits, so the 10-bit encoder receives
    real 10-bit source data instead of a quantized 8-bit round trip.
    """
    use_16 = bit_depth > 8 or hdr_output
    rgb_format = "rgb48le" if use_16 else "rgb24"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(info.path),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
    ]

    if info.is_hdr and not hdr_output:
        # Preserve precision while converting PQ/HLG to display-referred SDR.
        cmd += ["-vf", f"{TONEMAP_TO_SDR},format={rgb_format}"]
    elif use_16:
        # HDR is kept in its original transfer function (PQ or HLG). RIFE
        # interpolates in the encoded RGB space and the encoder restores the
        # same tags, so a 10-bit HDR source stays HDR instead of being
        # tone-mapped down to SDR.
        cmd += ["-vf", f"format={rgb_format}"]
    else:
        cmd += ["-vf", "format=rgb24"]

    cmd += [
        "-fps_mode",
        "cfr",
        "-r",
        str(info.fps),
        "-f",
        "rawvideo",
        "-pix_fmt",
        rgb_format,
        "pipe:1",
    ]
    return cmd


def _encoder_args(
    encoder: str,
    crf: int,
    preset: str,
    bit_depth: int,
    hdr_output: bool,
) -> list[str]:
    if hdr_output and encoder not in HDR_ENCODERS:
        raise ValueError("HDR output requires libx265, hevc_amf or av1_amf")
    if bit_depth > 8 and encoder not in TEN_BIT_ENCODERS:
        raise ValueError(f"{encoder} cannot write 10-bit output")

    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
    if encoder == "libx265":
        return ["-c:v", "libx265", "-preset", preset, "-crf", str(crf), "-tag:v", "hvc1"]
    if encoder in {"h264_amf", "hevc_amf", "av1_amf"}:
        args = [
            "-c:v",
            encoder,
            "-quality",
            "quality",
            "-rc",
            "cqp",
            "-qp_i",
            str(crf),
            "-qp_p",
            str(crf),
        ]
        if bit_depth > 8 and encoder in {"hevc_amf", "av1_amf"}:
            args += ["-bitdepth", "10"]
        if encoder == "hevc_amf":
            args += ["-tag:v", "hvc1"]
        return args
    raise ValueError(f"unsupported encoder: {encoder}")


def build_encode_cmd(
    ffmpeg: str,
    info: MediaInfo,
    output: Path,
    input_fps: float,
    output_fps: float,
    encoder: str,
    crf: int,
    preset: str,
    audio: bool,
    overwrite: bool,
    bit_depth: int = 8,
    hdr_output: bool = False,
) -> list[str]:
    input_pix_fmt = "rgb48le" if bit_depth > 8 else "rgb24"
    output_pix_fmt = "yuv420p10le" if bit_depth > 8 else "yuv420p"
    matrix = _output_matrix(info, hdr_output)

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y" if overwrite else "-n",
        "-f",
        "rawvideo",
        "-pix_fmt",
        input_pix_fmt,
        "-s",
        f"{info.display_width}x{info.display_height}",
        "-r",
        f"{input_fps:.6f}",
        *_raw_input_color_args(info, hdr_output),
        "-i",
        "pipe:0",
        "-i",
        str(info.path),
        "-map",
        "0:v:0",
    ]
    if audio and info.has_audio:
        cmd += ["-map", "1:a?", "-c:a", "copy"]
    cmd += _encoder_args(encoder, crf, preset, bit_depth, hdr_output)
    cmd += [
        "-r",
        f"{output_fps:.6f}",
        "-fps_mode",
        "cfr",
        "-vf",
        f"scale=out_color_matrix={matrix}:out_range=tv",
        "-pix_fmt",
        output_pix_fmt,
        *_output_color_args(info, hdr_output),
        "-map_metadata",
        "1",
        "-movflags",
        "+faststart",
    ]
    if output.suffix.lower() == ".mp4":
        cmd += ["-f", "mp4"]
    cmd.append(str(output))
    return cmd


def _preview_filter(
    info: MediaInfo,
    pane_width: int,
    pane_height: int,
    fps: float,
) -> str:
    """Normalize one input to a matching SDR preview pane."""
    pieces: list[str] = []
    if info.is_hdr:
        # Compare playback is deliberately SDR. The rendered file is untouched;
        # this is only a lightweight, reliably playable preview proxy.
        pieces.append(TONEMAP_TO_SDR)
    pieces.extend(
        [
            "setpts=PTS-STARTPTS",
            f"fps={fps:g}",
            (
                f"scale={pane_width}:{pane_height}:"
                "force_original_aspect_ratio=decrease:"
                "force_divisible_by=2:flags=lanczos"
            ),
            (
                f"pad={pane_width}:{pane_height}:"
                "(ow-iw)/2:(oh-ih)/2:color=black"
            ),
            "setsar=1",
            "format=yuv420p",
        ]
    )
    return ",".join(pieces)


def build_compare_preview_cmd(
    ffmpeg: str,
    source_info: MediaInfo,
    output_info: MediaInfo,
    destination: Path,
    pane_width: int = 960,
    pane_height: int = 540,
    fps: float = 60.0,
) -> list[str]:
    """Build a synchronized, side-by-side SDR playback proxy.

    One composite file is generated instead of opening two media players.
    That guarantees the original and interpolated frames stay on the same
    clock, and it avoids relying on the desktop media backend for 10-bit/HDR
    playback. ``destination`` is temporary until the caller atomically moves
    the finished file into its cache location.
    """
    left = _preview_filter(source_info, pane_width, pane_height, fps)
    right = _preview_filter(output_info, pane_width, pane_height, fps)
    filter_complex = (
        f"[0:v]{left}[left];"
        f"[1:v]{right}[right];"
        "[left][right]hstack=inputs=2,"
        # Tone-mapping does not necessarily remove HDR frame side data. The
        # preview is SDR only, so drop all frame side data before the H.264
        # encoder can forward HDR10/HDR10+/Dolby metadata into the container.
        # hstack is the last stage so it is also the best place to tag the
        # composite as BT.709.
        "sidedata=mode=delete,"
        "setsar=1,"
        "setparams=color_primaries=bt709:color_trc=bt709:"
        "colorspace=bt709:range=tv[v]"
    )
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source_info.path),
        "-i",
        str(output_info.path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "1:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        "-progress",
        "pipe:1",
        "-nostats",
        str(destination),
    ]
