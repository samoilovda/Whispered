"""
Whispered - Main Window
Main application window with compact header-bar layout and AI processing
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout,
    QLabel, QFileDialog, QMessageBox, QDialog,
    QApplication, QTabWidget,
    QTextEdit, QLineEdit, QPlainTextEdit, QStackedWidget,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut, QDragEnterEvent, QDropEvent

from ui.toast import show_toast
from ui.option_labels import recipe_label
from ui.library_view import LibraryView
from ui.record_view import RecordView
from ui.workspace_shell import WorkspaceShell
from ui.start_view import StartView
from ui.status_bar import StatusBar
from ui.transcribe_options import TranscribeOptions
from ui.command_palette import CommandPalette
from ui.file_selector import FileSelector
from ui.transcript_view import TranscriptView
from ui.article_view import ArticleView, CleanedTextView
from ui.batch_panel import BatchPanel
from ui.course_capture_panel import CourseCapturePanel
from ui.book_panel import BookPanel
from ui.cut_view import CutView
from ui.player_widget import PlayerWidget
from ui.recorder_widget import RecorderWidget
from ui.chat_panel import ChatPanel
from ui.insights_panel import InsightsPanel
from ui.youtube_panel import YouTubePanel
from ui.cover_view import CoverView
from ui.live_view import LiveView
from ui.live_preflight_panel import LivePreflightWorker
from ui.progress_timeline import ProgressTimeline
from ui.animated_button import AnimatedButton
from ui.live_checkpoint_tracker import LiveCheckpointTracker
from ui.shutdownable import Shutdownable
from ui.run_view import RunView
from ui.recipe_editor import RecipeEditorDialog
from transcriber import Transcriber, TranscriptionResult
from exporters import EXPORT_FORMATS
from application import export_controller
from utils import (
    WHISPER_MODELS,
    WHISPER_LANGUAGES,
    PERFORMANCE_MODES,
    detect_gpu,
    get_thread_count,
    is_supported_format,
)
from application.document_session import DocumentSession
from application.job_engine import JobRun
from application.steps import (
    STEP_DEFINITIONS,
    StepContext,
    build_cache_checks,
    build_job_spec,
    build_runners,
    load_step_result,
    manifest_path_for_step,
)
from domain.job import JobSpec, StepOutcome, StepStatus
from domain.recipe import BUILTIN_RECIPES_BY_KEY, Recipe, TRANSCRIPT_ONLY
from config import get_config, save_config
from core.insights_cache import InsightsCache
from core.base_worker import BaseWorker
from core.job_runner import JobRunner
from core.logger import get_logger
from core.i18n import tr
from core.worker_registry import WorkerRegistry
from transcriber import _build_initial_prompt
from timeline_export import write_edl
from video_edit import mark_pauses
from video_cut import assemble_draft
from core.live.preflight import default_helper_path
from core.live.contracts import SegmentState
from core.live.runtime import LiveRuntime
from ui.transcription_progress import (
    format_eta,
    localized_progress,
    timeline_stage_for_progress,
)

logger = get_logger(__name__)


# ============================================================================
# MAIN WINDOW
# ============================================================================


class GPUDetectionWorker(BaseWorker):
    """Detect hardware without delaying construction of the main window."""

    detected = pyqtSignal(str, str)

    def _on_error(self, msg: str) -> None:
        # No error signal exists — hardware detection failing just means
        # the device badge keeps whatever it already showed. BaseWorker's
        # run() has already logged the exception; previously an exception
        # here crashed the thread with no log entry at all.
        pass

    def _execute(self) -> None:
        gpu_type, gpu_name = detect_gpu(cancel_event=self._cancelled)
        if not self.is_cancelled():
            self.detected.emit(gpu_type, gpu_name)


class DraftAssemblyWorker(BaseWorker):
    """Run the potentially long FFmpeg draft assembly away from the UI thread."""

    progress = pyqtSignal(str)
    assembled = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, source_path: str, segments, output_path: str, parent=None) -> None:
        super().__init__(parent)
        self._source_path = source_path
        self._segments = list(segments)
        self._output_path = output_path

    def _on_error(self, msg: str) -> None:
        self.error.emit(msg)

    def _execute(self) -> None:
        assemble_draft(
            self._source_path,
            self._segments,
            self._output_path,
            on_progress=self.progress.emit,
            should_cancel=self.is_cancelled,
        )
        self.assembled.emit(self._output_path)


class _WorkerShutdown:
    """Adapts an ``Optional[QThread]`` attribute to the Shutdownable
    protocol (ui/shutdownable.py).

    ``_recipe_job``, ``_gpu_worker`` and ``_live_preflight_worker`` (along
    with the five single-step ``_*_job`` attributes) are not persistent
    objects like the panels — they are created per-operation, set back to
    ``None`` when idle, and sometimes replaced while running.
    A registration built once at construction time therefore looks the
    attribute up by name at shutdown time rather than holding the worker
    itself, so it always sees whatever is current when the window closes.
    """

    def __init__(
        self,
        owner: MainWindow,
        attr: str,
        wait_ms: int = 5000,
    ) -> None:
        self._owner = owner
        self._attr = attr
        self._wait_ms = wait_ms

    def shutdown(self) -> None:
        worker = getattr(self._owner, self._attr)
        if worker is None or not worker.isRunning():
            return
        worker.cancel()
        if not worker.wait(self._wait_ms):
            # The bounded wait is a UX budget, not a correctness guarantee.
            # A worker that outlives it must not be abandoned — dropping
            # the last reference to (or later destroying) a still-running
            # QThread is what Qt aborts the process for. WorkerRegistry
            # keeps it alive and deletes it once it actually finishes.
            logger.warning(
                "%s did not stop within %d ms; deferring cleanup to WorkerRegistry",
                self._attr, self._wait_ms,
            )
            self._owner._registry.register(worker, name=self._attr)
            self._owner._registry.retire(worker)


def _result_from_payload(payload: dict) -> TranscriptionResult:
    """Rebuild a TranscriptionResult from a history/transcript_revision
    JSON payload dict — the same segment/word reconstruction
    ``_load_from_history`` uses, factored out so B8's version restore
    doesn't duplicate it."""
    from transcriber import Segment, Word

    segments = [
        Segment(
            start=s["start"],
            end=s["end"],
            text=s["text"],
            speaker=s.get("speaker"),
            words=[Word(**word) for word in s.get("words", [])],
        )
        for s in payload.get("segments", [])
    ]
    return TranscriptionResult(
        segments=segments,
        language=payload.get("language", ""),
        duration=payload.get("duration", 0.0),
        speaker_names=payload.get("speaker_names") or {},
    )


