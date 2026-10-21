from __future__ import annotations

import argparse
import math
import subprocess
import sys
from pathlib import Path

from .ffmpeg import (
    HDR_ENCODERS,
    TEN_BIT_ENCODERS,
    build_decode_cmd,
    build_encode_cmd,
    find_tool,
)
from .pipeline import Job, run_job
from .probe import probe
from .rife import RifeModel

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = REPO_ROOT / "models" / "RIFE_fp32_timestep.onnx"
ANIME_MODEL = REPO_ROOT / "models" / "RIFE_4.9.onnx"
PROFILE_MODELS = {"live": DEFAULT_MODEL, "anime": ANIME_MODEL}
ENCODERS = ["libx264", "libx265", "h264_amf", "hevc_amf", "av1_amf"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gfi",
        description=(
            "GFI (Generate Frame Inbetween): pipelined video frame interpolation "
            "(RIFE + ONNX Runtime + FFmpeg)."
        ),
    )
    parser.add_argument("input", type=Path, help="input video")
    parser.add_argument("-o", "--output", type=Path, help="output video (default: <name>_<n>x.mp4)")
    parser.add_argument("-m", "--multiplier", type=int, default=2, help="interpolation factor (default: 2)")
    parser.add_argument("-t", "--target-fps", type=float, help="exact output fps on the target timeline")
    parser.add_argument(
        "--profile",
        choices=["live", "anime"],
        default="live",
        help="content profile: live action, or anime/cartoon (RIFE 4.9 + lower scene threshold)",
    )
    parser.add_argument(
        "--encoder",
        choices=ENCODERS,
        default=None,
        help="video encoder (default: libx264 for 8-bit, libx265 for 10-bit/HDR)",
    )
    parser.add_argument("--crf", type=int, default=16, help="quality (x264 CRF or AMF QP; default: 16)")
    parser.add_argument("--preset", default="veryfast", help="x264/x265 preset (default: veryfast)")
    parser.add_argument(
        "--scene-threshold",
        type=float,
        default=None,
        help="scene cut threshold; 0 disables (default: 0.25 live, 0.15 anime)",
    )
    parser.add_argument(
        "--bit-depth",
        choices=["auto", "8", "10"],
        default="auto",
        help="output bit depth (default: 10 for 10-bit/HDR sources, otherwise 8)",
    )
    parser.add_argument(
        "--hdr",
        choices=["preserve", "tonemap"],
        default="preserve",
        help="keep HDR10/HLG tags in a 10-bit output, or tone-map to SDR",
    )
    parser.add_argument("--no-audio", action="store_true", help="do not copy the audio track")
    parser.add_argument("--provider", default="DmlExecutionProvider", help="ONNX Runtime provider")
    parser.add_argument("--model", type=Path, default=None, help="override the RIFE ONNX model")
    parser.add_argument("--overwrite", action="store_true", help="overwrite the output")
    parser.add_argument("--quiet", action="store_true", help="hide progress")
    parser.add_argument("--dry-run", action="store_true", help="print the ffmpeg commands and exit")
    return parser


