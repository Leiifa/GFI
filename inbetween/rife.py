from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort


class RifeModel:
    """Thin wrapper around a RIFE ONNX graph.

    Supports the two common export layouts:

    * stacked: ``input`` [1, 6, H, W] + scalar ``timestep``
    * split:   ``img0``, ``img1``, optional ``timestep``
    """

    def __init__(self, model_path: Path, provider: str = "DmlExecutionProvider") -> None:
        model_path = Path(model_path)
        if not model_path.is_file():
            raise FileNotFoundError(f"model not found: {model_path}")

        available = ort.get_available_providers()
        if provider not in available:
            raise RuntimeError(
                f"{provider} is not available (have: {', '.join(available)}). "
                "Install onnxruntime-directml or use --provider CPUExecutionProvider."
            )

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3

        self.provider = provider
        self.session = ort.InferenceSession(str(model_path), sess_options=options, providers=[provider])

        inputs = self.session.get_inputs()
        self._inputs = {node.name: node for node in inputs}
        self._timestep_name = next((n for n in ("timestep", "t") if n in self._inputs), None)

        image_inputs = [n for n in inputs if n.name != self._timestep_name]
        if len(image_inputs) == 1:
            channels = self._shape_at(image_inputs[0].shape, 1)
            if channels not in (None, 6):
                raise RuntimeError(
                    f"single image input has {channels} channels; expected a 6-channel stack"
                )
            self.mode = "stacked"
            self._image_name = image_inputs[0].name
        elif len(image_inputs) == 2:
            self.mode = "split"
            self._image_names = (image_inputs[0].name, image_inputs[1].name)
        else:
            names = ", ".join(f"{n.name}{n.shape}" for n in inputs)
            raise RuntimeError(f"unsupported RIFE graph inputs: {names}")

        self._stack: np.ndarray | None = None
        self._grid: np.ndarray | None = None
        self._flow_div: np.ndarray | None = None
        self._grid_shape: tuple[int, int] | None = None

        # Some older exports bake the timestep in; warn instead of silently
        # producing every intermediate at the midpoint.
        if self._timestep_name is None:
            print("warning: model has no timestep input; only 2x (t=0.5) is supported", file=sys.stderr)

    @staticmethod
    def _shape_at(shape: list, index: int) -> int | None:
        if index >= len(shape):
            return None
        value = shape[index]
        return value if isinstance(value, int) else None

    def prepare(self, frame: np.ndarray) -> np.ndarray:
        """HWC uint8/uint16 -> [1, 3, H, W] float32 in [0, 1]."""
        height, width = frame.shape[:2]
        scale = 65535.0 if frame.dtype == np.uint16 else 255.0
        planar = np.empty((1, 3, height, width), dtype=np.float32)
        np.multiply(frame.transpose(2, 0, 1), 1.0 / scale, out=planar[0], dtype=np.float32)
        return planar

    def _extra_feeds(self, height: int, width: int) -> dict[str, np.ndarray]:
        feeds: dict[str, np.ndarray] = {}
        if "tenFlow_div" in self._inputs:
            if self._flow_div is None:
                self._flow_div = np.array(
                    [2.0 / max(width - 1, 1), 2.0 / max(height - 1, 1)], dtype=np.float32
                )
            feeds["tenFlow_div"] = self._flow_div
        if "backwarp_tenGrid" in self._inputs:
            if self._grid_shape != (height, width):
                ys, xs = np.meshgrid(
                    np.linspace(-1.0, 1.0, height, dtype=np.float32),
                    np.linspace(-1.0, 1.0, width, dtype=np.float32),
                    indexing="ij",
                )
                self._grid = np.stack((xs, ys))[None]
                self._grid_shape = (height, width)
            feeds["backwarp_tenGrid"] = self._grid
        return feeds

    def _timestep_feed(self, timestep: float, height: int, width: int) -> dict[str, np.ndarray]:
        if self._timestep_name is None:
            return {}
        shape = self._inputs[self._timestep_name].shape
        if len(shape) == 0:
            value = np.array(timestep, dtype=np.float32)
        else:
            dims = tuple(1 if d is None or isinstance(d, str) else int(d) for d in shape)
            value = np.full(dims, timestep, dtype=np.float32)
        return {self._timestep_name: value}

    def infer(
        self,
        frame0: np.ndarray,
        frame1: np.ndarray,
        timestep: float,
        out_dtype: type[np.generic] = np.uint8,
    ) -> np.ndarray:
        """Interpolate two prepared frames; returns HWC uint8 or uint16."""
        height, width = frame0.shape[2], frame0.shape[3]
        feeds = self._extra_feeds(height, width)
        feeds.update(self._timestep_feed(timestep, height, width))

        if self.mode == "stacked":
            if (
                self._stack is None
                or self._stack.shape[2] != height
                or self._stack.shape[3] != width
            ):
                self._stack = np.empty((1, 6, height, width), dtype=np.float32)
            np.copyto(self._stack[0, :3], frame0[0])
            np.copyto(self._stack[0, 3:], frame1[0])
            feeds[self._image_name] = self._stack
        else:
            feeds[self._image_names[0]] = frame0
            feeds[self._image_names[1]] = frame1

        output = self.session.run(None, feeds)[0]
        result = output[0]

        if result.shape[1] != height or result.shape[2] != width:
            result = result[:, :height, :width]

        np.clip(result, 0.0, 1.0, out=result)
        scale = 65535.0 if out_dtype == np.uint16 else 255.0
        result *= scale
        np.rint(result, out=result)
        return np.ascontiguousarray(result.astype(out_dtype).transpose(1, 2, 0))
