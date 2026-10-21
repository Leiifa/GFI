from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .ffmpeg import (
    CREATE_NO_WINDOW,
    HDR_ENCODERS,
    TEN_BIT_ENCODERS,
    build_decode_cmd,
    build_encode_cmd,
)
from .probe import MediaInfo
from .rife import RifeModel


@dataclass
class Job:
    input: Path
    output: Path
    multiplier: int = 2
    generated_fps: float = 0.0
    out_fps: float = 0.0
    target_fps: float = 0.0
    encoder: str = "libx264"
    crf: int = 16
    preset: str = "veryfast"
    scene_threshold: float = 0.25
    bit_depth: int = 0  # 0 = auto (10-bit for 10-bit/HDR sources)
    hdr_mode: str = "preserve"  # preserve or tonemap
    audio: bool = True
    overwrite: bool = False
    quiet: bool = False
    progress: Callable[[int, int, int, float], None] | None = None
    log: Callable[[str], None] | None = None
    cancel: threading.Event | None = None


class CancelledError(Exception):
    """Raised when the caller cancels an in-flight job."""


class _StderrLog:
    def __init__(self, stream) -> None:
        self._lines: deque[str] = deque(maxlen=80)
        self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def _pump(self, stream) -> None:
        try:
            for raw in iter(stream.readline, b""):
                text = raw.decode("utf-8", "replace").rstrip()
                if text:
                    self._lines.append(text)
        except (ValueError, OSError):
            pass

    def tail(self) -> str:
        self._thread.join(timeout=1.0)
        return "\n".join(self._lines)


