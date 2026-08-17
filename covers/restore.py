"""Optional local portrait restoration with lazily loaded ONNX Runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from core.paths import models_dir

MODEL_FILES = {
    "gfpgan-1.4": "gfpgan-1.4.onnx",
    "realesrgan-x4": "realesrgan-x4.onnx",
    "yunet": "yunet.onnx",
}


def execution_providers(ort) -> list[str]:
    available = set(ort.get_available_providers())
    result = []
    if "CoreMLExecutionProvider" in available:
        result.append("CoreMLExecutionProvider")
    if "CPUExecutionProvider" in available:
        result.append("CPUExecutionProvider")
    return result or ["CPUExecutionProvider"]


def restore(
    image: np.ndarray,
    upscale: bool = True,
    model: str = "gfpgan-1.4",
    cancel: Callable[[], bool] | None = None,
    model_root: str | Path | None = None,
) -> np.ndarray:
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError(
            "Установите onnxruntime, чтобы включить реставрацию портретов"
        ) from exc
    root = Path(model_root) if model_root else models_dir() / "covers"
    stages = [model] + (["realesrgan-x4"] if upscale else [])
    result = image
    for stage in stages:
        if cancel and cancel():
            return result
        path = root / MODEL_FILES[stage]
        if not path.is_file():
            raise FileNotFoundError(f"Модель {path.name} не загружена")
        session = ort.InferenceSession(str(path), providers=execution_providers(ort))
        input_info = session.get_inputs()[0]
        array = result.astype(np.float32) / 255.0
        tensor = np.transpose(array[..., :3], (2, 0, 1))[None]
        output = session.run(None, {input_info.name: tensor})[0]
        if output.ndim == 4:
            output = output[0]
        if output.shape[0] in (1, 3, 4):
            output = np.transpose(output, (1, 2, 0))
        result = np.clip(output * (255.0 if output.max() <= 1.5 else 1), 0, 255).astype(
            np.uint8
        )
    return result