class MainWindow(QMainWindow):
    """Main application window with header-bar settings layout."""

    def __init__(self):
        super().__init__()
        # Every object with background work to stop on window close
        # registers itself here instead of closeEvent reaching into each
        # one individually (see ui/shutdownable.py).
        self._shutdownables: list[Shutdownable] = []
        self._registry = WorkerRegistry(parent=self)
        # Shared by insights_panel/youtube_panel/cover_view so a type more
        # than one of them generates (e.g. "chapters") isn't recomputed —
        # see core/insights_cache.py.
        self._insights_cache = InsightsCache()
        self.transcriber = Transcriber()
        self._shutdownables.append(self.transcriber)
        self._cleaned_text: str | None = None
        self._clean_job: JobRunner | None = None
        self._article_job: JobRunner | None = None
        self._insights_job: JobRunner | None = None
        self._youtube_job: JobRunner | None = None
        self._book_job: JobRunner | None = None
        # StepContext of whichever job is currently running, kept around
        # so its _on_*_job_finished handler can reload a SKIPPED (cache
        # hit — B1) outcome's result from disk via load_step_result(),
        # the same way _recipe_get_result()/_on_recipe_step_finished() do
        # for the recipe path.
        self._clean_job_context: StepContext | None = None
        self._article_job_context: StepContext | None = None
        self._insights_job_context: StepContext | None = None
        self._youtube_job_context: StepContext | None = None
        self._book_job_context: StepContext | None = None
        # A recipe-driven launch (see _run_recipe, B6) runs the rest of the
        # selected recipe's steps as one JobRunner once transcription
        # finishes — separate from the five single-step _*_job attributes
        # above, which stay as each panel's own direct re-run/retry path.
        self._recipe_job: JobRunner | None = None
        # The current recipe run's spec/state/context, kept around so a
        # RunView retry (_on_recipe_retry) can rebuild a fresh JobRunner
        # against the same JobRun instead of starting the whole run over.
        self._recipe_run: JobRun | None = None
        self._recipe_spec: JobSpec | None = None
        self._recipe_step_names: tuple = ()
        self._recipe_context: StepContext | None = None
        # The job_runs row id for the run above, once persisted (B8, see
        # application/run_store.py) — None until there's a real history
        # record to attach it to.
        self._recipe_run_id: int | None = None
        self._gpu_worker: GPUDetectionWorker | None = None
        self._draft_worker: DraftAssemblyWorker | None = None
        self._shutdownables.append(_WorkerShutdown(self, "_clean_job"))
        self._shutdownables.append(_WorkerShutdown(self, "_article_job"))
        self._shutdownables.append(_WorkerShutdown(self, "_insights_job"))
        self._shutdownables.append(_WorkerShutdown(self, "_youtube_job"))
        self._shutdownables.append(_WorkerShutdown(self, "_book_job"))
        self._shutdownables.append(_WorkerShutdown(self, "_recipe_job"))
        self._shutdownables.append(_WorkerShutdown(self, "_gpu_worker", wait_ms=1500))
        # Cancellation stops ffmpeg via SIGTERM (falling back to SIGKILL
        # after 5s) — bound generously above that worst case so close()
        # doesn't give up on the wait before the subprocess actually dies.
        self._shutdownables.append(_WorkerShutdown(self, "_draft_worker", wait_ms=12000))
        self._source_filepath: str | None = None
        self._source_kind = "file"
        self._use_gpu = True
        self._gpu_type, self._gpu_name = self.transcriber.gpu_type, self.transcriber.gpu_name
        # ETA tracking
        self._transcription_start: float = 0.0
        self._last_record_id: int | None = None
        self._versions_dialog = None  # B8: TranscriptVersionsDialog, lazily created
        self._live_checkpoint = LiveCheckpointTracker()
        # Debounced transcript-version save (B8,
        # docs/IMPROVEMENT_PLAN_2026-08.ru.md item 2): a version is
        # written 5s after the last edit, not on every keystroke —
        # restarted on each _on_transcript_changed() call.
        self._revision_save_timer = QTimer(self)
        self._revision_save_timer.setSingleShot(True)
        self._revision_save_timer.setInterval(5000)
        self._revision_save_timer.timeout.connect(self._save_transcript_revision)
        self._setup_ui()
        self._document_session = DocumentSession()
        self._register_document_session_consumers()
        self.command_palette = CommandPalette(self)
        self.command_palette.bind_run_view(self.run_view)
        self.command_palette.bind_actions(self._palette_menu_actions)
        self.command_palette.record_requested.connect(self._open_record_view)
        self.command_palette.recipe_requested.connect(self._select_recipe_from_palette)
        self.command_palette.retry_step_requested.connect(self._retry_recipe_step_from_palette)
        self._connect_signals()
        self.setAcceptDrops(True)
        # Apply saved mic device
        cfg = get_config()
        self.recorder_widget.set_device(getattr(cfg, "mic_device_index", None))
        # A job_runs row can only stay 'running' while the process that
        # wrote it is alive (see run_store.save_run's docstring) — one
        # still 'running' at startup means that process died mid-run
        # without ever closing it out. Flip those to 'interrupted' before
        # the Library reads job_runs below, so a dead run's card offers
        # "Продолжить" (B2, docs/IMPROVEMENT_PLAN_2026-08.ru.md) instead
        # of forever reading as still in progress.
        try:
            from application import run_store
            run_store.mark_stale_running_as_interrupted()
        except Exception as exc:
            logger.warning("Failed to mark stale job_runs as interrupted: %s", exc)
        # The Library is the startup page but nothing else triggers its
        # first load — every other refresh() call is a reaction to
        # navigating back to it or saving a new record, neither of which
        # has happened yet on a cold start.
        self.library_view.refresh()
        if os.environ.get("WHISPERED_UI_GALLERY") != "1":
            self._start_gpu_detection()

    @property
    def _current_result(self) -> TranscriptionResult | None:
        """The transcription result every panel is currently showing.

        Read-only and delegates to DocumentSession, which is the single
        place that sets it (see DocumentSession.apply_result()) — a stray
        ``self._current_result = ...`` anywhere in this file would raise
        AttributeError immediately rather than silently going out of sync
        with what the panels actually display.
        """
        return self._document_session.current_result

    def closeEvent(self, event):
        """Handle window close - cleanup resources.

        Every panel/worker with background work registers itself in
        ``self._shutdownables`` as it's constructed (see __init__ and
        _setup_ui); this used to be a hand-maintained ladder of
        ``hasattr`` guards here, including direct access to another
        panel's private state (``book_panel._batch_worker``). See
        ui/shutdownable.py.
        """
        for shutdownable in self._shutdownables:
            shutdownable.shutdown()
        event.accept()


    def _setup_ui(self):
        """Build the persistent Library | Document | Inspector workspace."""
        self._build_window_chrome()
        self._build_library_section()
        self._build_queue_section()
        self._build_recorder_section()
        self._build_live_section()
        self._build_record_section()

    def _build_window_chrome(self) -> None:
        """Create the window and the vertical host for shell + status."""
        self.setWindowTitle("Whispered")
        self.setMinimumSize(900, 550)
        self.resize(1100, 700)

        self._palette_menu_actions = self._init_menu_bar()

        central = QWidget()
        self.setCentralWidget(central)
        self._workspace_layout = QVBoxLayout(central)
        self._workspace_layout.setContentsMargins(0, 0, 0, 0)
        self._workspace_layout.setSpacing(0)

    def _init_menu_bar(self) -> "list":
        """Build the global menu bar (native on macOS) from one
        declarative table instead of ~24 repeated construct-and-addAction
        blocks (B12, docs/IMPROVEMENT_PLAN_2026-08.ru.md).

        Returns the QActions marked ``in_palette=True`` — MainWindow
        hands this straight to ``CommandPalette.bind_actions()`` so the
        command palette's generic-action rows are exactly these QActions,
        not a second hardcoded list that could silently drift from what
        the menu bar actually offers.
        """
        from PyQt6.QtGui import QAction, QKeySequence

        _SEPARATOR = object()
        # (menu_key, label_key, shortcut, slot, in_palette). label_key is
        # also what the palette row shows via action.text() — see
        # ui/command_palette.py's module docstring for why that matters.
        table = (
            ("menu_file", "menu_new_record", "", self._show_new_draft, True),
            ("menu_file", "menu_open", "Ctrl+O", self._menu_open_file, False),
            ("menu_file", "menu_export", "Ctrl+E", self._export_result, True),
            ("menu_file", _SEPARATOR, "", None, False),
            ("menu_file", "menu_settings", "Ctrl+,", self._open_settings, True),

            ("menu_edit", "menu_undo", "", self._trigger_undo, False),
            ("menu_edit", "menu_find", "", self._trigger_find, False),
            ("menu_edit", _SEPARATOR, "", None, False),
            ("menu_edit", "menu_copy_transcript", "Ctrl+Shift+C", self._copy_to_clipboard, False),

            ("menu_view", "menu_toggle_theme", "", self._toggle_theme, False),
            ("menu_view", "menu_toggle_library", "", self._toggle_library_sidebar, False),
            ("menu_view", "menu_show_queue", "", lambda: self.status_bar.show_queue(True), True),

            ("menu_go", "menu_go_library", "Ctrl+1", self._menu_focus_library, False),
            ("menu_go", "menu_go_article", "Ctrl+2", self._menu_show_articles, False),
            ("menu_go", "menu_go_live", "", lambda: self._on_section_changed("live"), True),
            ("menu_go", "menu_go_recipe_editor", "Ctrl+3", self._open_recipe_editor, False),
            # Cover workspace (docs/IMPROVEMENT_PLAN_2026-08.ru.md, A5):
            # before this, the only entrance was one button in the
            # Library panel — not the menu bar, the command palette, or
            # reachable from the record a cover is actually made for.
            ("menu_go", "menu_go_cover", "Ctrl+4", lambda: self._on_section_changed("cover"), False),

            ("menu_transcribe", "menu_start_transcription", "Ctrl+T", self._start_transcription, False),
            ("menu_transcribe", "menu_toggle_recording", "Ctrl+R", self._menu_toggle_recording, False),
            ("menu_transcribe", "menu_play_pause", "Space", self._space_play_pause, False),

            ("menu_video", "video_export_edl", "", self._trigger_export_edl, False),
            ("menu_video", "video_mark_pauses", "", self._trigger_mark_pauses, False),
            ("menu_video", "video_assemble_draft", "", self._trigger_assemble_mp4, False),

            ("menu_library", "menu_refresh_library", "", lambda: self.library_view.refresh(), False),
            ("menu_library", _SEPARATOR, "", None, False),
            ("menu_library", "menu_clear_history", "", lambda: self.library_view.clear_all(), False),

            ("menu_help", "menu_help_docs", "", self._open_help_docs, False),
        )

        menubar = self.menuBar()
        menubar.setNativeMenuBar(True)
        menus: dict = {}
        palette_actions = []
        for menu_key, label_key, shortcut, slot, in_palette in table:
            menu = menus.get(menu_key)
            if menu is None:
                menu = menubar.addMenu(tr(menu_key))
                menus[menu_key] = menu
            if label_key is _SEPARATOR:
                menu.addSeparator()
                continue
            action = QAction(tr(label_key), self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(slot)
            menu.addAction(action)
            if in_palette:
                palette_actions.append(action)
        return palette_actions

    # ── menu targets ─────────────────────────────────────────────────
    # Each of these navigates to the screen the action actually happens
    # on before performing it. Firing them blind is what the menu bar
    # used to do, and from the wrong screen it looked like nothing
    # happened at all: the file dialog fed a file selector on a page the
    # user could not see, Go > Article switched a tab inside a hidden
    # QTabWidget, and Go > Library focused a QLineEdit that is not on
    # screen while the Library is collapsed.

    def _menu_open_file(self) -> None:
        self._show_new_draft()
        self.file_selector.browse_btn.click()

    def _menu_focus_library(self) -> None:
        if getattr(get_config(), "library_collapsed", False):
            self.workspace_shell.set_library_collapsed(False)
        self.library_view._search_edit.setFocus()

    def _menu_show_articles(self) -> None:
        self._stack.setCurrentIndex(self._record_index)
        self.main_tabs.setCurrentWidget(self.article_view)

    def _menu_toggle_recording(self) -> None:
        self._stack.setCurrentIndex(self._start_index)
        self.start_view.set_source("recorder")
        self.recorder_widget._toggle_recording()

    def _trigger_undo(self) -> None:
        """Trigger undo on the currently focused widget if supported."""
        from PyQt6.QtWidgets import QApplication
        focus_widget = QApplication.focusWidget()
        if hasattr(focus_widget, "undo") and callable(focus_widget.undo):
            focus_widget.undo()

    def _trigger_find(self) -> None:
        """Open transcript search when a record is the active workspace."""
        if (
            hasattr(self, "transcript_view")
            and hasattr(self, "_stack")
            and self._stack.currentIndex() == self._record_index
        ):
            self.transcript_view._open_find()

    def _trigger_export_edl(self) -> None:
        if hasattr(self, "cut_view"):
            self.cut_view.video_panel._export_btn.click()

    def _trigger_mark_pauses(self) -> None:
        if hasattr(self, "cut_view"):
            self.cut_view.video_panel._mark_btn.click()

    def _trigger_assemble_mp4(self) -> None:
        if hasattr(self, "cut_view"):
            self.cut_view.video_panel._assemble_btn.click()

    def _toggle_theme(self) -> None:
        """Flip dark/light theme immediately, mirroring what Settings' Apply does."""
        from ui.theme import apply_theme
        cfg = get_config()
        new_theme = "light" if cfg.theme == "dark" else "dark"
        cfg.theme = new_theme
        save_config()
        app = QApplication.instance()
        if app:
            apply_theme(app, new_theme)

    def _toggle_library_sidebar(self) -> None:
        collapsed = not getattr(get_config(), "library_collapsed", False)
        self.workspace_shell.set_library_collapsed(collapsed)

    def _open_help_docs(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl("https://github.com/samoilovda/Whispered"))

    def _build_library_section(self) -> None:
        """Create the shared source widgets and persistent Library."""
        self.transcribe_options = TranscribeOptions()
        self.model_combo = self.transcribe_options.model_combo
        self.language_combo = self.transcribe_options.language_combo
        self.translate_checkbox = self.transcribe_options.translate_checkbox
        self.perf_combo = self.transcribe_options.perf_combo
        self.diarization_checkbox = self.transcribe_options.diarization_checkbox

        # Recorder widget lives on its own sidebar section now; still
        # created here so _connect_signals/_apply_config_defaults and the
        # Ctrl+R shortcut keep a single stable reference.
        self.recorder_widget = RecorderWidget()
        self.recorder_widget.file_ready.connect(self._on_recording_ready)
        self.recorder_widget.error.connect(self._on_recording_error)
        self._shutdownables.append(self.recorder_widget)

        self.file_selector = FileSelector()
        self._shutdownables.append(self.file_selector)
        self.file_selector.set_compact(self.height() < 650)

        # Batch Processing Panel lives on its own sidebar section (Queue);
        # still created here so signal wiring stays with the rest of setup.
        self.batch_panel = BatchPanel()
        self.batch_panel.start_requested.connect(self._start_batch_processing)
        self._shutdownables.append(self.batch_panel)

        # Watch folder (B5b): new supported files dropped into a
        # configured local directory join the same queue a multi-file
        # drop does (B5a) — see _apply_watch_folder_config().
        from core.watch_folder import WatchFolderService
        self._watch_folder_service = WatchFolderService(self)
        self._watch_folder_service.file_found.connect(self._on_watch_folder_file_found)
        self._apply_watch_folder_config()

        # Course Capture Panel — same "persistent status surface" placement
        # as Batch (see _build_queue_section): a queue of lessons captured
        # one at a time via the Live system-audio pipeline instead of files.
        self.course_capture_panel = CourseCapturePanel()
        self.course_capture_panel.lesson_saved.connect(self._on_course_lesson_saved)
        self._shutdownables.append(self.course_capture_panel)

        # Book Pipeline Panel — created here (not mode-gated) so its
        # connection-check timers start with the rest of setup; it's shown
        # as its own tab on the Record view (see content_tabs below).
        self.book_panel = BookPanel()
        self.book_panel.run_single_requested.connect(self._start_book_job)
        self.book_panel.cancel_requested.connect(self._cancel_operation)
        self._shutdownables.append(self.book_panel)

        self.library_view = LibraryView()
        self.library_view.open_record.connect(self._open_record_view)
        self.library_view.resume_run.connect(self._resume_run)
        self.library_view.open_cover.connect(lambda: self._on_section_changed("cover"))

    def _build_queue_section(self) -> None:
        """Queue is mounted in the persistent status surface later."""

    def _build_recorder_section(self) -> None:
        """Recorder is mounted as a StartView source later."""

    def _build_live_section(self) -> None:
        """Create Live once; StartView owns its visible placement."""
        self.live_view = LiveView()
        self.live_runtime = LiveRuntime(self)
        self._live_preflight_worker = None
        self._shutdownables.append(self.live_view)
        self._shutdownables.append(self.live_runtime)
        self._shutdownables.append(
            _WorkerShutdown(self, "_live_preflight_worker", wait_ms=2500)
        )
        self.live_view.preflight_requested.connect(self._run_live_preflight)
        self.live_view.start_requested.connect(self._start_live)
        self.live_view.pause_requested.connect(self._pause_live)
        self.live_view.stop_requested.connect(self._stop_live)
        self.live_runtime.segment_update.connect(self._on_live_segment_update)
        self.live_runtime.source_state_changed.connect(self.live_view.set_source_state)
        self.live_runtime.level_changed.connect(self.live_view.set_level)
        self.live_runtime.error_occurred.connect(self._on_live_error)
        self.live_runtime.finished.connect(self._on_live_finished)
        self.live_runtime.session_state_changed.connect(self.live_view.set_session_state)
        self.live_view.open_record_requested.connect(self._open_completed_live)
        self.live_view._timer.timeout.connect(self._update_live_metrics)

    def _build_record_section(self) -> None:
        """Build document content, inspector pages, draft and shell."""
        self.record_view = RecordView()
        self.record_view.back_requested.connect(self._show_library)
        self.record_view.export_requested.connect(self._export_result)
        self.record_view.export_preset_requested.connect(self._export_preset)
        self.record_view.clean_requested.connect(self._start_text_cleaning)
        self.record_view.articles_requested.connect(self._start_generate_all)
        # cover_view.set_segments() is already a DocumentSession consumer
        # (see _register_document_session_consumers) — the open record's
        # segments are already loaded by the time this button is clickable.
        self.record_view.cover_requested.connect(lambda: self._on_section_changed("cover"))
        self.record_view.versions_requested.connect(self._open_versions_dialog)

        # Audio player (hidden when multimedia backend unavailable)
        self.player = PlayerWidget()

        # Document tabs remain in the center; generated tools move to the inspector.
        self.main_tabs = QTabWidget()

        self.transcript_view = TranscriptView()
        self.main_tabs.addTab(self.transcript_view, tr("tab_transcript"))

        self.cleaned_view = CleanedTextView()
        self.main_tabs.addTab(self.cleaned_view, tr("tab_cleaned"))

        self.article_view = ArticleView()

        self.chat_panel = ChatPanel()
        self._shutdownables.append(self.chat_panel)

        self.insights_panel = InsightsPanel()
        self.insights_panel.generate_requested.connect(self._start_insights_job)
        self._shutdownables.append(self.insights_panel)

        self.cut_view = CutView()
        self.cut_view.video_panel.export_edl_requested.connect(self._export_edl)
        self.cut_view.video_panel.mark_pauses_requested.connect(self._mark_pauses)
        self.cut_view.video_panel.assemble_requested.connect(self._assemble_draft)

        self.youtube_panel = YouTubePanel()
        self.youtube_panel.generate_requested.connect(self._start_youtube_job)
        self._shutdownables.append(self.youtube_panel)

        # The five generator panels plus Cut/Chat used to live as inspector
        # rail pages; the inspector is retired in B6, so they become plain
        # tabs on the same main_tabs widget the transcript/cleaned-text tabs
        # already use — one content surface, no second navigation column.
        self.main_tabs.addTab(self.article_view, tr("tab_articles"))
        self.main_tabs.addTab(self.youtube_panel, tr("tab_youtube"))
        self.main_tabs.addTab(self.book_panel, tr("tab_book"))
        self.main_tabs.addTab(self.insights_panel, tr("tab_insights"))
        self.main_tabs.addTab(self.cut_view, tr("tab_cut"))
        self.main_tabs.addTab(self.chat_panel, tr("tab_chat"))
        self.record_view.set_content_widgets(self.player, self.main_tabs)

        self._stack = QStackedWidget()
        folder_source = QWidget()
        folder_layout = QVBoxLayout(folder_source)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_hint = QLabel(tr("draft_folder_hint"))
        folder_hint.setProperty("role", "muted")
        folder_hint.setWordWrap(True)
        folder_layout.addWidget(folder_hint)
        queue_button = AnimatedButton(tr("draft_open_queue"))
        queue_button.clicked.connect(lambda: self.status_bar._toggle_queue())
        folder_layout.addWidget(queue_button)
        folder_layout.addStretch()
        self.start_view = StartView(
            self.file_selector,
            self.recorder_widget,
            self.live_view,
            self.live_view.options_panel,
            folder_source,
            self.transcribe_options,
        )
        self.start_view.process_requested.connect(self._start_transcription)
        self.start_view.configure_recipe_requested.connect(self._open_recipe_editor)
        self._start_index = self._stack.addWidget(self.start_view)
        self._record_index = self._stack.addWidget(self.record_view)
        self.cover_view = CoverView(insights_cache=self._insights_cache)
        self._shutdownables.append(self.cover_view)
        self._cover_index = self._stack.addWidget(self.cover_view)
        # Run view (docs/UI_REDESIGN_PLAN_2026-09.ru.md, B4): one row per
        # step in the whole registry, fixed order — a JobRun only ever
        # populates the subset the active recipe actually includes, so an
        # out-of-recipe row simply stays "waiting" forever. It stays a pure
        # status/retry feed (no per-row viewer, see ui/run_view.py's module
        # docstring): a step's content is shown on its own main_tabs tab
        # instead, since a widget already tabbed into RecordView can't also
        # be reparented into a RunView row.
        self.run_view = RunView(
            step_order=[d.name for d in STEP_DEFINITIONS],
            labels={d.name: tr(d.label_key) for d in STEP_DEFINITIONS},
        )
        self.run_view.retry_requested.connect(self._on_recipe_retry)
        self.run_view.regenerate_requested.connect(self._on_recipe_regenerate)
        self.run_view.overall_progress_changed.connect(self._on_recipe_overall_progress)
        self.run_view.cancel_requested.connect(self._cancel_recipe_job)
        self.run_view.open_record_requested.connect(
            lambda: self._stack.setCurrentIndex(self._record_index)
        )
        self._run_index = self._stack.addWidget(self.run_view)

        self.workspace_shell = WorkspaceShell(self.library_view, self._stack)
        self.workspace_shell.new_requested.connect(self._show_new_draft)
        self._workspace_layout.addWidget(self.workspace_shell, stretch=1)

        self.status_bar = StatusBar()
        self.status_label = self.status_bar.status_label
        self.progress_bar = self.status_bar.progress
        self.cancel_btn = self.status_bar.cancel_button
        self.device_btn = self.status_bar.device_button
        self.device_btn.setToolTip(tr("tooltip_device"))
        self.device_btn.clicked.connect(self._toggle_device)
        self.status_bar.cancel_requested.connect(self._cancel_operation)
        self.status_bar.bind_queue(self.batch_panel)
        self.batch_panel.queue_changed.connect(self.status_bar.set_queue_count)
        self.status_bar.bind_course(self.course_capture_panel)
        self.course_capture_panel.queue_changed.connect(self.status_bar.set_course_count)
        # Course Capture sits on the same not-GA system-audio pipeline as
        # Live (see Config.live_transcription_enabled) and follows the same
        # opt-in gate rather than shipping ahead of it.
        self.status_bar.set_course_available(get_config().live_transcription_enabled)
        self.book_panel.connection_changed.connect(self.status_bar.set_llm_status)
        self.progress_timeline = ProgressTimeline()
        self.progress_timeline.stages = [
            tr("timeline_select"), tr("timeline_extract"), tr("timeline_transcribe"),
            tr("timeline_diarize"), tr("timeline_clean"), tr("timeline_generate"),
        ]
        self.progress_timeline.setVisible(False)
        self.status_bar.add_detail_widget(self.progress_timeline)
        self._workspace_layout.addWidget(self.status_bar)
        self.transcribe_btn = self.start_view.process_button
        self._update_device_badge()

    def _on_section_changed(self, key: str) -> None:
        """Compatibility entry point: top-level destinations are start sources."""
        if key == "cover":
            self._stack.setCurrentIndex(self._cover_index)
            self.status_bar.show_queue(False)
            return
        self._stack.setCurrentIndex(self._start_index)
        self.status_bar.show_queue(key == "queue")
        if key in {"recorder", "live"}:
            self.start_view.set_source(key)
        elif key == "queue":
            self.start_view.set_source("folder")
        else:
            self.start_view.set_source("file")
        if key == "live" and os.environ.get("WHISPERED_UI_GALLERY") != "1":
            self.live_view.setup.refresh_targets()

    def _show_new_draft(self) -> None:
        self._stack.setCurrentIndex(self._start_index)
        self.start_view.set_source("file")

    def _show_library(self) -> None:
        """Navigate back to the Library page (from the Record view) and
        refresh the list in case anything changed while a record was open."""
        self._stack.setCurrentIndex(self._start_index)
        self.library_view.refresh()

    def _open_record_view(self, record_id: int, artifact_type: str = "") -> None:
        """Load a history record and switch to the Record page.

        *artifact_type* (B7, docs/IMPROVEMENT_PLAN_2026-08.ru.md) is the
        reverse of ``_STEP_TO_ARTIFACT_TYPE`` — set when the caller is a
        materials search hit (Library scope toggle or the Ctrl+K palette),
        so the record opens straight on the tab that hit's own generator
        writes to instead of always landing on the transcript.
        """
        if self._load_from_history(record_id):
            self.library_view.set_open_record(record_id)
            self._stack.setCurrentIndex(self._record_index)
            tab = self._ARTIFACT_TYPE_TO_TAB.get(artifact_type)
            if tab is not None:
                self.main_tabs.setCurrentWidget(getattr(self, tab))

    def _live_options(self):
        use_mic, use_system = self.live_view.selected_sources()
        return use_mic, use_system, self.live_view.selected_model()

    def _run_live_preflight(self):
        if self._live_preflight_worker and self._live_preflight_worker.isRunning():
            return
        use_mic, use_system, model = self._live_options()
        self.live_view.set_preflighting()
        worker = LivePreflightWorker(
            use_mic=use_mic,
            use_system=use_system,
            model_name=model,
            target_available=not use_system or self.live_view.selected_target() is not None,
            helper_path=default_helper_path(),
            parent=self,
        )
        worker.completed.connect(self.live_view.show_preflight)
        self._live_preflight_worker = worker
        worker.start()

    def _start_live(self):
        use_mic, use_system, model = self._live_options()
        discovered = self.live_view.selected_target()
        if use_system and discovered is None:
            self.live_view.invalidate_preflight()
            return
        self.live_view.reset_session()
        self._live_checkpoint.start(
            source_name=time.strftime("Live %Y-%m-%d %H:%M"),
            model_name=model,
        )
        started = self.live_runtime.start(
            use_mic=use_mic,
            use_system=use_system,
            model_name=model,
            language=self.live_view.selected_language(),
            mic_device=self.live_view.selected_mic_device(),
            target=discovered.capture_target() if discovered else None,
            helper_path=default_helper_path(),
        )
        if started:
            self.live_view._timer.start()

    def _pause_live(self, paused: bool):
        if paused:
            self.live_runtime.pause()
        else:
            self.live_runtime.resume()

    def _stop_live(self):
        self.live_runtime.stop()

    def _update_live_metrics(self):
        if self.live_runtime.session_state.value not in {"idle", "completed"}:
            self.live_view.set_metrics(self.live_runtime.metrics())

    def _on_live_error(self, source: str, message: str):
        self.live_view.set_source_state(source, "failed")
        logger.error("Live %s failure: %s", source, message)

    def _on_live_segment_update(self, update) -> None:
        """Render an update and checkpoint only immutable live text."""
        self.live_view.accept_update(update)
        if update.state is not SegmentState.FINAL:
            return
        self._live_checkpoint.accept_final(update.segment_id, update.segment)
        self._checkpoint_live_history()

    def _checkpoint_live_history(self) -> None:
        """Persist finalized text during a meeting without writing audio."""
        if not getattr(get_config(), "history_enabled", True):
            return
        try:
            from core.history import get_history_store

            wrote = self._live_checkpoint.checkpoint(
                get_history_store(), self.live_view.selected_language()
            )
            if wrote:
                self._last_record_id = self._live_checkpoint.history_record_id
                self.library_view.refresh()
        except Exception as exc:
            logger.warning("Failed to checkpoint live transcript: %s", exc)

    def _on_live_finished(self, result: TranscriptionResult, source_path: str):
        self.live_view._timer.stop()
        self._source_filepath = source_path or None
        self._source_kind = "live"
        self._transcription_start = self.live_runtime._started_at
        if self._live_checkpoint.history_record_id is not None:
            try:
                from core.history import get_history_store
                store = get_history_store()
                store.update_result(
                    self._live_checkpoint.history_record_id,
                    result,
                    speaker_names=getattr(result, "speaker_names", {}) or {},
                )
                # First transcript version for a live-originated record
                # (B8) — the periodic checkpoints above are an in-place
                # save, not a version worth keeping individually; only
                # the finished transcript gets a baseline to restore to.
                store.save_current_revision(
                    self._live_checkpoint.history_record_id, result,
                    getattr(result, "speaker_names", {}) or {},
                    keep=get_config().transcript_revisions_kept,
                )
                self._last_record_id = self._live_checkpoint.history_record_id
                self.library_view.refresh()
            except Exception as exc:
                logger.warning("Failed to finalize live transcript history: %s", exc)
            self._on_finished(result, open_record=False, save_history=False)
        else:
            self._save_to_history(
                result,
                source_path="",
                source_name=self._live_checkpoint.source_name,
                model=self._live_checkpoint.model_name,
                speaker_names=getattr(result, "speaker_names", {}) or {},
            )
            self._on_finished(result, open_record=False, save_history=False)

    def _on_course_lesson_saved(self, _record_id: int) -> None:
        """Course Capture panel saves each finished lesson to history itself
        (it isn't the currently open document) — just refresh Library so
        the new row shows up."""
        self.library_view.refresh()

    def _open_completed_live(self):
        if self._current_result is not None:
            self._stack.setCurrentIndex(self._record_index)

    def _register_document_session_consumers(self) -> None:
        """Build the one list of "who gets told about a new result" that
        _on_finished, _load_from_history, and _on_transcript_changed all
        used to maintain separately (see DocumentSession's docstring).

        transcript_view is deliberately not registered here: it's the
        source of MANUAL_EDIT's result_changed signal, so re-applying
        set_result() from inside that same edit's handler would fight the
        widget's own in-progress state. FRESH_TRANSCRIPTION/HISTORY_OPEN/
        LIVE_FINISH still call transcript_view.set_result() directly at
        their own call sites.
        """
        self._document_session.register_consumer(
            lambda result: self.chat_panel.set_transcript(result.full_text)
        )
        self._document_session.register_consumer(
            lambda result: self.insights_panel.set_segments(
                result.segments, transcript_language=result.language
            )
        )
        self._document_session.register_consumer(
            lambda result: self.youtube_panel.set_segments(
                result.segments, transcript_language=result.language
            )
        )
        self._document_session.register_consumer(
            lambda result: self.cover_view.set_segments(
                result.segments, transcript_language=result.language
            )
        )
        self._document_session.register_consumer(
            lambda result: self.cut_view.set_result(result)
        )
        self._document_session.register_consumer(
            lambda result: self.book_panel.set_has_transcript(True)
        )

    def _connect_signals(self):
        """Connect widget signals."""
        self.file_selector.file_selected.connect(self._on_file_selected)
        self.file_selector.file_cleared.connect(self._on_file_cleared)
        self.transcript_view.copy_requested.connect(self._copy_to_clipboard)
        self.transcript_view.result_changed.connect(self._on_transcript_changed)

        # Article view signals
        self.article_view.copy_done.connect(lambda: show_toast(self, tr("toast_copied"), kind="success"))
        self.article_view.export_done.connect(lambda msg: show_toast(self, msg, kind="success"))
        self.cleaned_view.copy_requested.connect(lambda: show_toast(self, tr("toast_copied"), kind="success"))

        # Player ↔ transcript sync
        self.player.position_changed_sec.connect(self._on_player_position)
        self.transcript_view.seek_requested.connect(self.player.seek_to)
        self.cut_view.seek_requested.connect(self.player.seek_to)

        # Insights panel
        self.insights_panel.seek_requested.connect(self.player.seek_to)

        # Auto-save each completed batch item to history
        self.batch_panel.processor.item_finished.connect(self._on_batch_item_finished)

        # ── Keyboard shortcuts ────────────────────────────────────
        def _sc(seq, slot):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(slot)

        # Ctrl+,/O/T/E/Shift+C/R/1/2/3/Space are QAction shortcuts on the
        # menu bar (_init_menu_bar) — registering them again here as bare
        # QShortcuts would make the key ambiguous between the two.
        _sc("Ctrl+Return",  self.transcribe_btn.click)
        _sc("Ctrl+Enter",   self.transcribe_btn.click)  # numpad Enter
        _sc("Ctrl+K",       self.command_palette.open_palette)

    def _select_recipe_from_palette(self, key: str) -> None:
        """Command palette "Run: <recipe>" (B8): pick the recipe and land
        on the start screen — a source still has to be chosen/confirmed
        before Launch, same as picking a chip there directly."""
        self.start_view.select_recipe(key)
        self._show_new_draft()

    def _retry_recipe_step_from_palette(self, name: str) -> None:
        """Command palette "Restart: <step>" (B8) — retry it and jump to
        the run screen so its progress is visible."""
        self.run_view.retry_step(name)
        self._stack.setCurrentIndex(self._run_index)

    def _toggle_device(self):
        """Toggle between GPU and CPU mode."""
        if self._gpu_type == 'cpu':
            # No GPU available, can't toggle
            self.status_label.setText(tr("status_no_gpu"))
            return

        self._use_gpu = not self._use_gpu
        self._update_device_badge()

        device_name = self._gpu_name if self._use_gpu else "CPU"
        self.status_label.setText(tr("status_switched_device", device=device_name))

    def _start_gpu_detection(self) -> None:
        if self._gpu_worker and self._gpu_worker.isRunning():
            return
        worker = GPUDetectionWorker(self)
        self._gpu_worker = worker
        worker.detected.connect(self._on_gpu_detected)
        worker.finished.connect(lambda current=worker: self._on_gpu_worker_finished(current))
        worker.start()

    def _on_gpu_detected(self, gpu_type: str, gpu_name: str) -> None:
        self._gpu_type, self._gpu_name = gpu_type, gpu_name
        self.transcriber.gpu_type, self.transcriber.gpu_name = gpu_type, gpu_name
        if gpu_type == "cpu":
            self._use_gpu = False
        self._update_device_badge()

    def _on_gpu_worker_finished(self, worker: GPUDetectionWorker) -> None:
        if self._gpu_worker is worker:
            self._gpu_worker = None
        worker.deleteLater()

    def _update_device_badge(self):
        """Update the device button appearance based on current selection."""
        if self._gpu_type == "detecting":
            self.device_btn.setText(tr("device_detecting"))
            self.device_btn.setEnabled(False)
            role = "muted-badge"
        elif self._use_gpu and self._gpu_type in ('cuda', 'rocm'):
            self.device_btn.setText(f"🚀 {self._gpu_name}")
            self.device_btn.setEnabled(True)
            role = "muted-badge"
        elif self._use_gpu and self._gpu_type == 'metal':
            self.device_btn.setText(f"🍎 {self._gpu_name}")
            self.device_btn.setEnabled(True)
            role = "muted-badge"
        else:
            # CPU mode or no GPU
            self.device_btn.setText("💻 CPU")
            self.device_btn.setEnabled(self._gpu_type != "cpu")
            role = "muted-badge"

        self.device_btn.setProperty("role", role)
        # Property-based selectors need an explicit re-polish to take effect
        # once the widget has already been shown with a different role.
        self.device_btn.style().unpolish(self.device_btn)
        self.device_btn.style().polish(self.device_btn)

    def _open_settings(self):
        """Open the Settings dialog and apply any changes to the live UI."""
        from ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        dlg.settings_applied.connect(self._on_settings_applied)
        dlg.exec()

    def _on_settings_applied(self) -> None:
        """Apply saved preferences without requiring an application restart."""
        self._apply_config_defaults()
        self.transcript_view.apply_display_settings()
        cfg = get_config()
        self.recorder_widget.set_device(getattr(cfg, "mic_device_index", None))
        self.start_view._source_buttons["live"].setEnabled(
            cfg.live_transcription_enabled
        )
        self.status_bar.set_course_available(cfg.live_transcription_enabled)
        self._apply_watch_folder_config()
        self.transcribe_options.refresh_model_state()
        self.course_capture_panel.setup.refresh_model_state()
        self.live_view.setup.refresh_model_state()

    def _apply_watch_folder_config(self) -> None:
        """Point _watch_folder_service at Config.watch_folder, or stop it
        entirely when disabled or unset — called at startup and again
        after Settings is applied (B5b)."""
        cfg = get_config()
        folder = getattr(cfg, "watch_folder", "") if getattr(
            cfg, "watch_folder_enabled", False
        ) else ""
        self._watch_folder_service.set_folder(folder)

    def _on_watch_folder_file_found(self, path: str) -> None:
        """WatchFolderService.file_found (B5b): joins the same queue a
        multi-file drop does (B5a)."""
        self.batch_panel.add_files([path])
        self.status_bar.show_queue(True)

    def _apply_config_defaults(self):
        """Re-seed header controls from the saved config (after settings change)."""
        cfg = get_config()
        idx = next((i for i, (k, _) in enumerate(WHISPER_MODELS)
                    if k == cfg.default_model), None)
        if idx is not None:
            self.model_combo.setCurrentIndex(idx)
        idx = next((i for i, (k, _) in enumerate(WHISPER_LANGUAGES)
                    if k == cfg.default_language), None)
        if idx is not None:
            self.language_combo.setCurrentIndex(idx)
        idx = next((i for i, (k, *_) in enumerate(PERFORMANCE_MODES)
                    if k == cfg.performance_mode), None)
        if idx is not None:
            self.perf_combo.setCurrentIndex(idx)
        self.diarization_checkbox.setChecked(cfg.diarization_enabled)

    # ------------------------------------------------------------------ drag & drop

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(self._is_droppable(url) for url in urls):
                event.acceptProposedAction()
                self._show_drop_overlay(True)
                return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._show_drop_overlay(False)

    @staticmethod
    def _is_droppable(url) -> bool:
        """A url dragEnterEvent should accept: a supported file, or a
        directory (expanded into its supported files on drop — see
        _collect_dropped_paths)."""
        if not url.isLocalFile():
            return False
        path = url.toLocalFile()
        return is_supported_format(path) or os.path.isdir(path)

    @staticmethod
    def _collect_dropped_paths(urls) -> "tuple[list[str], int]":
        """Every url reduced to actual, supported file paths (B5a).

        A dropped folder is expanded one level deep only — not
        recursively — so a user dragging a folder full of unrelated
        subfolders doesn't silently pull in hundreds of files. Anything
        local-but-unsupported (a loose file with the wrong extension, or
        one found inside a dropped folder) is counted in *skipped*
        instead of raising — see the caller's toast."""
        paths: list[str] = []
        skipped = 0
        for url in urls:
            if not url.isLocalFile():
                continue
            local_path = url.toLocalFile()
            if os.path.isdir(local_path):
                for entry in sorted(Path(local_path).iterdir()):
                    if not entry.is_file():
                        continue
                    if is_supported_format(str(entry)):
                        paths.append(str(entry))
                    else:
                        skipped += 1
            elif is_supported_format(local_path):
                paths.append(local_path)
            else:
                skipped += 1
        return paths, skipped

    def dropEvent(self, event: QDropEvent):
        self._show_drop_overlay(False)
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        paths, skipped = self._collect_dropped_paths(event.mimeData().urls())
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()

        if len(paths) == 1:
            # The drop is accepted window-wide, but the selector it fills
            # and the Launch button that acts on it both live on the
            # start screen. Dropping a file while a record was open used
            # to leave the user looking at that record, with nothing on
            # screen having changed.
            self._show_new_draft()
            self.file_selector._set_file(paths[0])
        else:
            self.batch_panel.add_files(paths)
            self.status_bar.show_queue(True)

        if skipped:
            show_toast(
                self, tr("toast_drop_skipped_unsupported", count=skipped), kind="info"
            )

    def _show_drop_overlay(self, visible: bool):
        if not hasattr(self, "_drop_overlay"):
            overlay = QLabel(tr("drop_overlay"), self)
            overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            overlay.setProperty("role", "drop-overlay")
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self._drop_overlay = overlay
        ov = self._drop_overlay
        if visible:
            ov.setGeometry(self.centralWidget().geometry())
            ov.raise_()
            ov.show()
        else:
            ov.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "file_selector"):
            self.file_selector.set_compact(event.size().height() < 650)
        if hasattr(self, "_drop_overlay") and self._drop_overlay.isVisible():
            self._drop_overlay.setGeometry(self.centralWidget().geometry())

    # ------------------------------------------------------------------ helpers

    def _space_play_pause(self):
        """Space play/pause — only fires when focus is not in a text field."""
        focused = QApplication.focusWidget()
        if isinstance(focused, (QTextEdit, QLineEdit, QPlainTextEdit)):
            return
        self.player.toggle_play()

    def _on_player_position(self, seconds: float):
        """Forward player position to transcript highlight (throttled by player timer)."""
        self.transcript_view.highlight_at(seconds)

    def _on_recording_ready(self, filepath: str):
        """Load a freshly-recorded WAV into the file selector."""
        self.file_selector._set_file(filepath)
        self._source_kind = "recorder"

    def _on_recording_error(self, msg: str):
        QMessageBox.warning(self, tr("error_transcription"), msg)

    def _on_file_selected(self, filepath: str):
        """Handle file selection."""
        self._source_kind = "file"
        self._source_filepath = filepath
        self.start_view.set_process_enabled(True)
        self.status_label.setText(tr("status_ready_file", name=os.path.basename(filepath)))
        self.player.load(filepath)

    def _on_file_cleared(self):
        """Drop every media-specific reference when selection is cleared."""
        self._source_filepath = None
        self._source_kind = "file"
        self.player.load("")
        self.start_view.set_process_enabled(False)
        # Transcript-only records can still be exported, but cannot be cut.
        self.cut_view.video_panel.set_has_transcript(False)

    def _on_transcript_changed(self, _change_kind: str) -> None:
        """Propagate an in-place transcript edit to every dependent view."""
        result = self.transcript_view.get_result()
        if result is None:
            return
        self._cleaned_text = None
        self.cleaned_view.clear()
        self.article_view.clear()
        self._document_session.apply_result(result)
        if not getattr(get_config(), "history_enabled", True):
            return
        try:
            from core.history import get_history_store
            store = get_history_store()
            if _change_kind == "speakers":
                # A rename dialog's untouched fields fall back to the raw
                # speaker id itself (ui/transcript_view.py's
                # _SpeakerRenameDialog.get_names()) — only a name that
                # actually differs from its id is worth remembering as a
                # future suggestion (B6, docs/IMPROVEMENT_PLAN_2026-08.ru.md).
                for sid, alias in (result.speaker_names or {}).items():
                    if alias and alias != sid:
                        store.remember_speaker_alias(alias)
            if self._last_record_id is not None:
                store.update_result(
                    self._last_record_id,
                    result,
                    getattr(result, "speaker_names", {}),
                )
                self.library_view.refresh()
                # Debounced version save (B8) — restarted on every edit so
                # a burst of keystrokes/renames produces one version 5s
                # after the last one, not one per edit.
                self._revision_save_timer.start()
        except Exception as exc:
            logger.warning("Failed to persist transcript edit: %s", exc)

    def _save_transcript_revision(self) -> None:
        """Fired by _revision_save_timer, 5s after the last transcript
        edit (B8, docs/IMPROVEMENT_PLAN_2026-08.ru.md item 2). A no-op
        write (the edit ended up producing the same transcript_revision
        as the last saved version) is silently skipped inside
        HistoryStore.save_current_revision() itself."""
        if self._last_record_id is None:
            return
        result = self.transcript_view.get_result()
        if result is None:
            return
        try:
            from core.history import get_history_store
            get_history_store().save_current_revision(
                self._last_record_id, result,
                getattr(result, "speaker_names", {}),
                keep=get_config().transcript_revisions_kept,
            )
        except Exception as exc:
            logger.warning("Failed to save transcript version: %s", exc)

    def _open_versions_dialog(self) -> None:
        """B8: open (or raise) the non-modal "Версии" dialog for the
        currently open record."""
        if self._last_record_id is None:
            return
        from ui.transcript_versions_dialog import TranscriptVersionsDialog

        if self._versions_dialog is not None:
            self._versions_dialog.close()
        dialog = TranscriptVersionsDialog(self._last_record_id, parent=self)
        dialog.restore_requested.connect(self._restore_transcript_revision)
        self._versions_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _restore_transcript_revision(self, revision_id: int) -> None:
        """Apply a saved transcript version through DocumentSession.
        apply_result() (B8 item 4) — the same fan-out every other path
        producing/loading a result uses, so every panel (cleaned text,
        article, cover, ...) sees the restored content, not just the
        transcript tab. Also persists it as the record's current text and
        writes a new version for it (B8 item 5): the restored content's
        own transcript_revision differs from whatever was last saved, so
        this legitimately produces one more entry in the version list,
        and (being a real revision change) correctly invalidates any
        generated material's cache the next time a recipe runs."""
        if self._last_record_id is None:
            return
        try:
            from core.history import get_history_store
            store = get_history_store()
            payload = store.get_transcript_revision(revision_id)
            if payload is None:
                return
            result = _result_from_payload(payload)
            self.transcript_view.set_result(result)
            self._document_session.apply_result(result)
            speaker_names = getattr(result, "speaker_names", {})
            store.update_result(self._last_record_id, result, speaker_names)
            store.save_current_revision(
                self._last_record_id, result, speaker_names,
                keep=get_config().transcript_revisions_kept,
            )
            self.library_view.refresh()
            if self._versions_dialog is not None:
                self._versions_dialog.reload()
        except Exception as exc:
            logger.warning("Failed to restore transcript version: %s", exc)

    def _start_transcription(self):
        """Start the transcription process."""
        # The Process button is disabled while a recipe run is active, but
        # the Ctrl+T / Ctrl+Return shortcuts call this directly — without
        # this guard they'd start a second transcription mid-run (and the
        # youtube_panel.clear() below would kill the run's workers while it
        # still waits on them, hanging the UI).
        if self._recipe_job is not None and self._recipe_job.isRunning():
            return
        filepath = self.file_selector.get_file()
        if not filepath:
            return

        # Update UI for transcription mode
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.setVisible(False)
        self.status_bar.set_busy(True)
        self.progress_bar.setValue(0)
        # Select is done (file chosen); Extract is the first active stage
        self.progress_timeline.setVisible(True)
        self.progress_timeline.set_stage(1, 0)
        self.transcript_view.clear()
        self.cleaned_view.clear()
        self.article_view.clear()
        self.chat_panel.clear_transcript()
        self._cancel_insights_job()
        self.insights_panel.clear()
        self._cancel_youtube_job()
        self.youtube_panel.clear()
        self._cancel_book_job()
        self.cut_view.clear()
        self._cleaned_text = None

        # Get settings from header controls, with the selected recipe's
        # own params (B4, docs/IMPROVEMENT_PLAN_2026-08.ru.md) overriding
        # when set — a recipe without an override for a given key falls
        # back to whatever the shared widget/Config already has, same as
        # every launch before B4.
        recipe = self._resolve_recipe(self.start_view.current_recipe_key())
        params = recipe.params or {}
        model = params.get("model") or self.model_combo.currentData()
        language = params.get("language") or self.language_combo.currentData()
        translate = (
            params["translate"] if "translate" in params
            else self.translate_checkbox.isChecked()
        )
        perf_mode = params.get("performance_mode") or self.perf_combo.currentData()

        # Determine thread count based on performance mode
        n_threads = get_thread_count(perf_mode)

        enable_diarization = (
            params["diarization"] if "diarization" in params
            else self.diarization_checkbox.isChecked()
        )

        self._transcription_start = time.monotonic()
        # Build initial prompt from custom vocabulary
        vocab = getattr(get_config(), "custom_vocabulary", []) or []
        prompt = _build_initial_prompt(vocab) if vocab else None
        # Start transcription
        self.transcriber.transcribe(
            filepath=filepath,
            model_name=model,
            language=language,
            translate=translate,
            n_threads=n_threads,
            enable_diarization=enable_diarization,
            num_speakers=None,  # Auto-detect
            initial_prompt=prompt,
            # Sentence-level segments, not per-word: word_timestamps=True
            # routes through _group_words_into_segments, whose ''.join
            # gluing loses inter-word spaces for Cyrillic BPE tokens and
            # wrecks the transcript. The Cut tab works fine on sentence
            # segments; finer word-level cutting needs that merge fixed
            # for non-Latin scripts first.
            word_timestamps=False,
            use_gpu=self._use_gpu,
            on_progress=self._on_progress,
            on_finished=self._on_finished,
            on_error=self._on_error
        )

    def _cancel_recipe_job(self) -> None:
        """Cancel the current recipe run without blocking the GUI thread.

        Mirrors the five single-step ``_cancel_*_job`` methods: disconnects
        business signals immediately (so a late step/job-finished can't
        reach UI state that already believes the run was cancelled) and
        hands it to WorkerRegistry, which deletes it once its QThread
        actually finishes.

        Reporting the cancellation lives here rather than in the status
        bar's own handler: retiring the runner disconnects job_finished,
        so _on_recipe_job_finished never runs and nothing else takes the
        screen out of its "running" state. A run cancelled from a step
        row's own Cancel button used to leave "Running the recipe…" on the
        status bar, the Cancel button still offering to cancel it, and the
        run screen with no way out.
        """
        if self._recipe_job is not None and self._recipe_job.isRunning():
            self._registry.retire(self._recipe_job)
            self._recipe_job = None
            self._save_recipe_run("cancelled")
            self.library_view.refresh()
            self.status_label.setText(tr("status_chain_cancelled"))
            self._reset_ui()
            self.run_view.set_finished(True)

    def _cancel_operation(self):
        """Cancel the current operation (transcription, a recipe run, or
        one of the five single-step re-run/retry jobs)."""
        if self.batch_panel._is_processing():
            self.batch_panel.cancel_processing()
            self.status_label.setText(tr("status_cancelled"))
            return

        if self._recipe_job is not None and self._recipe_job.isRunning():
            # _cancel_recipe_job() reports and resets on its own — the run
            # screen's per-step Cancel reaches it directly, without coming
            # through here.
            self._cancel_recipe_job()
            return

        if self._clean_job is not None and self._clean_job.isRunning():
            self._cancel_clean_job()
            self.status_label.setText(tr("status_ai_cancelled"))
        elif self._article_job is not None and self._article_job.isRunning():
            self._cancel_article_job()
            self.status_label.setText(tr("status_ai_cancelled"))
        elif self._insights_job is not None and self._insights_job.isRunning():
            self._cancel_insights_job()
            self.status_label.setText(tr("status_ai_cancelled"))
        elif self._youtube_job is not None and self._youtube_job.isRunning():
            self._cancel_youtube_job()
            self.status_label.setText(tr("status_ai_cancelled"))
        elif self._book_job is not None and self._book_job.isRunning():
            self._cancel_book_job()
            self.status_label.setText(tr("status_ai_cancelled"))
        else:
            self.transcriber.cancel()
            self.status_label.setText(tr("status_cancelled"))

        self._reset_ui()

    def _start_batch_processing(self):
        """Start batch processing with current settings."""
        model = self.model_combo.currentData()
        language = self.language_combo.currentData()
        translate = self.translate_checkbox.isChecked()
        perf_mode = self.perf_combo.currentData()
        n_threads = get_thread_count(perf_mode)
        enable_diarization = self.diarization_checkbox.isChecked()

        # Downloader dialogs belong to the GUI thread.  BatchWorker only
        # transcribes already prepared models.
        if not Transcriber.prepare_models(model, enable_diarization):
            self.batch_panel._on_batch_finished()
            self.status_label.setText(tr("status_cancelled"))
            return

        self.status_label.setText(tr("status_batch_starting"))
        self.status_bar.set_operation(
            tr("status_batch_starting"), cancel_text=tr("btn_cancel")
        )

        self.batch_panel.start_processing(
            model_name=model,
            language=language,
            translate=translate,
            n_threads=n_threads,
            enable_diarization=enable_diarization,
            num_speakers=None,
            use_gpu=self._use_gpu,
        )

    def _on_progress(self, percentage: int, message: str):
        """Handle progress updates with ETA calculation."""
        self.progress_bar.setValue(percentage)
        stage, local_fill = timeline_stage_for_progress(percentage, message)
        self.progress_timeline.set_stage(stage, local_fill)
        message = localized_progress(message)
        if percentage > 5 and self._transcription_start > 0:
            elapsed = time.monotonic() - self._transcription_start
            eta_str = format_eta(elapsed, percentage)
            self.status_label.setText(f"{message}  ·  {eta_str}")
        else:
            self.status_label.setText(message)

    def _on_finished(
        self,
        result: TranscriptionResult,
        open_record: bool = True,
        save_history: bool = True,
    ):
        """Handle transcription completion."""
        self.transcript_view.set_result(result)
        self._reset_ui()

        elapsed = time.monotonic() - self._transcription_start if self._transcription_start else 0
        word_count = len(result.full_text.split())
        self.status_label.setText(tr("status_complete", words=word_count, seconds=int(elapsed)))
        show_toast(self, tr("toast_complete", words=word_count), kind="success")

        # Auto-save to history
        if save_history:
            self._save_to_history(
                result,
                source_path=self._source_filepath or "",
                model=self.model_combo.currentData() or "",
                speaker_names=getattr(self.transcript_view, "_speaker_names", {}),
            )

        # Feed the result to every registered consumer (chat, insights,
        # YouTube, Cover, Cut, AI/Book enable flags — see DocumentSession).
        self._document_session.apply_result(result)
        # _last_record_id is finalized by now regardless of path (set
        # directly above via _save_to_history, or by the live-checkpoint
        # branches in _on_live_finished before it calls this method).
        self.cover_view.set_provenance(self._last_record_id, self._source_filepath)
        self.article_view.set_provenance(
            self._last_record_id, self._source_filepath,
            result.segments, result.language,
        )
        self.youtube_panel.set_provenance(self._last_record_id, self._source_filepath)
        self.insights_panel.set_provenance(self._last_record_id, self._source_filepath)
        if self._source_filepath:
            self.youtube_panel.set_source_name(Path(self._source_filepath).stem)
            self.insights_panel.set_source_name(Path(self._source_filepath).stem)
        elif self._source_kind == "live":
            self.youtube_panel.set_source_name(self._live_checkpoint.source_name)
            self.insights_panel.set_source_name(self._live_checkpoint.source_name)

        # Cut tab's video actions depend on source media, not the result
        # content — cut_view.set_result() itself already ran via
        # apply_result() above.
        self.cut_view.video_panel.set_has_transcript(bool(self._source_filepath))
        self.main_tabs.setCurrentIndex(0)

        # A fresh transcription result is a record too — open the Record
        # view so the user immediately sees what they just produced.
        title = (
            Path(self._source_filepath).stem if self._source_filepath
            else self._live_checkpoint.source_name if self._source_kind == "live" else tr("app_title")
        )
        self.record_view.set_title(title)
        self.record_view.set_has_result(True)

        self._run_recipe(result, show_run_screen=open_record)

    _STEP_TO_ARTIFACT_TYPE = {
        "article": "article",
        "insights": "insights",
        "youtube_package": "youtube",
        "book": "book",
    }

    # Reverse of the mapping above (B7) — which main_tabs page a materials
    # search hit's artifact type opens on. Attribute names, resolved via
    # getattr() in _open_record_view() since main_tabs is built in
    # _setup_ui(), after this class body is read.
    _ARTIFACT_TYPE_TO_TAB = {
        "article": "article_view",
        "insights": "insights_panel",
        "youtube": "youtube_panel",
        "book": "book_panel",
    }

    def _resolve_recipe(self, key: str) -> Recipe:
        """Look up *key* among the five built-ins first, then Config.recipes'
        saved custom recipes (B4, docs/IMPROVEMENT_PLAN_2026-08.ru.md —
        see ui/recipe_editor.py), matched by name; falls back to
        transcript-only for an unknown/missing key so a stale
        Config.last_recipe never breaks a launch. ``key == "custom"``
        matches the very first entry regardless of its own name — the
        pre-B4 single-slot format's saved name, kept for old configs."""
        builtin = BUILTIN_RECIPES_BY_KEY.get(key)
        if builtin is not None:
            return builtin
        for entry in get_config().recipes:
            recipe = Recipe.from_dict(entry)
            if key == "custom" or recipe.name == key:
                return recipe
        return TRANSCRIPT_ONLY

    @staticmethod
    def _unique_recipe_name(base: str, existing_names: "set[str]") -> str:
        """*base* if it's free, otherwise "*base* (2)", "(3)", … — what
        "Save as new" (B4) disambiguates a colliding name with instead of
        silently overwriting an unrelated recipe that happens to share
        the default-name text."""
        if base not in existing_names:
            return base
        n = 2
        while f"{base} ({n})" in existing_names:
            n += 1
        return f"{base} ({n})"

    def _recipe_get_result(self, run: JobRun, name: str):
        """``StepContext.get_result`` for the recipe run: a finished
        step's real return value, or — for a step JobEngine resolved via
        cache-skip (``StepOutcome.result`` is ``None`` on a SKIPPED
        outcome, since the runner that would have produced it never ran)
        — its artifact reloaded from disk (see
        application/steps.py::load_step_result and
        docs/IMPROVEMENT_PLAN_2026-08.ru.md, B1). Without this, a step
        that reads a cache-skipped dependency (e.g. "article" reading
        "clean") would see None and treat it as though that dependency
        had never run at all.
        """
        outcome = run.outcomes.get(name)
        if outcome is None:
            return None
        if outcome.result is not None:
            return outcome.result
        return load_step_result(self._recipe_context, name)

    def _open_recipe_editor(self) -> None:
        """"Настроить…"/"Изменить" on the start screen (see
        ui/recipe_editor.py). transcribe_options is borrowed by the
        dialog for the duration of exec() and must be reparented back out
        before the dialog is discarded, or Qt would destroy it along with
        the dialog's other children.

        Save/Save as new/Delete (B4, docs/IMPROVEMENT_PLAN_2026-08.ru.md)
        replaced the old single unnamed "custom" slot: a recipe is
        identified by its own name, "Save" upserts Config.recipes by that
        name (creating it the first time, updating it in place after),
        "Save as new" always inserts a fresh entry (disambiguating a
        colliding name via _unique_recipe_name), and "Delete" removes the
        matching entry. Cancel leaves Config.recipes untouched and the
        original chip checked.
        """
        original_key = self.start_view.current_recipe_key()
        recipe = self._resolve_recipe(original_key)
        cfg = get_config()
        existing_names = {entry.get("name", "") for entry in cfg.recipes}
        # Re-check downloaded state (B10) every time the editor opens —
        # this combo is a single long-lived widget, not rebuilt just by
        # showing the dialog, so a model downloaded since the last time
        # it was open would otherwise still read "will download".
        self.transcribe_options.refresh_model_state()
        dialog = RecipeEditorDialog(self.transcribe_options, recipe, existing_names, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        action = dialog.result_action if accepted else None
        steps = dialog.selected_steps() if accepted else None
        name = dialog.recipe_name() if accepted else None
        params = dialog.recipe_params() if accepted else None
        self.transcribe_options.setParent(None)
        dialog.deleteLater()

        if action == "delete":
            cfg.recipes = [e for e in cfg.recipes if e.get("name") != recipe.name]
            if cfg.last_recipe == recipe.name:
                cfg.last_recipe = TRANSCRIPT_ONLY.builtin_key
            save_config()
            self.start_view.refresh_recipe_chips()
            self.library_view.refresh_recipe_filters()
        elif action in ("save", "save_as_new"):
            final_name = (
                name if action == "save" else self._unique_recipe_name(name, existing_names)
            )
            edited = Recipe(name=final_name, steps=steps, builtin_key="", params=params or {})
            cfg.recipes = [
                e for e in cfg.recipes if e.get("name") != final_name
            ] + [edited.to_dict()]
            cfg.last_recipe = final_name
            save_config()
            self.start_view.refresh_recipe_chips()
            self.library_view.refresh_recipe_filters()
        else:
            self.start_view.set_recipe(original_key)
        self.start_view.refresh_summary()

    def _run_recipe(self, result: TranscriptionResult, show_run_screen: bool = True) -> None:
        """After a fresh transcription, run the rest of the selected
        recipe's steps as one JobRunner (see
        docs/UI_REDESIGN_PLAN_2026-09.ru.md, B6) — replaces the old
        preset chain's one-button-click-per-step orchestration now that
        every generator is a job-engine step (B5). *show_run_screen* is
        False for a live-finished/batch result (_on_finished's own
        open_record=False callers): the run still executes, it just
        doesn't yank the view away from wherever the user already is."""
        from core.ai_provider import provider_from_config
        from core.paths import artifact_dir
        from utils import language_name_for_code

        recipe = self._resolve_recipe(self.start_view.current_recipe_key())
        spec = recipe.to_job_spec(build_job_spec)
        step_names = tuple(step.name for step in spec.steps)

        # A live session (B7, docs/UI_REDESIGN_PLAN_2026-09.ru.md) already
        # produced this result by streaming, not by running the job-engine
        # "transcribe"/"diarize" steps — SKIPPED (not SUCCEEDED) is the
        # honest status for a step this run never actually executed, and
        # matches how a real cache-skip renders on the run screen.
        already_streamed = self._source_kind == "live"
        transcribe_status = StepStatus.SKIPPED if already_streamed else StepStatus.SUCCEEDED
        run = JobRun(spec=spec)
        if "transcribe" in step_names:
            run.outcomes["transcribe"] = StepOutcome(
                "transcribe", transcribe_status, result=result
            )
        if "diarize" in step_names and any(seg.speaker for seg in result.segments):
            run.outcomes["diarize"] = StepOutcome(
                "diarize", transcribe_status, result=result
            )

        cfg = get_config()
        provider = provider_from_config(cfg)
        record_id = self._last_record_id if self._last_record_id is not None else "unsaved"
        stem = Path(self._source_filepath).stem if self._source_filepath else "recording"

        self._recipe_run = run
        self._recipe_spec = spec
        self._recipe_step_names = step_names
        self._recipe_context = StepContext(
            source_path=self._source_filepath or "",
            result=result,
            record_id=record_id,
            artifact_dir=artifact_dir(record_id, self._source_filepath or stem),
            params={
                "lm_url": cfg.lm_studio_url,
                "language": language_name_for_code(result.language),
                "provider": None if provider.kind == "lmstudio" else provider,
                # Shared with InsightsPanel/YouTubePanel so a type more
                # than one step generates (e.g. "chapters") isn't
                # recomputed — see core/insights_cache.py.
                "insights_cache": self._insights_cache,
                "do_unwrap": self.book_panel.chk_unwrap.isChecked(),
                "do_custom": self.book_panel.chk_custom.isChecked(),
                "custom_prompt_path": self.book_panel.custom_prompt_edit.text().strip(),
                # The "YouTube video" recipe includes the cover step, so it
                # has to render what the Cover workspace is actually set to
                # rather than _cover_runner's own fallback defaults.
                **self.cover_view.render_params(),
            },
            get_result=lambda name: self._recipe_get_result(run, name),
            is_cancelled=run.is_cancelled,
        )

        self._recipe_run_id = None
        self.run_view.bind_run(run)
        self.run_view.set_recipe_name(recipe_label(recipe))
        self.run_view.set_finished(False)
        if show_run_screen:
            self._stack.setCurrentIndex(self._run_index)
        self._save_recipe_run("running")
        self._launch_recipe_job()

    def _resume_run(self, record_id: int) -> None:
        """LibraryView.resume_run (B2, docs/IMPROVEMENT_PLAN_2026-08.ru.md):
        pick up a run that stopped short — failed, or was interrupted by
        a crash (run_store.mark_stale_running_as_interrupted) — from
        wherever it left off.

        Needs B1: a restored StepOutcome's result is always None (see
        run_store's own module docstring), so a dependent step reads a
        SUCCEEDED/SKIPPED predecessor's real output from disk via
        load_step_result(), exactly like a cache-skipped step already
        does. Resumes by the run's *saved* step composition (its own
        recipe, resolved by name) rather than assuming nothing changed —
        the recipe could have been edited since (B4 makes that easy) or
        deleted outright, in which case _resolve_recipe() already falls
        back to transcript-only and the mismatch note below explains why
        the run screen looks different from what actually ran.
        """
        from application import run_store
        from core.ai_provider import provider_from_config
        from core.paths import artifact_dir
        from utils import language_name_for_code

        if not self._load_from_history(record_id):
            return
        stored = run_store.load_latest_run(record_id)
        if stored is None:
            return
        result = self._current_result
        if result is None:
            return

        recipe = self._resolve_recipe(stored.recipe)
        spec = build_job_spec(stored.recipe, recipe.steps)
        step_names = tuple(step.name for step in spec.steps)
        run = JobRun(spec=spec)
        run_store.apply_stored_outcomes(run, stored)

        cfg = get_config()
        provider = provider_from_config(cfg)
        stem = Path(self._source_filepath).stem if self._source_filepath else "recording"

        self._recipe_run = run
        self._recipe_spec = spec
        self._recipe_step_names = step_names
        self._recipe_context = StepContext(
            source_path=self._source_filepath or "",
            result=result,
            record_id=record_id,
            artifact_dir=artifact_dir(record_id, self._source_filepath or stem),
            params={
                "lm_url": cfg.lm_studio_url,
                "language": language_name_for_code(result.language),
                "provider": None if provider.kind == "lmstudio" else provider,
                "insights_cache": self._insights_cache,
                "do_unwrap": self.book_panel.chk_unwrap.isChecked(),
                "do_custom": self.book_panel.chk_custom.isChecked(),
                "custom_prompt_path": self.book_panel.custom_prompt_edit.text().strip(),
                **self.cover_view.render_params(),
            },
            get_result=lambda name: self._recipe_get_result(run, name),
            is_cancelled=run.is_cancelled,
        )

        self._recipe_run_id = stored.id
        self.run_view.bind_run(run)
        name = recipe_label(recipe)
        if set(stored.outcomes) - set(recipe.steps):
            name = tr("run_resumed_mismatch", name=name)
        self.run_view.set_recipe_name(name)
        self.run_view.set_finished(False)
        self._stack.setCurrentIndex(self._run_index)
        # JobEngine never re-resolves a step already in run.outcomes, so
        # _launch_recipe_job() below won't fire step_finished for any of
        # these restored SUCCEEDED/SKIPPED outcomes — without this loop
        # their tabs (Clean, Article, ...) would stay empty until the
        # user happened to trigger some other refresh.
        for step_name, outcome in run.outcomes.items():
            if outcome.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED):
                self._on_recipe_step_finished(step_name, outcome)
        self._save_recipe_run("running")
        self._launch_recipe_job()

    def _save_recipe_run(self, status: str) -> None:
        """Persist self._recipe_run's current outcomes to job_runs (B8,
        see application/run_store.py) — a no-op until there's a real
        history record to attach the run to. Reuses self._recipe_run_id
        across calls so this updates one row instead of inserting a new
        one for every step/retry."""
        if self._last_record_id is None or self._recipe_run is None or self._recipe_spec is None:
            return
        from application import run_store

        try:
            self._recipe_run_id = run_store.save_run(
                self._last_record_id, self._recipe_spec.name, self._recipe_run,
                run_id=self._recipe_run_id, status=status,
            )
        except Exception as exc:
            logger.warning("Failed to persist recipe run: %s", exc)

    def _launch_recipe_job(self) -> None:
        """(Re)start the current recipe run's JobRunner against whatever
        steps ``self._recipe_run`` doesn't already have an outcome for —
        used both by _run_recipe() and by _on_recipe_retry() below, since
        JobEngine.run() only (re)runs steps missing from run_state.outcomes
        (see application/job_engine.py)."""
        self._recipe_job = JobRunner(self._recipe_spec, run_state=self._recipe_run)
        runners = build_runners(
            self._recipe_context, self._recipe_step_names,
            progress_factory=self._recipe_job.make_progress_callback,
        )
        cache_checks = build_cache_checks(self._recipe_context, self._recipe_step_names)
        self._recipe_job.set_runners(runners, cache_checks=cache_checks)
        self._recipe_job.step_started.connect(self.run_view.on_step_started)
        self._recipe_job.step_progress.connect(self.run_view.on_step_progress)
        self._recipe_job.step_finished.connect(self.run_view.on_step_finished)
        self._recipe_job.step_finished.connect(self._on_recipe_step_finished)
        self._recipe_job.job_finished.connect(self._on_recipe_job_finished)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setEnabled(False)
        self.status_label.setText(tr("status_chain_running"))
        self._recipe_job.start()

    def _on_recipe_retry(self, name: str) -> None:
        """RunView.retry_requested: it has already reset *name* (and any
        dependent step only CANCELLED because of it) on the same JobRun
        we're about to reuse — see RunView._on_retry."""
        if self._recipe_context is None or self._recipe_run is None:
            return
        if self._recipe_job is not None and self._recipe_job.isRunning():
            return
        if self._recipe_run.is_cancelled():
            self._recipe_run = self._run_after_cancel()
        self._save_recipe_run("running")
        self._launch_recipe_job()

    def _on_recipe_overall_progress(self, percent: int) -> None:
        """RunView.overall_progress_changed (B3): mirrors the run screen's
        own N-of-M bar into the persistent status bar, so the overall
        percentage is visible without switching to the run screen. Guarded
        on an actually-running recipe job so a RunView recompute from
        something else (a fixture bind, a retried/regenerated step's
        reset) never overwrites an unrelated status-bar operation."""
        if self._recipe_job is None or not self._recipe_job.isRunning():
            return
        self.status_bar.set_operation(tr("status_chain_running"), progress=percent)

    def _on_recipe_regenerate(self, name: str) -> None:
        """RunView.regenerate_requested: same reused-run mechanics as
        _on_recipe_retry, plus deleting *name*'s on-disk manifest first —
        RunView already reset the step's outcome, but a still-valid cache
        would otherwise just re-mark it SKIPPED with the same old result
        (B1's "forced regeneration": revision/hash/prompt-version already
        invalidate the cache on their own; wanting a different result from
        unchanged inputs is the one case only this manual action covers)."""
        if self._recipe_context is None or self._recipe_run is None:
            return
        if self._recipe_job is not None and self._recipe_job.isRunning():
            return
        manifest = manifest_path_for_step(self._recipe_context, name)
        if manifest is not None and manifest.exists():
            try:
                manifest.unlink()
            except OSError as exc:
                logger.warning("Failed to delete manifest for regenerate (%s): %s", name, exc)
        if self._recipe_run.is_cancelled():
            self._recipe_run = self._run_after_cancel()
        self._save_recipe_run("running")
        self._launch_recipe_job()

    def _run_after_cancel(self) -> JobRun:
        """A fresh JobRun carrying the cancelled one's outcomes forward.

        ``JobRun.cancel()`` latches a threading.Event that nothing clears,
        and every step resolves through it (``JobEngine._resolve_step``
        marks a step CANCELLED before running it, and each runner's
        ``StepContext.is_cancelled`` is bound to it) — so retrying a step
        on a run that was ever cancelled would immediately re-resolve it
        CANCELLED without running anything at all.

        Clearing the flag in place is not the fix: ``_cancel_recipe_job()``
        retires the JobRunner rather than blocking on it (see its
        docstring), so the cancelled worker may still be inside a step
        that is watching this very flag, and un-cancelling underneath it
        would let it resume and race the retry. A new JobRun leaves that
        worker with the old, still-cancelled one it already holds.
        """
        import dataclasses

        fresh = JobRun(spec=self._recipe_spec)
        fresh.outcomes.update(self._recipe_run.outcomes)
        self._recipe_context = dataclasses.replace(
            self._recipe_context,
            get_result=lambda name: self._recipe_get_result(fresh, name),
            is_cancelled=fresh.is_cancelled,
        )
        self.run_view.bind_run(fresh)
        return fresh

    def _on_recipe_step_finished(self, name: str, outcome: StepOutcome) -> None:
        """Feed a just-finished recipe step's result to whichever tab
        shows it. Mirrors the success branch of each single-step
        _on_*_job_finished (this recipe run and those five buttons' own
        jobs share application/steps.py's runners; only where the result
        lands differs) — transcribe/diarize/cover have no tab to push
        into, so they're left to the run screen's own row status.

        A SKIPPED step (cache hit — B1) never ran its runner, so
        outcome.result is None; so does every restored outcome a resumed
        run (B2) feeds through here, regardless of its own status —
        run_store's contract is that a StepOutcome read back from storage
        always has result=None (see application/run_store.py's module
        docstring). Either way, the tab still needs populating from the
        artifact already on disk via load_step_result(), the same
        reconstruction _recipe_get_result() uses to feed dependent steps.
        """
        self._save_recipe_run("running")
        if outcome.status not in (StepStatus.SUCCEEDED, StepStatus.SKIPPED):
            return
        result = outcome.result
        if result is None:
            result = load_step_result(self._recipe_context, name)
        if name == "clean":
            from text_processor import ProcessingResult
            if isinstance(result, ProcessingResult):
                self._cleaned_text = result.coherent.text
                self.cleaned_view.set_text(
                    result.coherent.text,
                    original_length=len(result.original),
                    removed_fillers=result.cleaned.removed_fillers,
                    paragraphs=len(result.coherent.paragraphs),
                )
        elif name == "article":
            from article_generator import GenerationResult
            if isinstance(result, GenerationResult):
                self.article_view.set_articles(result.articles)
        elif name == "insights":
            if isinstance(result, dict):
                self.insights_panel.set_result(result)
        elif name == "youtube_package":
            if isinstance(result, dict):
                self.youtube_panel.set_result(result)
        elif name == "book":
            from book_pipeline import BookResult
            if isinstance(result, BookResult) and result.final_text:
                self.cleaned_view.set_text(
                    result.final_text,
                    original_length=len(self._current_result.full_text) if self._current_result else 0,
                    removed_fillers=0,
                    paragraphs=result.final_text.count("\n\n") + 1,
                )

    def _on_recipe_job_finished(self, run: JobRun) -> None:
        """Persist the finished run (B8, job_runs — the Library card's run
        composition) and whichever steps actually produced an artifact to
        this record's history badges (mirroring the old preset chain's
        own bookkeeping), and report how many came out of it."""
        self._recipe_job = None
        self._reset_ui()
        self.run_view.set_finished(True)

        succeeded = {
            name for name, outcome in run.outcomes.items()
            if outcome.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED)
        }
        had_error = any(
            outcome.status is StepStatus.FAILED for outcome in run.outcomes.values()
        )
        self._save_recipe_run("failed" if had_error else "done")
        self.library_view.refresh()
        artifact_types = {
            self._STEP_TO_ARTIFACT_TYPE[name]
            for name in succeeded if name in self._STEP_TO_ARTIFACT_TYPE
        }
        if self._last_record_id is not None and artifact_types:
            try:
                from core.history import get_history_store
                store = get_history_store()
                current = store.get_record(self._last_record_id) or {}
                artifacts = {"transcript", *artifact_types, *current.get("artifacts", [])}
                store.set_artifacts(self._last_record_id, sorted(artifacts))
            except Exception as exc:
                logger.warning("Failed to persist recipe run artifacts: %s", exc)

        # _reset_ui() only hides the progress/cancel affordances — without
        # this the one persistent status line would keep reading "Running
        # the recipe…" long after the run ended, since no other call site
        # writes to it until the next operation starts.
        if had_error:
            self.status_label.setText(tr("status_chain_failed"))
        else:
            self.status_label.setText(
                tr("status_chain_done", count=len(artifact_types))
            )

        if had_error:
            show_toast(self, tr("toast_chain_error"), kind="error")
        elif artifact_types:
            show_toast(
                self, tr("toast_chain_done", count=len(artifact_types)), kind="success"
            )

    def _save_to_history(self, result: TranscriptionResult, source_path: str,
                         model: str, speaker_names: dict,
                         source_name: str | None = None):
        """Persist a result to history (if enabled). Remembers the new
        row id in self._last_record_id so a preset chain (Phase C.3) that
        runs afterward can attach its artifacts to the right record."""
        self._last_record_id = None
        cfg = get_config()
        if not getattr(cfg, "history_enabled", True):
            return
        try:
            from core.history import get_history_store
            store = get_history_store()
            self._last_record_id = store.add(
                result,
                source_path=source_path,
                model=model,
                speaker_names=speaker_names or {},
                source_kind=self._source_kind,
                source_name=source_name,
            )
            # First transcript version ("as transcribed" — B8,
            # docs/IMPROVEMENT_PLAN_2026-08.ru.md item 2), written
            # immediately rather than waiting for the manual-edit
            # debounce so a record that's never edited still has a
            # baseline version to restore to.
            store.save_current_revision(
                self._last_record_id, result, speaker_names or {},
                keep=get_config().transcript_revisions_kept,
            )
            self.library_view.refresh()
        except Exception as e:
            logger.warning("Failed to save history: %s", e)

    def _on_batch_item_finished(self, index: int, result):
        """Persist each completed batch item to history."""
        if result is None:
            return
        items = self.batch_panel.processor.items
        source_path = items[index].filepath if 0 <= index < len(items) else ""
        self._save_to_history(
            result,
            source_path=source_path,
            model=self.model_combo.currentData() or "",
            speaker_names=getattr(result, "speaker_names", {}) or {},
        )

    def _load_from_history(self, record_id: int) -> bool:
        """Restore a history record and its media context atomically.

        Returns ``False`` without changing the visible page for a missing or
        malformed row.  This prevents a Library click from exposing the
        previous record under a new title.
        """
        try:
            from core.history import get_history_store
            store = get_history_store()
            record = store.get_record(record_id)
            if record is None:
                return False
            payload = record["payload"]
            source_name = record["source_name"] or ""
            source_path = record["source_path"] or ""

            result = _result_from_payload(payload)
            self._last_record_id = record_id
            self._source_kind = record.get("source_kind", "file")
            # set_result honours result.speaker_names, restoring renames.
            self.transcript_view.set_result(result)

            # A history row may be transcript-only (Live) or point at media
            # that has since been moved/deleted.  Never leave the old media
            # selected in that case.
            has_media = bool(source_path and Path(source_path).is_file())
            if has_media:
                self.file_selector._set_file(source_path)
                # _set_file emits file_selected and updates the player.
            else:
                self.file_selector._clear_selection()
                self.player.load("")

            self._document_session.apply_result(result)
            self.cover_view.set_provenance(record_id, source_path or None)
            self.article_view.set_provenance(
                record_id, source_path or None, result.segments, result.language,
            )
            self.youtube_panel.set_provenance(record_id, source_path or None)
            self.insights_panel.set_provenance(record_id, source_path or None)
            stem = Path(source_path or source_name).stem if (source_path or source_name) else ""
            self.youtube_panel.set_source_name(stem)
            self.insights_panel.set_source_name(stem)
            self.cut_view.video_panel.set_has_transcript(has_media)
            word_count = len(result.full_text.split())
            self.status_label.setText(tr("toast_loaded_history", words=word_count))
            self.main_tabs.setCurrentIndex(0)
            self.record_view.set_title(Path(source_name).stem if source_name else tr("app_title"))
            self.record_view.set_has_result(True)
            return True
        except Exception as e:
            logger.warning("Failed to load history record %d: %s", record_id, e)
            return False

    def _on_error(self, error_message: str):
        """Handle transcription error."""
        self._reset_ui()
        self.status_label.setText(tr("status_error", error=f"{error_message[:50]}..."))

        QMessageBox.critical(
            self,
            tr("error_transcription"),
            tr("error_occurred", detail=error_message),
        )

    def _reset_ui(self):
        """Reset UI to ready state — unless a recipe run is still going
        (see _run_recipe), in which case Cancel must stay reachable and
        Process must stay disabled so the user can't start a second
        transcription mid-run."""
        run_active = bool(self._recipe_job is not None and self._recipe_job.isRunning())
        self.start_view.set_process_enabled(
            not run_active and self.file_selector.get_file() is not None
        )
        self.transcribe_btn.setVisible(True)
        self.cancel_btn.setVisible(run_active)
        self.progress_bar.setVisible(False)
        self.progress_timeline.setVisible(False)

    def _copy_to_clipboard(self):
        """Copy transcription to clipboard."""
        text = self.transcript_view.get_text()
        if text:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            show_toast(self, tr("toast_copied"), kind="success")

    def _export_result(self):
        """Export the transcription result using the formats currently
        checked in the Record view's Export menu.

        The actual export work (what succeeded, what failed) is delegated
        to application/export_controller.py; this method only owns the
        file dialog / message box / toast presentation around it.
        """
        result = self.transcript_view.get_result()
        if not result:
            return

        format_keys = self.record_view.get_export_formats()
        source_file = self.file_selector.get_file() or "transcript"
        default_name = os.path.splitext(os.path.basename(source_file))[0]

        if len(format_keys) == 1:
            # Single format
            format_key = format_keys[0]
            format_name, _ = EXPORT_FORMATS[format_key]
            ext = export_controller.format_extension(format_key)
            filepath, _ = QFileDialog.getSaveFileName(
                self, f"Export as {format_name}", f"{default_name}.{ext}",
                f"{format_name} (*.{ext});;All Files (*)"
            )

            if filepath:
                try:
                    export_controller.export_single(result, filepath, format_key)
                    show_toast(self, tr("toast_exported_one", name=os.path.basename(filepath)), kind="success")
                except Exception as e:
                    QMessageBox.critical(self, tr("error_export"), str(e))
        else:
            # Multiple formats - directory
            directory = QFileDialog.getExistingDirectory(self, "Select Export Directory")
            if directory:
                outcome = export_controller.export_many_to_directory(
                    result, directory, format_keys, default_name
                )
                if outcome.any_succeeded:
                    show_toast(
                        self,
                        tr("toast_exported_many", count=len(outcome.succeeded)),
                        kind="success" if not outcome.any_failed else "warning",
                    )
                if outcome.any_failed:
                    QMessageBox.warning(
                        self,
                        tr("error_export"),
                        "Failed formats: " + ", ".join(outcome.failed),
                    )

    def _export_preset(self, preset_key: str) -> None:
        """B9, docs/IMPROVEMENT_PLAN_2026-08.ru.md: collect an export
        preset's formats and generated materials into one folder — the
        actual collection is delegated to
        application/export_controller.py::export_preset(); this method
        only owns the directory dialog and the resulting toast."""
        result = self.transcript_view.get_result()
        if not result:
            return
        from domain.export_preset import BUILTIN_EXPORT_PRESETS_BY_KEY
        preset = BUILTIN_EXPORT_PRESETS_BY_KEY.get(preset_key)
        if preset is None:
            return

        directory = QFileDialog.getExistingDirectory(self, "Select Export Directory")
        if not directory:
            return

        source_file = self.file_selector.get_file() or "transcript"
        default_name = os.path.splitext(os.path.basename(source_file))[0]
        record_id = self._last_record_id if self._last_record_id is not None else "unsaved"

        outcome = export_controller.export_preset(
            result, preset, directory, record_id,
            source_path=self._source_filepath or "", default_name=default_name,
        )
        if outcome.any_missing:
            show_toast(
                self,
                tr(
                    "toast_export_preset_partial",
                    count=outcome.total_files, missing=len(outcome.materials_missing),
                ),
                kind="warning",
            )
        else:
            show_toast(
                self,
                tr("toast_export_preset_done", count=outcome.total_files, dir=directory),
                kind="success",
            )

    # ===== AI Processing Methods =====

    def _get_text_for_ai(self) -> str | None:
        """Get text to use for AI processing (cleaned if available, else raw)."""
        if self._cleaned_text:
            return self._cleaned_text
        if self._current_result:
            return self._current_result.full_text
        return None

    def _start_text_cleaning(self):
        """Start text cleaning — routed through application/steps.py's
        "clean" step via JobRunner (see docs/UI_REDESIGN_PLAN_2026-09.ru.md,
        B5a) instead of the generic AIProcessingWorker, so its output gets
        full provenance the same way every other job-engine step does, and
        the run screen's "clean" row (when a JobRun is bound there)
        reflects this very run rather than a second, disconnected
        progress source."""
        if not self._current_result:
            self.status_label.setText(tr("status_no_transcription_to_clean"))
            return

        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)
        self.progress_timeline.setVisible(True)
        self.progress_timeline.set_stage(4, 0)   # Clean

        from core.paths import artifact_dir

        record_id = self._last_record_id if self._last_record_id is not None else "unsaved"
        stem = Path(self._source_filepath).stem if self._source_filepath else "recording"
        out_dir = artifact_dir(record_id, self._source_filepath or stem)

        spec = build_job_spec("clean-only", ("clean",))
        self._clean_job = JobRunner(spec)
        context = StepContext(
            source_path=self._source_filepath or "",
            result=self._current_result,
            record_id=record_id,
            artifact_dir=out_dir,
            params={"lm_url": get_config().lm_studio_url},
            is_cancelled=self._clean_job.run_state.is_cancelled,
        )
        runners = build_runners(
            context, ("clean",), progress_factory=self._clean_job.make_progress_callback
        )
        self._clean_job_context = context
        cache_checks = build_cache_checks(context, ("clean",))
        self._clean_job.set_runners(runners, cache_checks=cache_checks)
        self._clean_job.step_progress.connect(self._on_clean_progress)
        self._clean_job.job_finished.connect(self._on_clean_job_finished)
        self._clean_job.start()

    def _cancel_clean_job(self) -> None:
        """Cancel the running "clean" JobRunner, mirroring
        _cancel_recipe_job()'s non-blocking retire-through-the-registry
        pattern (see its docstring)."""
        if self._clean_job is not None and self._clean_job.isRunning():
            self._registry.retire(self._clean_job)
            self._clean_job = None

    def _start_generate_all(self):
        """Start generation of all article formats — routed through
        application/steps.py's "article" step via JobRunner (see
        docs/UI_REDESIGN_PLAN_2026-09.ru.md, B5b), the same migration
        B5a did for clean. This is a standalone one-step job (article
        alone, not run alongside clean in the same JobRun), so it
        replicates _get_text_for_ai()'s own "cleaned text if available,
        else raw" fallback via a get_result("clean") shim, rather than
        relying on the step registry's real clean->article dependency
        edge — that edge only matters once a caller actually runs both
        steps in one JobRun (B6's recipe-driven jobs)."""
        text = self._get_text_for_ai()
        if not text:
            self.status_label.setText(tr("status_no_text_to_process"))
            return

        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)
        self.progress_timeline.setVisible(True)
        self.progress_timeline.set_stage(5, 0)   # Generate

        from core.paths import artifact_dir

        record_id = self._last_record_id if self._last_record_id is not None else "unsaved"
        stem = Path(self._source_filepath).stem if self._source_filepath else "recording"
        out_dir = artifact_dir(record_id, self._source_filepath or stem)

        cleaned_stub = None
        if self._cleaned_text:
            from types import SimpleNamespace
            cleaned_stub = SimpleNamespace(
                coherent=SimpleNamespace(text=self._cleaned_text)
            )

        spec = build_job_spec("article-only", ("article",))
        self._article_job = JobRunner(spec)
        context = StepContext(
            source_path=self._source_filepath or "",
            result=self._current_result,
            record_id=record_id,
            artifact_dir=out_dir,
            params={"lm_url": get_config().lm_studio_url},
            get_result=lambda name: cleaned_stub if name == "clean" else None,
            is_cancelled=self._article_job.run_state.is_cancelled,
        )
        runners = build_runners(
            context, ("article",), progress_factory=self._article_job.make_progress_callback
        )
        self._article_job_context = context
        cache_checks = build_cache_checks(context, ("article",))
        self._article_job.set_runners(runners, cache_checks=cache_checks)
        self._article_job.step_progress.connect(self._on_article_progress)
        self._article_job.job_finished.connect(self._on_article_job_finished)
        self._article_job.start()

    def _cancel_article_job(self) -> None:
        """Cancel the running "article" JobRunner — see
        _cancel_clean_job()'s docstring for the retire-through-the-
        registry pattern this mirrors."""
        if self._article_job is not None and self._article_job.isRunning():
            self._registry.retire(self._article_job)
            self._article_job = None

    def _start_insights_job(self) -> None:
        """Start chapters/action items/key moments — routed through
        application/steps.py's "insights" step via JobRunner (see
        docs/UI_REDESIGN_PLAN_2026-09.ru.md, B5c), the same migration B5a
        did for clean. InsightsPanel no longer creates any worker itself;
        this is what both its own "Generate" button (via
        generate_requested) and the preset chain's insights step (see
        _start_next_extra_chain_step) now call.

        One behavior change from the three-separate-InsightsWorker path
        this replaces: the three insight types used to fail independently
        (one type's LM error left the other two displayed); the step
        generates all three in one pass, so a failure now fails the whole
        run. Matches every other already-migrated step's all-or-nothing
        shape (B5a/B5b) — see application/steps.py's _insights_runner.
        """
        if not self._current_result:
            return

        cfg = get_config()
        if not cfg.lm_studio_url:
            self.insights_panel.set_error(tr("insights_no_lm"))
            return

        self.insights_panel.begin_generating()
        self.cancel_btn.setVisible(True)

        from core.paths import artifact_dir
        from utils import language_name_for_code

        record_id = self._last_record_id if self._last_record_id is not None else "unsaved"
        stem = Path(self._source_filepath).stem if self._source_filepath else "recording"
        out_dir = artifact_dir(record_id, self._source_filepath or stem)

        spec = build_job_spec("insights-only", ("insights",))
        self._insights_job = JobRunner(spec)
        context = StepContext(
            source_path=self._source_filepath or "",
            result=self._current_result,
            record_id=record_id,
            artifact_dir=out_dir,
            params={
                "lm_url": cfg.lm_studio_url,
                "language": language_name_for_code(self._current_result.language),
                # Shared with YouTubePanel so a type both generate (e.g.
                # "chapters") isn't recomputed — see core/insights_cache.py.
                "insights_cache": self._insights_cache,
            },
            is_cancelled=self._insights_job.run_state.is_cancelled,
        )
        runners = build_runners(
            context, ("insights",), progress_factory=self._insights_job.make_progress_callback
        )
        self._insights_job_context = context
        cache_checks = build_cache_checks(context, ("insights",))
        self._insights_job.set_runners(runners, cache_checks=cache_checks)
        self._insights_job.step_progress.connect(self._on_insights_progress)
        self._insights_job.job_finished.connect(self._on_insights_job_finished)
        self._insights_job.start()

    def _cancel_insights_job(self) -> None:
        """Cancel the running "insights" JobRunner — see
        _cancel_clean_job()'s docstring for the retire-through-the-
        registry pattern this mirrors."""
        if self._insights_job is not None and self._insights_job.isRunning():
            self._registry.retire(self._insights_job)
            self._insights_job = None

    def _on_insights_progress(self, _name: str, percentage: int, message: str) -> None:
        """JobRunner.step_progress for the single-step "insights" job."""
        self.status_label.setText(message)

    def _on_insights_job_finished(self, run: JobRun) -> None:
        """JobRunner.job_finished for the single-step "insights" job
        started by _start_insights_job() — hands the outcome to the panel."""
        self._insights_job = None
        outcome = run.outcomes.get("insights")
        context = self._insights_job_context
        self._insights_job_context = None
        # _start_insights_job() showed the status bar's Cancel button;
        # nothing here used to hide it again, so it stayed on screen
        # offering to cancel a job that had already finished.
        self._reset_ui()

        result = outcome.result if outcome is not None else None
        if (
            result is None
            and outcome is not None
            and outcome.status is StepStatus.SKIPPED
            and context is not None
        ):
            result = load_step_result(context, "insights")

        if outcome is not None and outcome.status in (
            StepStatus.SUCCEEDED, StepStatus.SKIPPED
        ) and isinstance(result, dict):
            self.insights_panel.set_result(result)
            return

        # _cancel_insights_job() disconnects this very signal before the
        # run can reach a CANCELLED outcome (see _on_clean_job_finished's
        # matching comment), so the only other case reaching here is a
        # real failure.
        self.insights_panel.set_error(outcome.error if outcome is not None else "")

    def _start_youtube_job(self) -> None:
        """Start the YouTube package (chapters/titles/description/tags/
        questions) — routed through application/steps.py's
        "youtube_package" step via JobRunner (see
        docs/UI_REDESIGN_PLAN_2026-09.ru.md, B5d), the same migration
        B5a/B5b/B5c did for clean/article/insights. YouTubePanel no longer
        creates any worker itself; this is what both its own "Generate"
        button (via generate_requested) and the preset chain's youtube
        step (see _start_preset_chain) now call, same as generate()
        already did before B5d.

        The panel's language/provider combos stay put for now (moving
        them into a real recipe editor is B6's job) — this just reads
        their current selection instead of the panel resolving and
        starting workers with it itself."""
        if not self._current_result:
            return

        from core.ai_provider import provider_from_config

        cfg = get_config()
        # cfg.yt_provider already reflects the panel's combo — it's
        # persisted eagerly on every change by
        # YouTubePanel._on_provider_changed(), the same as before B5d.
        provider = provider_from_config(cfg)

        if provider.kind != "lmstudio" and not provider.api_key:
            self.youtube_panel.set_error(tr("youtube_no_api_key"))
            return
        if provider.kind == "lmstudio" and not cfg.lm_studio_url:
            self.youtube_panel.set_error(tr("youtube_no_lm"))
            return

        self.youtube_panel.begin_generating()
        self.cancel_btn.setVisible(True)

        from core.paths import artifact_dir
        from utils import language_name_for_code

        record_id = self._last_record_id if self._last_record_id is not None else "unsaved"
        stem = Path(self._source_filepath).stem if self._source_filepath else "recording"
        out_dir = artifact_dir(record_id, self._source_filepath or stem)

        # "Auto" (None) falls back to the transcript's own detected
        # language rather than sending no directive at all — an empty
        # directive left the model free to answer in whatever language it
        # defaulted to (usually English), even for a Russian transcript.
        lang = self.youtube_panel.selected_language() or language_name_for_code(
            self._current_result.language
        )

        spec = build_job_spec("youtube-only", ("youtube_package",))
        self._youtube_job = JobRunner(spec)
        context = StepContext(
            source_path=self._source_filepath or "",
            result=self._current_result,
            record_id=record_id,
            artifact_dir=out_dir,
            params={
                "lm_url": cfg.lm_studio_url,
                "language": lang,
                "provider": None if provider.kind == "lmstudio" else provider,
                # Shared with InsightsPanel so a type both generate (e.g.
                # "chapters") isn't recomputed — see core/insights_cache.py.
                "insights_cache": self._insights_cache,
            },
            is_cancelled=self._youtube_job.run_state.is_cancelled,
        )
        runners = build_runners(
            context, ("youtube_package",),
            progress_factory=self._youtube_job.make_progress_callback,
        )
        self._youtube_job_context = context
        cache_checks = build_cache_checks(context, ("youtube_package",))
        self._youtube_job.set_runners(runners, cache_checks=cache_checks)
        self._youtube_job.step_progress.connect(self._on_youtube_progress)
        self._youtube_job.job_finished.connect(self._on_youtube_job_finished)
        self._youtube_job.start()

    def _cancel_youtube_job(self) -> None:
        """Cancel the running "youtube_package" JobRunner — see
        _cancel_clean_job()'s docstring for the retire-through-the-
        registry pattern this mirrors."""
        if self._youtube_job is not None and self._youtube_job.isRunning():
            self._registry.retire(self._youtube_job)
            self._youtube_job = None

    def _on_youtube_progress(self, _name: str, percentage: int, message: str) -> None:
        """JobRunner.step_progress for the single-step "youtube_package" job."""
        self.status_label.setText(message)

    def _on_youtube_job_finished(self, run: JobRun) -> None:
        """JobRunner.job_finished for the single-step "youtube_package"
        job started by _start_youtube_job() — hands the outcome to the panel."""
        self._youtube_job = None
        outcome = run.outcomes.get("youtube_package")
        context = self._youtube_job_context
        self._youtube_job_context = None
        # Same as the insights job above: _start_youtube_job() showed the
        # Cancel button and nothing here took it back down.
        self._reset_ui()

        result = outcome.result if outcome is not None else None
        if (
            result is None
            and outcome is not None
            and outcome.status is StepStatus.SKIPPED
            and context is not None
        ):
            result = load_step_result(context, "youtube_package")

        if outcome is not None and outcome.status in (
            StepStatus.SUCCEEDED, StepStatus.SKIPPED
        ) and isinstance(result, dict):
            self.youtube_panel.set_result(result)
            return

        # _cancel_youtube_job() disconnects this very signal before the
        # run can reach a CANCELLED outcome (see _on_clean_job_finished's
        # matching comment), so the only other case reaching here is a
        # real failure.
        self.youtube_panel.set_error(outcome.error if outcome is not None else "")

    def _on_clean_progress(self, _name: str, percentage: int, message: str) -> None:
        """JobRunner.step_progress for the single-step "clean" job."""
        self.progress_timeline.set_progress(percentage)
        self.status_label.setText(message)

    def _on_clean_job_finished(self, run: JobRun) -> None:
        """JobRunner.job_finished for the single-step "clean" job started
        by _start_text_cleaning() — mirrors the success/error split the
        old AIProcessingWorker.finished/.error signals drove, including
        both preset-chain hooks (consume_auto_article/on_ai_error)."""
        from text_processor import ProcessingResult

        self._clean_job = None
        outcome = run.outcomes.get("clean")
        context = self._clean_job_context
        self._clean_job_context = None
        result = outcome.result if outcome is not None else None
        if (
            result is None
            and outcome is not None
            and outcome.status is StepStatus.SKIPPED
            and context is not None
        ):
            result = load_step_result(context, "clean")

        if (
            outcome is not None
            and outcome.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED)
            and isinstance(result, ProcessingResult)
        ):
            self._reset_ui()
            self._cleaned_text = result.coherent.text

            self.cleaned_view.set_text(
                result.coherent.text,
                original_length=len(result.original),
                removed_fillers=result.cleaned.removed_fillers,
                paragraphs=len(result.coherent.paragraphs)
            )

            # Switch to cleaned tab
            self.main_tabs.setCurrentIndex(1)

            self.status_label.setText(
                f"Cleaned in {result.processing_time:.1f}s - "
                f"removed {result.cleaned.removed_fillers} fillers"
            )
            return

        # _cancel_clean_job() disconnects this very signal before the run
        # can reach a CANCELLED outcome, so the only other case reaching
        # here is a real failure.
        error = outcome.error if outcome is not None else ""
        self._reset_ui()
        self.status_label.setText(
            tr("status_ai_error", error=f"{error[:50]}...")
        )
        QMessageBox.warning(
            self, tr("error_ai"), tr("error_occurred", detail=error),
        )

    def _on_article_progress(self, _name: str, percentage: int, message: str) -> None:
        """JobRunner.step_progress for the single-step "article" job."""
        self.progress_timeline.set_progress(percentage)
        self.status_label.setText(message)

    def _on_article_job_finished(self, run: JobRun) -> None:
        """JobRunner.job_finished for the single-step "article" job
        started by _start_generate_all() — mirrors the success/error split
        the old AIProcessingWorker.finished/.error signals drove for
        "generate_all", including both preset-chain hooks
        (on_generate_all_finished/on_ai_error)."""
        from article_generator import GenerationResult

        self._article_job = None
        outcome = run.outcomes.get("article")
        context = self._article_job_context
        self._article_job_context = None
        result = outcome.result if outcome is not None else None
        if (
            result is None
            and outcome is not None
            and outcome.status is StepStatus.SKIPPED
            and context is not None
        ):
            result = load_step_result(context, "article")

        if (
            outcome is not None
            and outcome.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED)
            and isinstance(result, GenerationResult)
        ):
            self._reset_ui()
            self.article_view.set_articles(result.articles)

            # Switch to articles tab
            self.main_tabs.setCurrentWidget(self.article_view)

            self.status_label.setText(tr(
                "status_articles_generated",
                count=len(result.articles),
                seconds=f"{result.generation_time:.1f}",
            ))
            return

        # _cancel_article_job() disconnects this very signal before the
        # run can reach a CANCELLED outcome (see _on_clean_job_finished's
        # matching comment), so the only other case reaching here is a
        # real failure.
        error = outcome.error if outcome is not None else ""
        self._reset_ui()
        self.status_label.setText(
            tr("status_ai_error", error=f"{error[:50]}...")
        )
        QMessageBox.warning(
            self, tr("error_ai"), tr("error_occurred", detail=error),
        )

    # ===== Video Pipeline Methods =====

    def _export_edl(self):
        """Export kept segments from the Cut view as a CMX3600 EDL file."""
        segs = self.cut_view.get_kept_segments()
        if not segs:
            self.status_label.setText(tr("status_no_segments"))
            return
        cfg = get_config()
        src = self._source_filepath or "clip"
        clip_name = os.path.basename(src)
        default_name = os.path.splitext(clip_name)[0] + ".edl"
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Export EDL", default_name, "EDL (*.edl);;All Files (*)"
        )
        if not filepath:
            return
        try:
            write_edl(
                segs, filepath,
                fps=cfg.video_fps,
                drop_frame=cfg.video_drop_frame,
                title=os.path.splitext(clip_name)[0],
                clip_name=clip_name,
            )
            show_toast(self, tr("toast_edl_exported", name=os.path.basename(filepath)), kind="success")
        except Exception as e:
            QMessageBox.critical(self, tr("error_export"), str(e))

    def _mark_pauses(self, threshold: float):
        """Run algorithmic pause/filler detection and uncheck matched segments."""
        if not self._current_result:
            return
        segs = self._current_result.segments
        indices = mark_pauses(segs, min_duration=threshold)
        self.cut_view.mark_indices(indices)
        logger.info("Mark pauses (threshold=%.2fs): %d segments marked", threshold, len(indices))

    def _assemble_draft(self):
        """Cut and concatenate kept segments into a draft MP4 via ffmpeg."""
        if self._draft_worker and self._draft_worker.isRunning():
            return
        src = getattr(self, "_source_filepath", None)
        if not src:
            return
        segs = self.cut_view.get_kept_segments()
        if not segs:
            self.status_label.setText(tr("status_no_segments"))
            return
        base = os.path.splitext(os.path.basename(src))[0]
        default_name = base + "_draft.mp4"
        out_path, _ = QFileDialog.getSaveFileName(
            self, tr("video_assemble_draft"), default_name, "MP4 (*.mp4);;All Files (*)"
        )
        if not out_path:
            return
        show_toast(self, tr("toast_assembling"), kind="info")
        self.status_bar.set_operation(tr("toast_assembling"))
        self._draft_worker = DraftAssemblyWorker(src, segs, out_path, self)
        self._draft_worker.progress.connect(self.status_label.setText)
        self._draft_worker.assembled.connect(self._on_draft_assembled)
        self._draft_worker.error.connect(self._on_draft_assembly_error)
        self._draft_worker.start()

    def _on_draft_assembled(self, output_path: str) -> None:
        self._draft_worker = None
        self.status_bar.clear()
        show_toast(self, tr("toast_assembled", name=os.path.basename(output_path)), kind="success")

    def _on_draft_assembly_error(self, error: str) -> None:
        self._draft_worker = None
        self.status_bar.clear()
        show_toast(self, tr("toast_assemble_error", detail=error[:80]), kind="error")

    # ===== Book Pipeline Methods =====

    def _start_book_job(self, do_unwrap: bool, do_custom: bool, custom_prompt_path: str):
        """Start book pipeline processing for the current transcript —
        routed through application/steps.py's "book" step via JobRunner
        (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B5e), the same migration
        B5a did for clean. BookPanel never created its own worker for a
        single-file run (only its now-hidden folder-batch section did,
        untouched here) — run_single_requested already just asked
        MainWindow to do it, so this replaces _on_book_run() itself
        one-for-one, called from the same two places: the panel's Run
        button and the preset chain's book step
        (_start_next_extra_chain_step).

        Stage output moves from next to the source file (BookPipeline's
        own default when no output_dir is given, which the legacy
        AIProcessingWorker path relied on) to this record's artifact_dir
        — matches every other already-migrated generator's convention
        (application/steps.py's _book_runner passes output_dir itself).
        Per-stage provenance manifests (with an incomplete provider/model/
        prompt_version) stop being written; only the one wrapper book.md
        file gets a complete manifest now — already true since B0, this
        phase just starts actually exercising it.
        """
        if not self._current_result:
            self.status_label.setText(tr("status_no_transcript_for_book"))
            return

        self.book_panel.set_processing(True)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)

        from core.paths import artifact_dir

        record_id = self._last_record_id if self._last_record_id is not None else "unsaved"
        stem = Path(self._source_filepath).stem if self._source_filepath else "recording"
        out_dir = artifact_dir(record_id, self._source_filepath or stem)

        spec = build_job_spec("book-only", ("book",))
        self._book_job = JobRunner(spec)
        context = StepContext(
            source_path=self._source_filepath or "transcript",
            result=self._current_result,
            record_id=record_id,
            artifact_dir=out_dir,
            params={
                "do_unwrap": do_unwrap,
                "do_custom": do_custom,
                "custom_prompt_path": custom_prompt_path,
            },
            is_cancelled=self._book_job.run_state.is_cancelled,
        )
        runners = build_runners(
            context, ("book",), progress_factory=self._book_job.make_progress_callback
        )
        self._book_job_context = context
        cache_checks = build_cache_checks(context, ("book",))
        self._book_job.set_runners(runners, cache_checks=cache_checks)
        self._book_job.step_progress.connect(self._on_book_progress)
        self._book_job.job_finished.connect(self._on_book_job_finished)
        self._book_job.start()

    def _cancel_book_job(self) -> None:
        """Cancel the running "book" JobRunner — see _cancel_clean_job()'s
        docstring for the retire-through-the-registry pattern this
        mirrors."""
        if self._book_job is not None and self._book_job.isRunning():
            self._registry.retire(self._book_job)
            self._book_job = None

    def _on_book_progress(self, _name: str, percentage: int, message: str) -> None:
        """JobRunner.step_progress for the single-step "book" job."""
        self.book_panel.update_progress(percentage, message)
        self.status_label.setText(message)

    def _on_book_job_finished(self, run: JobRun) -> None:
        """JobRunner.job_finished for the single-step "book" job started
        by _start_book_job() — mirrors the success/error split the old
        AIProcessingWorker.finished/.error signals drove."""
        from book_pipeline import BookResult

        self.book_panel.set_processing(False)
        self._reset_ui()
        self._book_job = None
        outcome = run.outcomes.get("book")
        context = self._book_job_context
        self._book_job_context = None

        result = outcome.result if outcome is not None else None
        if (
            result is None
            and outcome is not None
            and outcome.status is StepStatus.SKIPPED
            and context is not None
        ):
            result = load_step_result(context, "book")

        if outcome is not None and outcome.status in (
            StepStatus.SUCCEEDED, StepStatus.SKIPPED
        ) and isinstance(result, BookResult):
            if result.stages:
                saved_paths = [s.output_path for s in result.stages if s.success and s.output_path]
                if saved_paths:
                    files = ", ".join(os.path.basename(p) for p in saved_paths)
                    self.status_label.setText(tr("status_book_saved", files=files))
                    # Show result in Cleaned tab
                    if result.final_text:
                        self.cleaned_view.set_text(
                            result.final_text,
                            original_length=len(self._current_result.full_text) if self._current_result else 0,
                            removed_fillers=0,
                            paragraphs=result.final_text.count('\n\n') + 1,
                        )
                        self.main_tabs.setCurrentIndex(1)
                else:
                    # A stage failed internally (caught by BookPipeline
                    # itself, not an exception) — status text only, same
                    # as a fully successful run; no popup either way.
                    failed = [s.error for s in result.stages if not s.success]
                    error = failed[0] if failed else tr("error_unknown")
                    self.status_label.setText(tr("status_book_error", error=error))
            else:
                self.status_label.setText(tr("status_book_pipeline_done"))
        else:
            # _cancel_book_job() disconnects this very signal before the
            # run can reach a CANCELLED outcome (see
            # _on_clean_job_finished's matching comment), so the only
            # other case reaching here is the pipeline itself raising
            # (not a per-stage failure — that's the "succeeded" branch
            # above, since BookPipeline catches those itself).
            error_message = outcome.error if outcome is not None else ""
            self.status_label.setText(tr("status_book_error", error=error_message[:60]))
            QMessageBox.warning(
                self,
                tr("error_book_pipeline_title"),
                tr("error_occurred", detail=error_message),
            )
