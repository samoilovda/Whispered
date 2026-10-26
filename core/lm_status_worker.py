"""
Whispered – LM Studio status worker
One shared, non-blocking "is the server up and what is loaded?" probe.

Three call sites used to answer this question independently: the settings
dialog hand-rolled its own ``urllib`` request against ``/models`` (missing
the auth headers and the client's serialization lock), the Book panel
wrapped ``BookPipeline.is_available()``, and the AI panel routed it through
its CLI worker.  They drifted: a change to timeouts or headers in
``core/lm_client.py`` did not reach the dialog.  Everything now goes
through :class:`LMStudioClient.probe`.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal

from core.base_worker import BaseWorker
from core.logger import get_logger

logger = get_logger(__name__)


class LMStatusWorker(BaseWorker):
    """Probe one LM Studio endpoint off the GUI thread.

    Signals
    -------
    status_ready(bool, str)
        ``(connected, model_name)`` on success — ``model_name`` is empty when
        the server is up with no model loaded — or ``(False, error_message)``.
    """

    status_ready = pyqtSignal(bool, str)

    def __init__(self, url: str, timeout: float = 5.0, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._timeout = timeout

    def _on_error(self, msg: str) -> None:
        self.status_ready.emit(False, msg)

    def _execute(self) -> None:
        from core.lm_client import LMStudioClient

        connected, detail = LMStudioClient(self._url).probe(timeout=self._timeout)
        if connected:
            detail = self._loaded_model(detail)
        if self.is_cancelled():
            return
        self.status_ready.emit(connected, detail)

    def _loaded_model(self, listed: str) -> str:
        """The model LM Studio actually holds in memory. Under JIT loading
        ``/v1/models`` lists every downloaded model, so *listed* (its first
        entry) is not necessarily loaded; LM Studio's own ``/api/v0/models``
        knows. Another OpenAI-compatible server has no such endpoint —
        then *listed* stays the best answer."""
        from core.lm_state import loaded_llms

        loaded = loaded_llms(self._url, timeout=self._timeout)
        if loaded is None:
            return listed
        return min(loaded) if loaded else ""
