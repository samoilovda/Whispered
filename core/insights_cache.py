"""Content-addressed cache for InsightsWorker results.

See docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R8: YouTubePanel and
InsightsPanel each spawn their own InsightsWorker for "chapters"
independently, with no way to know the other panel already computed the
exact same thing from the exact same transcript — so it was silently
recomputed via a second LLM round-trip. An InsightsCache instance, shared
by MainWindow across the panels that construct InsightsWorker, closes that
gap: the key is the exact prompt text that would be sent (already encodes
insight_type/segments/language/transcript truncation), so two requests
only collide when they'd genuinely produce the same request.

Deliberately in-memory and instance-scoped, not a module-level global and
not persisted via infrastructure.persistence.artifact_store: the problem
this fixes is redundant computation *within one running session* holding
the same transcript open, which doesn't need cross-restart persistence or
its staleness questions (was the transcript edited since, did the model
change). A caller that wants disk-backed caching across restarts should
build one on Artifact/artifact_store instead — this class intentionally
stays small, and instance-scoping (rather than a module-level dict) keeps
it out of the way of test isolation, since nothing here is process-global.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any, Optional


class InsightsCache:
    """Thread-safe in-memory cache. One instance is meant to be shared by
    every panel that constructs InsightsWorker for the same open
    transcript (see ui/main_window.py)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, Any] = {}

    @staticmethod
    def key(insight_type: str, prompt: str, provider_id: str) -> str:
        """Cache key derived from exactly what determines the LLM's
        output: which insight type, the fully-built prompt, and which
        provider would answer it."""
        return hashlib.sha256(
            f"{insight_type}\x00{provider_id}\x00{prompt}".encode("utf-8")
        ).hexdigest()

    def get(self, cache_key: str) -> Optional[Any]:
        with self._lock:
            return self._entries.get(cache_key)

    def put(self, cache_key: str, result: Any) -> None:
        with self._lock:
            self._entries[cache_key] = result

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
