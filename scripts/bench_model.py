"""Quick RIFE ONNX benchmark across execution providers and resolutions."""

import argparse
import time

import numpy as np
import onnxruntime as ort


def make_session(model: str, provider: str, width: int, height: int) -> ort.InferenceSession:
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.log_severity_level = 3
    # DirectML handles the free dimensions itself; the Python API has no
    # AddFreeDimensionOverride binding, so leave the graph dynamic.
    return ort.InferenceSession(model, sess_options=so, providers=[provider])


def bench(sess: ort.InferenceSession, width: int, height: int, iters: int) -> float:
    rng = np.random.default_rng(0)
    x = rng.random((1, 6, height, width), dtype=np.float32)
    t = np.array(0.5, dtype=np.float32)
    feeds = {"input": x, "timestep": t}

    for _ in range(2):  # warmup
        sess.run(None, feeds)

    start = time.perf_counter()
    for _ in range(iters):
        sess.run(None, feeds)
    elapsed = time.perf_counter() - start
    return elapsed / iters


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/RIFE_fp32_timestep.onnx")
    ap.add_argument("--providers", default="DmlExecutionProvider,CPUExecutionProvider")
    ap.add_argument("--sizes", default="1280x720,1920x1080,2560x1440,3840x2160")
    ap.add_argument("--iters", type=int, default=5)
    args = ap.parse_args()

    for provider in args.providers.split(","):
        for size in args.sizes.split(","):
            width, height = (int(v) for v in size.lower().split("x"))
            try:
                t0 = time.perf_counter()
                sess = make_session(args.model, provider, width, height)
                load = time.perf_counter() - t0
                per_frame = bench(sess, width, height, args.iters)
                print(
                    f"{provider:24s} {width}x{height:<5d} "
                    f"load={load:5.2f}s  {per_frame * 1000:7.1f} ms/frame  "
                    f"{1 / per_frame:6.1f} fps  "
                    f"({per_frame / (width * height) * 1e6:.3f} us/Mpx)"
                )
            except Exception as exc:  # noqa: BLE001 - benchmark wants to continue
                print(f"{provider:24s} {width}x{height:<5d} FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
