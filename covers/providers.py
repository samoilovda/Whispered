"""Local and ComfyUI image-provider factory."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImage

from core.paths import resource_path


@dataclass(frozen=True)
class ImageProviderSettings:
    kind: str
    base_url: str = ""
    workflow_path: str = ""
    api_key: str = ""


class ImageProvider(Protocol):
    def process(
        self, image: QImage, intent: str, cancel: Callable[[], bool]
    ) -> QImage: ...


class LocalProvider:
    def process(self, image: QImage, intent: str, cancel: Callable[[], bool]) -> QImage:
        if intent != "restore":
            raise ValueError("Локальный провайдер поддерживает только реставрацию")
        if cancel():
            return image
        # The ONNX numpy pipeline is invoked by RestoreWorker; this adapter keeps
        # the provider contract useful even when optional model weights are absent.
        return image


class HttpProvider:
    def process(self, image: QImage, intent: str, cancel: Callable[[], bool]) -> QImage:
        raise NotImplementedError("облачный провайдер не настроен")


class ComfyProvider:
    def __init__(
        self,
        settings: ImageProviderSettings,
        *,
        timeout: float = 300,
        poll_interval: float = 1,
        opener=None,
    ):
        self.base_url = settings.base_url.rstrip("/") or "http://127.0.0.1:8188"
        self.workflow_path = (
            Path(settings.workflow_path)
            if settings.workflow_path
            else resource_path("assets/covers/workflows/img2img.json")
        )
        self.timeout, self.poll_interval = timeout, poll_interval
        self._opener = opener or urllib.request.urlopen

    def _request(
        self,
        path: str,
        data: bytes | None = None,
        content_type: str = "application/json",
    ) -> bytes:
        request = urllib.request.Request(self.base_url + path, data=data)
        if data is not None:
            request.add_header("Content-Type", content_type)
        try:
            with self._opener(request, timeout=30) as response:
                return response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(
                f"ComfyUI не отвечает на {self.base_url}. Запустите ComfyUI или переключитесь на локальную реставрацию"
            ) from exc

    def process(self, image: QImage, intent: str, cancel: Callable[[], bool]) -> QImage:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        boundary = f"whispered-{random.randrange(10**12):012d}"
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="cover-input.png"\r\n'
                "Content-Type: image/png\r\n\r\n"
            ).encode()
            + bytes(buffer.data())
            + f"\r\n--{boundary}--\r\n".encode()
        )
        uploaded = json.loads(
            self._request(
                "/upload/image", body, f"multipart/form-data; boundary={boundary}"
            )
        )
        workflow = self.workflow_path.read_text(encoding="utf-8")
        workflow = workflow.replace("%IMAGE%", uploaded.get("name", "cover-input.png"))
        workflow = workflow.replace(
            "%PROMPT%",
            "restore portrait" if intent == "restore" else "subtle editorial portrait",
        )
        workflow = workflow.replace("%SEED%", str(random.randrange(2**31))).replace(
            "%DENOISE%", "0.35"
        )
        queued = json.loads(
            self._request(
                "/prompt", json.dumps({"prompt": json.loads(workflow)}).encode()
            )
        )
        prompt_id = queued["prompt_id"]
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if cancel():
                self._request("/interrupt", b"{}")
                return image
            history = json.loads(self._request(f"/history/{prompt_id}"))
            record = history.get(prompt_id)
            if record:
                for output in record.get("outputs", {}).values():
                    pictures = output.get("images", [])
                    if pictures:
                        item = pictures[0]
                        query = urllib.parse.urlencode(
                            {
                                key: item.get(key, "")
                                for key in ("filename", "subfolder", "type")
                            }
                        )
                        result = QImage()
                        result.loadFromData(self._request(f"/view?{query}"))
                        return result
            time.sleep(self.poll_interval)
        raise TimeoutError("ComfyUI не завершил обработку за 300 секунд")


def create_image_provider(settings: ImageProviderSettings) -> ImageProvider:
    if settings.kind == "local":
        return LocalProvider()
    if settings.kind == "comfyui":
        return ComfyProvider(settings)
    if settings.kind == "http":
        return HttpProvider()
    raise ValueError(f"Неизвестный провайдер изображений: {settings.kind}")
