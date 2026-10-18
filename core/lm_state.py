"""Put LM Studio back as we found it.

LM Studio loads a model just-in-time on the first request, and the model then
sits in memory (VRAM/RAM) until the user unloads it.  ``LmStateGuard`` records
which LLMs were loaded when the first job started and, once the last running
job is done, unloads the ones that were not loaded before.  Models the user
had loaded themselves are never touched.

Unloading goes through the ``lms`` CLI (LM Studio's REST API has no unload
endpoint in the OpenAI-compatible surface).  Everything is best-effort: a
missing CLI, an unreachable server or a failed unload is logged and ignored.
"""

import json
import os
import shutil
import subprocess
import threading
import urllib.request
from typing import Iterable, Optional

from core.logger import get_logger

logger = get_logger(__name__)

_LMS_FALLBACK = os.path.expanduser("~/.lmstudio/bin/lms")
_TIMEOUT_S = 5


def _models_url(base_url: str) -> str:
    return base_url.rstrip("/").removesuffix("/v1") + "/api/v0/models"


def loaded_llms(base_url: str, timeout: float = _TIMEOUT_S) -> Optional[frozenset[str]]:
    """Ids of the LLMs LM Studio holds in memory, or ``None`` if unknown.

    ``/v1/models`` lists every downloaded model under JIT loading, so the load
    state comes from LM Studio's own ``/api/v0/models``.
    """
    try:
        with urllib.request.urlopen(_models_url(base_url), timeout=timeout) as response:
            models = json.loads(response.read().decode("utf-8")).get("data", [])
    except Exception as exc:  # noqa: BLE001 - best-effort probe
        logger.debug("LM Studio load-state probe failed: %s", exc)
        return None
    return frozenset(
        m["id"] for m in models
        if m.get("state") == "loaded" and m.get("type") != "embeddings" and "id" in m
    )


def _lms_binary() -> Optional[str]:
    found = shutil.which("lms")
    if found:
        return found
    return _LMS_FALLBACK if os.access(_LMS_FALLBACK, os.X_OK) else None


def unload(model_ids: Iterable[str]) -> None:
    """Unload each model with ``lms unload``; never raises."""
    lms = _lms_binary()
    if lms is None:
        logger.info("lms CLI not found; leaving LM Studio models loaded")
        return
    for model_id in model_ids:
        try:
            subprocess.run(
                [lms, "unload", model_id],
                check=True, capture_output=True, timeout=30,
                stdin=subprocess.DEVNULL,
            )
            logger.info("Unloaded %s from LM Studio", model_id)
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            logger.warning("Could not unload %s from LM Studio: %s", model_id, exc)


class LmStateGuard:
    """Reference-counted snapshot/restore of LM Studio's loaded models.

    Jobs overlap (a recipe run plus a chat), so the snapshot is taken by the
    first ``acquire()`` and the restore happens on the last ``release()``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self._baseline: Optional[frozenset[str]] = None
        self._base_url = ""

    def acquire(self, base_url: str) -> None:
        with self._lock:
            self._active += 1
            if self._active == 1:
                self._base_url = base_url
                self._baseline = loaded_llms(base_url)

    def release(self) -> None:
        with self._lock:
            self._active -= 1
            if self._active > 0:
                return
            self._active = 0
            baseline, base_url = self._baseline, self._base_url
            self._baseline = None
        if baseline is None:
            return  # server was unreachable at the start: nothing to compare
        now = loaded_llms(base_url)
        extra = sorted(now - baseline) if now else []
        if extra:
            unload(extra)


_guard = LmStateGuard()


def get_guard() -> LmStateGuard:
    return _guard