def _fmt_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _default_output(source: Path, multiplier: int, target_fps: float | None) -> Path:
    tag = f"{target_fps:g}fps" if target_fps else f"{multiplier}x"
    return source.with_name(f"{source.stem}_{tag}.mp4")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    args = _build_parser().parse_args(argv)

    source = args.input.expanduser().resolve()
    if not source.is_file():
        print(f"error: input not found: {source}", file=sys.stderr)
        return 2
    if args.multiplier < 1:
        print("error: --multiplier must be >= 1", file=sys.stderr)
        return 2

    ffmpeg = find_tool("ffmpeg")
    ffprobe = find_tool("ffprobe")
    try:
        info = probe(ffprobe, source)
    except Exception as exc:  # noqa: BLE001 - clean CLI error
        print(f"error: could not read input: {exc}", file=sys.stderr)
        return 1
    source_fps = float(info.fps)

    requested_depth = 0 if args.bit_depth == "auto" else int(args.bit_depth)
    output_bit_depth = requested_depth or (10 if (info.is_hdr or info.bit_depth > 8) else 8)
    hdr_output = bool(
        info.is_hdr and output_bit_depth > 8 and args.hdr == "preserve"
    )
    encoder = args.encoder or (
        "libx265" if (info.is_hdr or output_bit_depth > 8) else "libx264"
    )
    if info.is_hdr and args.hdr == "preserve" and not hdr_output:
        print(
            "note: 8-bit output cannot carry HDR; the input will be tone-mapped to SDR",
            file=sys.stderr,
        )
    if output_bit_depth > 8 and encoder not in TEN_BIT_ENCODERS:
        print(
            f"error: {encoder} cannot write 10-bit output; choose libx264, libx265, "
            "hevc_amf or av1_amf",
            file=sys.stderr,
        )
        return 2
    if hdr_output and encoder not in HDR_ENCODERS:
        print(
            "error: HDR output requires libx265, hevc_amf or av1_amf",
            file=sys.stderr,
        )
        return 2
    scene_threshold = (
        args.scene_threshold
        if args.scene_threshold is not None
        else (0.15 if args.profile == "anime" else 0.25)
    )
    model_path = (
        args.model.expanduser().resolve()
        if args.model is not None
        else PROFILE_MODELS[args.profile]
    )

    if args.target_fps is not None:
        if args.target_fps <= source_fps + 1e-6:
            print(
                f"error: --target-fps {args.target_fps:g} must be above the source rate {source_fps:g}",
                file=sys.stderr,
            )
            return 2
        multiplier = math.ceil(args.target_fps / source_fps - 1e-9)
        generated_fps = args.target_fps
        out_fps = args.target_fps
        target_fps = args.target_fps
    else:
        multiplier = args.multiplier
        generated_fps = source_fps * multiplier
        out_fps = generated_fps
        target_fps = 0.0
    needs_model = multiplier > 1 or target_fps > 0

    output = (args.output.expanduser().resolve() if args.output else _default_output(source, multiplier, args.target_fps))

    if args.dry_run:
        print(
            f"# profile={args.profile} model={model_path.name} "
            f"bit_depth={output_bit_depth} hdr={'preserve' if hdr_output else 'tonemap' if info.is_hdr else 'sdr'}"
        )
        print(
            subprocess.list2cmdline(
                build_decode_cmd(
                    ffmpeg,
                    info,
                    bit_depth=output_bit_depth,
                    hdr_output=hdr_output,
                )
            )
        )
        print(subprocess.list2cmdline(
            build_encode_cmd(
                ffmpeg,
                info,
                output,
                generated_fps,
                out_fps,
                encoder,
                args.crf,
                args.preset,
                not args.no_audio,
                args.overwrite,
                bit_depth=output_bit_depth,
                hdr_output=hdr_output,
            )
        ))
        return 0

    if output.exists() and not args.overwrite:
        print(f"error: output exists: {output} (use --overwrite)", file=sys.stderr)
        return 2
    if needs_model and not model_path.is_file():
        print(f"error: model not found: {model_path}", file=sys.stderr)
        print("hint: python scripts/fetch_model.py", file=sys.stderr)
        return 2

    job = Job(
        input=source,
        output=output,
        multiplier=multiplier,
        generated_fps=generated_fps,
        out_fps=out_fps,
        target_fps=target_fps,
        encoder=encoder,
        crf=args.crf,
        preset=args.preset,
        scene_threshold=scene_threshold,
        bit_depth=output_bit_depth,
        hdr_mode=args.hdr,
        audio=not args.no_audio,
        overwrite=args.overwrite,
        quiet=args.quiet,
    )

    if target_fps > 0:
        expected = max(1, round(info.duration * target_fps)) if info.duration > 0 else info.frame_count
    else:
        expected = info.frame_count * multiplier
    print(f"input : {source.name}  {info.display_width}x{info.display_height}  {source_fps:g} fps  {info.frame_count} frames")
    depth_text = f"{output_bit_depth}-bit"
    if info.is_hdr:
        depth_text += " HDR" if hdr_output else " SDR (tone-mapped)"
    print(
        f"output: {output.name}  {out_fps:g} fps  ~{expected} frames  "
        f"{encoder} crf={args.crf}  {depth_text}"
    )
    print(f"style : {args.profile}  ({model_path.name})")

    model = None
    if needs_model:
        try:
            model = RifeModel(model_path, provider=args.provider)
        except Exception as exc:  # noqa: BLE001 - clean CLI error
            print(f"error: could not load model: {exc}", file=sys.stderr)
            return 1
        print(f"model : {model_path.name} on {args.provider}")

    try:
        stats = run_job(job, model, info, ffmpeg)  # type: ignore[arg-type]
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - report a clean CLI error
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    print(
        f"done  {stats['width']}x{stats['height']}  {stats['output_frames']} frames  "
        f"{stats['seconds']:.1f}s  {stats['fps']:.1f} fps  {_fmt_size(stats['bytes'])}"
    )
    print(f"      {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
