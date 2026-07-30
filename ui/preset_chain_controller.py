"""
Whispered UI - Preset chain controller
State machine for the launch-bar preset chain (Phase C.3): after a fresh
transcription, automatically run whichever extra generation steps
("youtube", "article") the selected preset calls for, then tell the
caller once every step has settled so it can auto-save the results.

This used to be four instance attributes on MainWindow
(``_chain_pending``, ``_chain_ran``, ``_chain_had_error``,
``_chain_auto_article``) threaded through half a dozen otherwise-unrelated
methods. Pulling the state and its transitions out here makes the
machine itself testable in isolation, against the stubbed Qt in
tests/conftest.py, without constructing a MainWindow at all.

Deliberately has no widget references. MainWindow still owns every
side effect (starting workers, updating status text, saving files) —
it drives them from this class's return values and the ``finished``
signal, emitted once a chain (or the current step of one) has nothing
left pending.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class PresetChainController(QObject):
    """Tracks which chain steps are still running and reports when done.

    Signals
    -------
    finished(bool, object)
        ``(had_error, ran_steps)`` once every pending step has settled.
        ``ran_steps`` is the ``set[str]`` of steps that actually produced
        content worth auto-saving (a failed step is excluded).
    """

    finished = pyqtSignal(bool, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pending: set[str] = set()
        self._ran: set[str] = set()
        self._had_error = False
        self._auto_article = False

    def is_active(self) -> bool:
        """True while a chain is running or about to start its article
        step (clean-then-article isn't "pending" yet — text cleaning is a
        plain AI task the caller starts and watches on its own)."""
        return bool(self._pending or self._auto_article)

    @staticmethod
    def steps_for_preset(preset: str) -> set[str]:
        steps: set[str] = set()
        if preset in ("youtube", "full"):
            steps.add("youtube")
        if preset in ("article", "full"):
            steps.add("article")
        return steps

    def start(self, preset: str) -> set[str]:
        """Resolve ``preset`` to its steps and begin tracking them.

        Returns the resolved steps (empty for "transcribe_only", meaning
        the caller has nothing further to kick off). Does not itself
        start anything — the caller still owns starting
        ``youtube_panel.generate()`` / text cleaning for whichever steps
        come back.
        """
        steps = self.steps_for_preset(preset)
        if not steps:
            return steps
        self._pending = set(steps)
        self._ran = set(steps)
        self._had_error = False
        if "article" in steps:
            self._auto_article = True
        return steps

    def cancel(self) -> None:
        """Abort an in-progress chain (user hit Cancel)."""
        self._pending.clear()
        self._ran.clear()
        self._auto_article = False

    def on_youtube_done(self, success: bool) -> None:
        if "youtube" not in self._pending:
            return  # YouTube tab generated outside of an active chain
        self._pending.discard("youtube")
        if not success:
            self._had_error = True
            # A failed step produced no real content — its tabs hold error
            # text, which must not be auto-saved as an artifact nor counted
            # in the record's artifact chips.
            self._ran.discard("youtube")
        self._maybe_finish()

    def consume_auto_article(self) -> bool:
        """Called once AI text-cleaning finishes. Returns True if the
        chain should now continue straight into article generation."""
        if not self._auto_article:
            return False
        self._auto_article = False
        return True

    def on_generate_all_finished(self) -> None:
        if "article" in self._pending:
            self._pending.discard("article")
            self._maybe_finish()

    def on_ai_error(self) -> bool:
        """Called on any AI-worker error. Returns True if the error was in
        the chain's own article step — the caller must not also show the
        generic AI-error dialog for it."""
        self._auto_article = False
        if "article" in self._pending:
            self._pending.discard("article")
            self._had_error = True
            self._maybe_finish()
            return True
        return False

    def _maybe_finish(self) -> None:
        if self._pending:
            return
        ran, had_error = self._ran, self._had_error
        self._ran = set()
        self.finished.emit(had_error, ran)