def _read_exact(stream, size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return chunks[0] if len(chunks) == 1 else b"".join(chunks)


def _put(q: queue.Queue, item, stop: threading.Event) -> bool:
    while not stop.is_set():
        try:
            q.put(item, timeout=0.25)
            return True
        except queue.Full:
            continue
    return False


def _get(q: queue.Queue, stop: threading.Event):
    while not stop.is_set():
        try:
            return q.get(timeout=0.25)
        except queue.Empty:
            continue
    return None


def _reader(
    proc,
    width: int,
    height: int,
    q: queue.Queue,
    stop: threading.Event,
    errors: list,
    channel_bytes: int = 1,
) -> None:
    frame_size = width * height * 3 * channel_bytes
    dtype = np.uint16 if channel_bytes == 2 else np.uint8
    try:
        while not stop.is_set():
            data = _read_exact(proc.stdout, frame_size)
            if data is None:
                break
            frame = np.frombuffer(data, dtype).reshape(height, width, 3)
            if not _put(q, frame, stop):
                return
    except (OSError, ValueError) as exc:
        errors.append(exc)
        stop.set()
    finally:
        _put(q, None, stop)


def _writer(proc, q: queue.Queue, stop: threading.Event, errors: list) -> None:
    try:
        while True:
            item = q.get()
            if item is None:
                break
            proc.stdin.write(np.ascontiguousarray(item).tobytes())
    except (BrokenPipeError, OSError, ValueError) as exc:
        errors.append(exc)
        stop.set()
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


def _is_scene_cut(prev: np.ndarray, cur: np.ndarray, threshold: float) -> bool:
    if threshold <= 0:
        return False
    sample = (slice(None, None, 8), slice(None, None, 8))
    a = prev[sample].astype(np.int32)
    b = cur[sample].astype(np.int32)
    scale = 65535.0 if prev.dtype == np.uint16 else 255.0
    return float(np.abs(a - b).mean()) / scale > threshold


def _format_eta(seconds: float) -> str:
    if not np.isfinite(seconds) or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _report(done: int, total: int, produced: int, elapsed: float) -> None:
    elapsed = max(elapsed, 1e-6)
    rate = produced / elapsed
    fraction = min(done / max(total, 1), 1.0)
    eta = (total - done) / (done / elapsed) if done else float("inf")
    sys.stderr.write(
        f"\r  {done}/{total} in  {produced} out  "
        f"{fraction * 100:5.1f}%  {rate:5.1f} fps  ETA {_format_eta(eta)}   "
    )
    sys.stderr.flush()


def _emit_progress(job: Job, done: int, total: int, produced: int, started: float) -> None:
    elapsed = max(time.monotonic() - started, 1e-6)
    if job.progress is not None:
        job.progress(done, total, produced, elapsed)
    elif not job.quiet:
        _report(done, total, produced, elapsed)


def _emit_log(job: Job, message: str) -> None:
    if job.log is not None:
        job.log(message)
    else:
        print(message, file=sys.stderr)


def _check_cancel(job: Job) -> None:
    if job.cancel is not None and job.cancel.is_set():
        raise CancelledError()


def run_job(job: Job, model: RifeModel, info: MediaInfo, ffmpeg: str) -> dict:
    target_mode = job.target_fps > 0
    source_fps = float(info.fps)
    if source_fps <= 0:
        raise RuntimeError("source has no usable frame rate")
    if target_mode and job.target_fps <= source_fps + 1e-6:
        raise RuntimeError(
            f"target fps {job.target_fps:g} must be above the source rate {source_fps:g}"
        )

    if target_mode and model is None:
        raise RuntimeError("the interpolation model is required for target-rate output")
    if not target_mode and job.multiplier > 1 and model is None:
        raise RuntimeError("the interpolation model is required for multiplier > 1")

    if info.rotation % 360 != 0:
        _emit_log(
            job,
            f"note: input has {info.rotation} degrees of display rotation; "
            "output dimensions assume ffmpeg autorotates",
        )

    if job.bit_depth not in (0, 8, 10):
        raise RuntimeError("bit depth must be auto, 8 or 10")
    hdr_mode = job.hdr_mode.lower()
    if hdr_mode not in {"preserve", "tonemap"}:
        raise RuntimeError("HDR mode must be preserve or tonemap")

    auto_bit_depth = 10 if (info.bit_depth > 8 or info.is_hdr) else 8
    output_bit_depth = job.bit_depth or auto_bit_depth
    hdr_output = bool(
        info.is_hdr and output_bit_depth > 8 and hdr_mode == "preserve"
    )

    if output_bit_depth > 8 and job.encoder not in TEN_BIT_ENCODERS:
        raise RuntimeError(
            f"{job.encoder} cannot write 10-bit video; choose libx264, libx265, "
            "hevc_amf or av1_amf"
        )
    if hdr_output and job.encoder not in HDR_ENCODERS:
        raise RuntimeError(
            "HDR output requires libx265, hevc_amf or av1_amf"
        )

    if info.is_hdr:
        if hdr_output:
            _emit_log(
                job,
                f"HDR input preserved ({info.hdr_kind or 'HDR'}, 10-bit); "
                "output color tags are copied",
            )
        else:
            _emit_log(
                job,
                "HDR input will be tone-mapped to SDR BT.709"
                + (" at 10-bit" if output_bit_depth > 8 else " at 8-bit"),
            )
    elif info.bit_depth > 8:
        _emit_log(
            job,
            f"{info.bit_depth}-bit input will stay {output_bit_depth}-bit "
            f"({info.pix_fmt} -> yuv420p10le)",
        )
    if info.is_vfr:
        _emit_log(
            job,
            f"warning: source looks VFR ({float(info.fps):g} avg / "
            f"{float(info.real_fps):g} real fps); decoder normalizes to CFR",
        )

    decode_cmd = build_decode_cmd(
        ffmpeg,
        info,
        bit_depth=output_bit_depth,
        hdr_output=hdr_output,
    )
    encode_input_fps = job.target_fps if target_mode else job.generated_fps
    encode_cmd = build_encode_cmd(
        ffmpeg,
        info,
        job.output,
        encode_input_fps,
        job.out_fps,
        job.encoder,
        job.crf,
        job.preset,
        job.audio,
        job.overwrite,
        bit_depth=output_bit_depth,
        hdr_output=hdr_output,
    )
    channel_bytes = 2 if output_bit_depth > 8 else 1

    job.output.parent.mkdir(parents=True, exist_ok=True)

    decoder = subprocess.Popen(
        decode_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        creationflags=CREATE_NO_WINDOW,
    )
    try:
        encoder = subprocess.Popen(
            encode_cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=CREATE_NO_WINDOW,
        )
    except BaseException:
        decoder.terminate()
        raise
    dec_log = _StderrLog(decoder.stderr)
    enc_log = _StderrLog(encoder.stderr)

    stop = threading.Event()
    errors: list[BaseException] = []
    reader_q: queue.Queue = queue.Queue(maxsize=4)
    writer_q: queue.Queue = queue.Queue(maxsize=8)

    reader = threading.Thread(
        target=_reader,
        args=(
            decoder,
            info.display_width,
            info.display_height,
            reader_q,
            stop,
            errors,
            channel_bytes,
        ),
        daemon=True,
    )
    writer = threading.Thread(
        target=_writer,
        args=(encoder, writer_q, stop, errors),
        daemon=True,
    )

    width, height = info.display_width, info.display_height
    total_in = max(info.frame_count, 1)
    playback_duration = info.duration if info.duration > 0 else total_in / source_fps
    target_fps = float(job.target_fps)
    expected_output = (
        max(1, int(round(playback_duration * target_fps))) if target_mode else 0
    )
    produced = 0
    consumed = 0
    scene_cuts = 0
    out_dtype = np.uint16 if output_bit_depth > 8 else np.uint8
    started = time.monotonic()
    last_report = 0.0

    try:
        reader.start()
        writer.start()

        first = _get(reader_q, stop)
        _check_cancel(job)
        if first is None:
            raise RuntimeError("decoder produced no frames")

        previous = first
        previous_planar = model.prepare(previous) if model is not None else None

        if not _put(writer_q, previous, stop):
            raise RuntimeError("encoder stopped accepting frames")
        produced += 1
        consumed = 1
        target_index = 1 if target_mode else 0
        previous_time = 0.0
        _emit_progress(job, consumed, total_in, produced, started)

        while True:
            current = _get(reader_q, stop)
            if current is None:
                _check_cancel(job)
                break

            consumed += 1
            current_planar = model.prepare(current) if model is not None else None

            current_time = (consumed - 1) / source_fps
            cut = _is_scene_cut(previous, current, job.scene_threshold)
            if cut:
                scene_cuts += 1

            if target_mode:
                # Emit exactly the frames whose timestamps fall on the target
                # timeline. This avoids generating an integer multiple and then
                # resampling (which makes 24 -> 60 motion uneven).
                while (
                    target_index < expected_output
                    and target_index / target_fps < current_time - 1e-7
                ):
                    _check_cancel(job)
                    if cut:
                        # Hold the outgoing shot rather than morphing across a cut.
                        frame = previous
                    else:
                        timestep = (
                            (target_index / target_fps - previous_time)
                            / (current_time - previous_time)
                        )
                        timestep = min(max(timestep, 0.0), 1.0)
                        frame = model.infer(
                            previous_planar,
                            current_planar,
                            timestep,
                            out_dtype=out_dtype,
                        )
                    if not _put(writer_q, frame, stop):
                        raise RuntimeError("encoder stopped accepting frames")
                    produced += 1
                    target_index += 1

                if (
                    target_index < expected_output
                    and abs(target_index / target_fps - current_time) <= 1e-7
                ):
                    if not _put(writer_q, current, stop):
                        raise RuntimeError("encoder stopped accepting frames")
                    produced += 1
                    target_index += 1
            else:
                if not cut:
                    for index in range(1, job.multiplier):
                        _check_cancel(job)
                        timestep = index / job.multiplier
                        intermediate = model.infer(
                            previous_planar,
                            current_planar,
                            timestep,
                            out_dtype=out_dtype,
                        )
                        if not _put(writer_q, intermediate, stop):
                            raise RuntimeError("encoder stopped accepting frames")
                        produced += 1

                if not _put(writer_q, current, stop):
                    raise RuntimeError("encoder stopped accepting frames")
                produced += 1

            previous = current
            previous_planar = current_planar
            previous_time = current_time

            now = time.monotonic()
            if now - last_report > 0.35:
                _emit_progress(job, consumed, total_in, produced, started)
                last_report = now

        if target_mode:
            # The source's final frame is displayed for one source-frame
            # interval. Fill that tail on the target timeline by holding it.
            while target_index < expected_output:
                target_time = target_index / target_fps
                if target_time >= playback_duration - 1e-9:
                    break
                _check_cancel(job)
                if not _put(writer_q, previous, stop):
                    raise RuntimeError("encoder stopped accepting frames")
                produced += 1
                target_index += 1

        if scene_cuts:
            _emit_log(job, f"scene cuts detected: {scene_cuts} (interpolation skipped)")

        _put(writer_q, None, stop)
        _emit_progress(job, total_in, total_in, produced, started)
    except BaseException:
        stop.set()
        for proc in (decoder, encoder):
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
        for proc in (decoder, encoder):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        job.output.unlink(missing_ok=True)
        raise
    finally:
        stop.set()
        reader.join(timeout=5)
        writer.join(timeout=30)

    if job.progress is None and not job.quiet:
        sys.stderr.write("\n")

    try:
        decoder_rc = decoder.wait(timeout=10)
        encoder_rc = encoder.wait(timeout=30)
    except subprocess.TimeoutExpired as exc:
        for proc in (decoder, encoder):
            proc.kill()
        job.output.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg did not exit cleanly") from exc

    if errors:
        job.output.unlink(missing_ok=True)
        raise RuntimeError(f"pipeline error: {errors[0]}")

    if decoder_rc != 0:
        raise RuntimeError(f"ffmpeg decode failed ({decoder_rc}):\n{dec_log.tail()}")
    if encoder_rc != 0:
        job.output.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg encode failed ({encoder_rc}):\n{enc_log.tail()}")

    elapsed = time.monotonic() - started
    size = job.output.stat().st_size if job.output.exists() else 0
    return {
        "input_frames": consumed,
        "output_frames": produced,
        "seconds": elapsed,
        "fps": produced / elapsed if elapsed else 0.0,
        "bytes": size,
        "width": width,
        "height": height,
        "out_fps": float(job.out_fps),
        "bit_depth": output_bit_depth,
        "hdr_output": hdr_output,
        "scene_cuts": scene_cuts,
    }
