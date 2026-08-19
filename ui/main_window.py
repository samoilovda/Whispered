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
    QLabel, QFileDialog, QMessageBox,
    QApplication, QTabWidget, QComboBox,
    QTextEdit, QLineEdit, QPlainTextEdit, QStackedWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut, QDragEnterEvent, QDropEvent

from ui.toast import show_toast
from ui.library_view import LibraryView
from ui.record_view import RecordView
from ui.inspector_rail import InspectorRail
from ui.workspace_shell import WorkspaceShell
from ui.draft_record import DraftRecord
from ui.status_bar import StatusBar
from ui.step_checklist import StepChecklist
from ui.transcribe_options import TranscribeOptionsPopover
from ui.command_palette import CommandPalette
from ui.file_selector import FileSelector
from ui.transcript_view import TranscriptView
from ui.ai_panel import AIProcessingPanel
from ui.article_view import ArticleView, CleanedTextView
from ui.batch_panel import BatchPanel
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
from ui.preset_chain_controller import PresetChainController
from ui.shutdownable import Shutdownable
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
from config import get_config
from core.ai_worker import AIProcessingWorker
from core.insights_cache import InsightsCache
from core.base_worker import BaseWorker
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

    ``_ai_worker``, ``_gpu_worker`` and ``_live_preflight_worker`` are not
    persistent objects like the panels — they are created per-operation,
    set back to ``None`` when idle, and sometimes replaced while running.
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
        self._current_result: TranscriptionResult | None = None
        self._cleaned_text: str | None = None
        self._ai_worker: AIProcessingWorker | None = None
        self._gpu_worker: GPUDetectionWorker | None = None
        self._draft_worker: DraftAssemblyWorker | None = None
        self._shutdownables.append(_WorkerShutdown(self, "_ai_worker"))
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
        # Preset chain state machine (see _start_preset_chain / Phase C.3
        # and ui/preset_chain_controller.py).
        self._preset_chain = PresetChainController(self)
        self._preset_chain.finished.connect(self._finish_preset_chain)
        self._chain_extra_steps: list[str] = []
        self._chain_extra_ran: set[str] = set()
        self._chain_extra_had_error = False
        self._chain_extra_active = ""
        self._last_record_id: int | None = None
        self._live_checkpoint = LiveCheckpointTracker()
        self._setup_ui()
        self._document_session = DocumentSession()
        self._register_document_session_consumers()
        self.command_palette = CommandPalette(self)
        self.command_palette.record_requested.connect(self._open_record_view)
        self.command_palette.action_requested.connect(self._run_palette_action)
        self._connect_signals()
        self.setAcceptDrops(True)
        # Apply saved mic device
        cfg = get_config()
        self.recorder_widget.set_device(getattr(cfg, "mic_device_index", None))
        # The Library is the startup page but nothing else triggers its
        # first load — every other refresh() call is a reaction to
        # navigating back to it or saving a new record, neither of which
        # has happened yet on a cold start.
        self.library_view.refresh()
        if os.environ.get("WHISPERED_UI_GALLERY") != "1":
            self._start_gpu_detection()

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

        self._init_menu_bar()

        central = QWidget()
        self.setCentralWidget(central)
        self._workspace_layout = QVBoxLayout(central)
        self._workspace_layout.setContentsMargins(0, 0, 0, 0)
        self._workspace_layout.setSpacing(0)

    def _init_menu_bar(self) -> None:
        """Initialize the global menu bar (native on macOS)."""
        from PyQt6.QtGui import QAction
        menubar = self.menuBar()
        menubar.setNativeMenuBar(True)

        # --- File Menu ---
        file_menu = menubar.addMenu(tr("menu_file"))

        new_record_action = QAction(tr("menu_new_record"), self)
        new_record_action.triggered.connect(self._show_new_draft)
        file_menu.addAction(new_record_action)

        settings_action = QAction(tr("menu_settings"), self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        # --- Edit Menu ---
        edit_menu = menubar.addMenu(tr("menu_edit"))

        undo_action = QAction(tr("menu_undo"), self)
        undo_action.triggered.connect(self._trigger_undo)
        edit_menu.addAction(undo_action)

        find_action = QAction(tr("menu_find"), self)
        find_action.triggered.connect(self._trigger_find)
        edit_menu.addAction(find_action)

        # --- Video / Actions Menu ---
        video_menu = menubar.addMenu(tr("menu_video"))

        export_action = QAction(tr("cut_export"), self)
        export_action.triggered.connect(self._trigger_export_edl)
        video_menu.addAction(export_action)

        mark_action = QAction(tr("cut_mark"), self)
        mark_action.triggered.connect(self._trigger_mark_pauses)
        video_menu.addAction(mark_action)

        assemble_action = QAction(tr("cut_assemble"), self)
        assemble_action.triggered.connect(self._trigger_assemble_mp4)
        video_menu.addAction(assemble_action)

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

    def _build_library_section(self) -> None:
        """Create the shared source widgets and persistent Library."""
        self.transcribe_options = TranscribeOptionsPopover(embedded=True)
        self.model_combo = self.transcribe_options.model_combo
        self.language_combo = self.transcribe_options.language_combo
        self.translate_checkbox = self.transcribe_options.translate_checkbox
        self.perf_combo = self.transcribe_options.perf_combo
        self.diarization_checkbox = self.transcribe_options.diarization_checkbox
        self.step_checklist = StepChecklist()

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

        # AIProcessingPanel remains the existing worker-facing controller,
        # but its disabled actions no longer clutter an empty Library page.
        self.ai_panel = AIProcessingPanel()
        self.ai_panel.setVisible(False)
        self._shutdownables.append(self.ai_panel)

        # Batch Processing Panel lives on its own sidebar section (Queue);
        # still created here so signal wiring stays with the rest of setup.
        self.batch_panel = BatchPanel()
        self.batch_panel.start_requested.connect(self._start_batch_processing)
        self._shutdownables.append(self.batch_panel)

        # Book Pipeline Panel — created here (not mode-gated) so its
        # connection-check timers start with the rest of setup; it's shown
        # as its own tab on the Record view (see content_tabs below).
        self.book_panel = BookPanel()
        self.book_panel.run_single_requested.connect(self._on_book_run)
        self.book_panel.cancel_requested.connect(self._cancel_operation)
        self._shutdownables.append(self.book_panel)

        self.library_view = LibraryView()
        self.library_view.open_record.connect(self._open_record_view)
        self.library_view.open_cover.connect(lambda: self._on_section_changed("cover"))

    def _build_queue_section(self) -> None:
        """Queue is mounted in the persistent status surface later."""

    def _build_recorder_section(self) -> None:
        """Recorder is mounted as a DraftRecord source later."""

    def _build_live_section(self) -> None:
        """Create Live once; DraftRecord owns its visible placement."""
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
        self.record_view.clean_requested.connect(self._start_text_cleaning)
        self.record_view.articles_requested.connect(self._start_generate_all)

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

        self.insights_panel = InsightsPanel(insights_cache=self._insights_cache)
        self.insights_panel.generation_finished.connect(
            self._on_chain_insights_done
        )
        self._shutdownables.append(self.insights_panel)

        self.cut_view = CutView()
        self.cut_view.video_panel.export_edl_requested.connect(self._export_edl)
        self.cut_view.video_panel.mark_pauses_requested.connect(self._mark_pauses)
        self.cut_view.video_panel.assemble_requested.connect(self._assemble_draft)

        self.youtube_panel = YouTubePanel(insights_cache=self._insights_cache)
        self.youtube_panel.generation_finished.connect(self._on_chain_youtube_done)
        self._shutdownables.append(self.youtube_panel)
        self.record_view.set_content_widgets(self.player, self.main_tabs)

        self.inspector = InspectorRail()

        materials = QWidget()
        materials_layout = QVBoxLayout(materials)
        materials_layout.setContentsMargins(0, 0, 0, 0)
        self.material_selector = QComboBox()
        self.material_selector.addItems(
            [tr("tab_articles"), tr("tab_youtube"), tr("tab_book")]
        )
        materials_layout.addWidget(self.material_selector)
        self.material_stack = QStackedWidget()
        for panel in (self.article_view, self.youtube_panel, self.book_panel):
            self.material_stack.addWidget(panel)
        self.material_selector.currentIndexChanged.connect(
            self.material_stack.setCurrentIndex
        )
        materials_layout.addWidget(self.material_stack, stretch=1)
        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.addWidget(self.transcribe_options)
        settings_layout.addWidget(self.step_checklist, stretch=1)
        self.inspector.set_page("materials", materials)
        self.inspector.set_page("insights", self.insights_panel)
        self.inspector.set_page("cut", self.cut_view)
        self.inspector.set_page("chat", self.chat_panel)
        self.settings_stack = QStackedWidget()
        self.settings_stack.addWidget(settings_page)
        self.settings_stack.addWidget(self.live_view.options_panel)
        self.inspector.set_page("settings", self.settings_stack)
        self.tools_tabs = self.inspector

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
        self.draft_record = DraftRecord(
            self.file_selector,
            self.recorder_widget,
            self.live_view,
            folder_source,
        )
        self.draft_record.process_requested.connect(self._start_transcription)
        self.draft_record.source_changed.connect(self._on_draft_source_changed)
        self._draft_index = self._stack.addWidget(self.draft_record)
        self._record_index = self._stack.addWidget(self.record_view)
        self.cover_view = CoverView(insights_cache=self._insights_cache)
        self._shutdownables.append(self.cover_view)
        self._cover_index = self._stack.addWidget(self.cover_view)
        self._section_index = {
            "library": self._draft_index,
            "queue": self._draft_index,
            "recorder": self._draft_index,
            "live": self._draft_index,
            "cover": self._cover_index,
        }

        self.workspace_shell = WorkspaceShell(
            self.library_view, self._stack, self.inspector
        )
        self.workspace_shell.new_requested.connect(self._show_new_draft)
        self.inspector.settings_requested.connect(self._open_settings)
        self._workspace_layout.addWidget(self.workspace_shell, stretch=1)

        self.status_bar = StatusBar()
        self.operation_bar = self.status_bar
        self.status_label = self.status_bar.status_label
        self.progress_bar = self.status_bar.progress
        self.cancel_btn = self.status_bar.cancel_button
        self.device_btn = self.status_bar.device_button
        self.device_btn.setToolTip(tr("tooltip_device"))
        self.device_btn.clicked.connect(self._toggle_device)
        self.status_bar.cancel_requested.connect(self._cancel_operation)
        self.status_bar.bind_queue(self.batch_panel)
        self.batch_panel.queue_changed.connect(self.status_bar.set_queue_count)
        self.book_panel.connection_changed.connect(self.status_bar.set_llm_status)
        self.progress_timeline = ProgressTimeline()
        self.progress_timeline.stages = [
            tr("timeline_select"), tr("timeline_extract"), tr("timeline_transcribe"),
            tr("timeline_diarize"), tr("timeline_clean"), tr("timeline_generate"),
        ]
        self.progress_timeline.setVisible(False)
        self.status_bar.add_detail_widget(self.progress_timeline)
        self._workspace_layout.addWidget(self.status_bar)
        self.transcribe_btn = self.draft_record.process_button
        self._update_device_badge()

    def _on_section_changed(self, key: str) -> None:
        """Compatibility entry point: top-level destinations are draft sources."""
        idx = self._section_index.get(key)
        if idx is not None:
            self._stack.setCurrentIndex(idx)
            self.status_bar.show_queue(key == "queue")
            if key in {"recorder", "live"}:
                self.draft_record.set_source(key)
            elif key == "queue":
                self.draft_record.set_source("folder")
            elif key != "cover":
                self.draft_record.set_source("file")
            if key == "live" and os.environ.get("WHISPERED_UI_GALLERY") != "1":
                self.live_view.setup.refresh_targets()

    def _on_sidebar_collapsed(self, collapsed: bool) -> None:
        self.workspace_shell.set_library_collapsed(collapsed)

    def _show_new_draft(self) -> None:
        self._stack.setCurrentIndex(self._draft_index)
        self.draft_record.set_source("file")
        self.inspector.set_section("settings")

    def _on_draft_source_changed(self, source: str) -> None:
        is_live = source == "live"
        self.settings_stack.setCurrentIndex(1 if is_live else 0)
        if is_live:
            self.inspector.set_section("settings")

    def _show_library(self) -> None:
        """Navigate back to the Library page (from the Record view) and
        refresh the list in case anything changed while a record was open."""
        self._stack.setCurrentIndex(self._draft_index)
        self.library_view.refresh()

    def _open_record_view(self, record_id: int) -> None:
        """Load a history record and switch to the Record page."""
        if self._load_from_history(record_id):
            self.library_view.set_open_record(record_id)
            self._stack.setCurrentIndex(self._record_index)

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
                get_history_store().update_result(
                    self._live_checkpoint.history_record_id,
                    result,
                    speaker_names=getattr(result, "speaker_names", {}) or {},
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
            lambda result: self.ai_panel.set_has_transcription(True)
        )
        self._document_session.register_consumer(
            lambda result: self.book_panel.set_has_transcript(True)
        )

    def _connect_signals(self):
        """Connect widget signals."""
        self.file_selector.file_selected.connect(self._on_file_selected)
        self.file_selector.file_cleared.connect(self._on_file_cleared)
        self.transcript_view.copy_requested.connect(self._copy_to_clipboard)
        self.transcript_view.export_requested.connect(self._export_result)
        self.transcript_view.result_changed.connect(self._on_transcript_changed)

        # AI Panel signals
        self.ai_panel.clean_requested.connect(self._start_text_cleaning)
        self.ai_panel.generate_requested.connect(self._start_article_generation)
        self.ai_panel.generate_all_requested.connect(self._start_generate_all)

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

        _sc("Ctrl+,",       self._open_settings)
        _sc("Ctrl+O",       self.file_selector.browse_btn.click)
        _sc("Ctrl+T",       self._start_transcription)
        _sc("Ctrl+Return",  self.transcribe_btn.click)
        _sc("Ctrl+Enter",   self.transcribe_btn.click)  # numpad Enter
        _sc("Ctrl+E",       self._export_result)
        _sc("Ctrl+Shift+C", self._copy_to_clipboard)
        _sc("Ctrl+R",       self.recorder_widget._toggle_recording)
        _sc("Ctrl+K",       self.command_palette.open_palette)
        _sc("Ctrl+1",       self.library_view._search_edit.setFocus)
        _sc("Ctrl+2",       lambda: self.inspector.set_section("materials"))
        _sc("Ctrl+3",       lambda: self.inspector.set_section("settings"))
        # Space: play/pause only when focus is not inside a text input
        _sc("Space",        self._space_play_pause)

    def _run_palette_action(self, action: str) -> None:
        handlers = {
            "new": self._show_new_draft,
            "youtube": self.youtube_panel.generate,
            "export": self._export_result,
            "live": lambda: self._on_section_changed("live"),
            "queue": lambda: self._on_section_changed("queue"),
            "settings": self._open_settings,
        }
        handler = handlers.get(action)
        if handler is not None:
            handler()

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
        self.draft_record._source_buttons["live"].setEnabled(
            cfg.live_transcription_enabled
        )

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
            if urls and urls[0].isLocalFile() and is_supported_format(urls[0].toLocalFile()):
                event.acceptProposedAction()
                self._show_drop_overlay(True)
                return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._show_drop_overlay(False)

    def dropEvent(self, event: QDropEvent):
        self._show_drop_overlay(False)
        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0]
            if url.isLocalFile():
                filepath = url.toLocalFile()
                if is_supported_format(filepath):
                    self.file_selector._set_file(filepath)
                    event.acceptProposedAction()
                    return
        event.ignore()

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
        self.draft_record.set_process_enabled(True)
        self.status_label.setText(tr("status_ready_file", name=os.path.basename(filepath)))
        self.player.load(filepath)

    def _on_file_cleared(self):
        """Drop every media-specific reference when selection is cleared."""
        self._source_filepath = None
        self._source_kind = "file"
        self.player.load("")
        self.draft_record.set_process_enabled(False)
        # Transcript-only records can still be exported, but cannot be cut.
        self.cut_view.video_panel.set_has_transcript(False)

    def _on_transcript_changed(self, _change_kind: str) -> None:
        """Propagate an in-place transcript edit to every dependent view."""
        result = self.transcript_view.get_result()
        if result is None:
            return
        self._current_result = result
        self._cleaned_text = None
        self.cleaned_view.clear()
        self.article_view.clear()
        self._document_session.apply_result(result)
        if self._last_record_id is not None:
            try:
                from core.history import get_history_store
                store = get_history_store()
                store.update_result(
                    self._last_record_id,
                    result,
                    getattr(result, "speaker_names", {}),
                )
                self.library_view.refresh()
            except Exception as exc:
                logger.warning("Failed to persist transcript edit: %s", exc)

    def _start_transcription(self):
        """Start the transcription process."""
        # The Process button is disabled while a preset chain runs, but
        # the Ctrl+T / Ctrl+Return shortcuts call this directly — without
        # this guard they'd start a second transcription mid-chain (and
        # the youtube_panel.clear() below would kill the chain's workers
        # while it still waits on them, hanging the UI).
        if self._preset_chain.is_active():
            return
        filepath = self.file_selector.get_file()
        if not filepath:
            return

        # Update UI for transcription mode
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.setVisible(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setVisible(True)
        self.operation_bar.setVisible(True)
        self.progress_bar.setValue(0)
        # Select is done (file chosen); Extract is the first active stage
        self.progress_timeline.setVisible(True)
        self.progress_timeline.set_stage(1, 0)
        self.transcript_view.clear()
        self.cleaned_view.clear()
        self.article_view.clear()
        self.chat_panel.clear_transcript()
        self.insights_panel.clear()
        self.youtube_panel.clear()
        self.cut_view.clear()
        self._cleaned_text = None

        # Disable AI panel during transcription
        self.ai_panel.set_has_transcription(False)

        # Get settings from header controls
        model = self.model_combo.currentData()
        language = self.language_combo.currentData()
        translate = self.translate_checkbox.isChecked()
        perf_mode = self.perf_combo.currentData()

        # Determine thread count based on performance mode
        n_threads = get_thread_count(perf_mode)

        enable_diarization = self.diarization_checkbox.isChecked()

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

    def _cancel_ai_worker(self) -> None:
        """Cancel the current AI worker without blocking the GUI thread.

        This runs on a button click, not window close — a blocking
        ``wait(5000)`` here would freeze the whole UI for up to 5 seconds
        if the worker doesn't stop quickly. ``retire()`` disconnects its
        business signals immediately (so a late finished/error can't reach
        UI state that already believes the operation was cancelled) and
        hands it to WorkerRegistry, which deletes it once its QThread
        actually finishes.
        """
        if self._ai_worker is not None and self._ai_worker.isRunning():
            self._registry.retire(self._ai_worker)
            self._ai_worker = None

    def _cancel_operation(self):
        """Cancel the current operation (transcription, AI processing, or
        an in-progress preset chain — see _start_preset_chain)."""
        if self.batch_panel._is_processing():
            self.batch_panel.cancel_processing()
            self.status_label.setText(tr("status_cancelled"))
            return

        if self._chain_extra_active or self._chain_extra_steps:
            self._chain_extra_steps.clear()
            self._chain_extra_active = ""
            self.insights_panel.clear()
            self._cancel_ai_worker()
            self.status_label.setText(tr("status_chain_cancelled"))
            self._reset_ui()
            return

        if self._preset_chain.is_active():
            self._preset_chain.cancel()
            self.youtube_panel._cancel_workers(timeout=1000)
            self._cancel_ai_worker()
            self.ai_panel.set_processing(False)
            self.status_label.setText(tr("status_chain_cancelled"))
            self._reset_ui()
            return

        if self._ai_worker and self._ai_worker.isRunning():
            self._cancel_ai_worker()
            self.ai_panel.set_processing(False)
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
        self.operation_bar.set_operation(
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
        self._current_result = result
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
        if open_record:
            self._stack.setCurrentIndex(self._record_index)
            self.inspector.set_section("materials")
        self.inspector.set_artifacts(["transcript"])

        self._start_preset_chain()

    def _start_preset_chain(self) -> None:
        """After a fresh transcription, automatically run whatever extra
        generation steps the selected checklist calls for
        (Phase C.3). "transcribe_only" does nothing here — the transcript
        itself is already saved to history above."""
        selected = self.step_checklist.selected_steps()
        self._chain_extra_steps = [
            step for step in selected if step in {"insights", "book"}
        ]
        self._chain_extra_ran = set()
        self._chain_extra_had_error = False
        self._chain_extra_active = ""
        steps = self._preset_chain.start(self.step_checklist.legacy_preset())
        if not steps:
            self._start_next_extra_chain_step()
            return

        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setEnabled(False)
        self.status_label.setText(tr("status_chain_running"))

        if "youtube" in steps:
            self.youtube_panel.generate()
        if "article" in steps:
            self._start_text_cleaning()

    def _on_chain_youtube_done(self, success: bool) -> None:
        self._preset_chain.on_youtube_done(success)

    def _start_next_extra_chain_step(self) -> None:
        if not self._chain_extra_steps:
            self._finish_extra_chain()
            return
        step = self._chain_extra_steps.pop(0)
        self._chain_extra_active = step
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setEnabled(False)
        self.status_label.setText(tr("status_chain_running"))
        if step == "insights":
            self.inspector.set_section("insights")
            self.insights_panel._generate_all()
        elif step == "book":
            self.inspector.set_section("materials")
            self.material_selector.setCurrentIndex(2)
            self._on_book_run(
                self.book_panel.chk_unwrap.isChecked(),
                self.book_panel.chk_custom.isChecked(),
                self.book_panel.custom_prompt_edit.text().strip(),
            )

    def _on_chain_insights_done(self, success: bool) -> None:
        if self._chain_extra_active != "insights":
            return
        if success:
            self._chain_extra_ran.add("insights")
        else:
            self._chain_extra_had_error = True
        self._chain_extra_active = ""
        self._start_next_extra_chain_step()

    def _finish_extra_chain(self) -> None:
        if not self._chain_extra_ran and not self._chain_extra_had_error:
            return
        artifacts = {"transcript", *self._chain_extra_ran}
        if self._last_record_id is not None:
            try:
                from core.history import get_history_store
                current = get_history_store().get_record(self._last_record_id) or {}
                artifacts.update(current.get("artifacts", []))
                get_history_store().set_artifacts(
                    self._last_record_id, sorted(artifacts)
                )
                self.library_view.refresh()
            except Exception as exc:
                logger.warning("Failed to persist extra chain artifacts: %s", exc)
        self.inspector.set_artifacts(artifacts)
        self._reset_ui()
        if self._chain_extra_had_error:
            show_toast(self, tr("toast_chain_error"), kind="error")
        elif self._chain_extra_ran:
            show_toast(
                self,
                tr("toast_chain_done", count=len(self._chain_extra_ran)),
                kind="success",
            )

    def _finish_preset_chain(self, had_error: bool, ran: set[str]) -> None:
        """Auto-save whatever the chain produced to data_dir()/output/<stem>/
        and report how many files came out of it — the plan's "Готово: N
        артефактов" toast. Connected to PresetChainController.finished."""
        from core.paths import artifact_dir, output_dir
        from article_generator import export_all_articles
        from application.artifact_provenance import source_fingerprint, transcript_revision

        stem = Path(self._source_filepath).stem if self._source_filepath else "recording"
        if self._last_record_id is not None:
            out_dir = artifact_dir(self._last_record_id, self._source_filepath or stem)
        else:
            out_dir = output_dir() / stem

        saved: list[Path] = []
        if "youtube" in ran:
            saved.extend(self.youtube_panel.save_all(out_dir))
        if "article" in ran:
            articles = self.article_view.get_articles()
            if articles:
                try:
                    revision = (
                        transcript_revision(self._current_result.segments, self._current_result.language)
                        if self._current_result else ""
                    )
                    saved.extend(Path(p) for p in export_all_articles(
                        list(articles), str(out_dir),
                        record_id=self._last_record_id if self._last_record_id is not None else "unsaved",
                        source_path=self._source_filepath,
                        source_hash=source_fingerprint(self._source_filepath),
                        transcript_revision=revision,
                    ))
                except OSError as e:
                    logger.warning("Failed to export chain articles to %s: %s", out_dir, e)
                    had_error = True

        if saved and self._last_record_id is not None:
            artifact_types = ["transcript", *sorted(ran)]
            try:
                from core.history import get_history_store
                get_history_store().set_artifacts(self._last_record_id, artifact_types)
                self.library_view.refresh()
            except Exception as e:
                logger.warning("Failed to persist chain artifacts to history: %s", e)

        self._chain_extra_had_error = self._chain_extra_had_error or had_error
        self._chain_extra_ran.update(ran)
        if self._chain_extra_steps:
            self._start_next_extra_chain_step()
            return

        self._reset_ui()

        if saved:
            show_toast(self, tr("toast_chain_done", count=len(saved)), kind="success")
            self.status_label.setText(tr("toast_chain_done", count=len(saved)))
        elif had_error:
            show_toast(self, tr("toast_chain_error"), kind="error")

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
            self._last_record_id = get_history_store().add(
                result,
                source_path=source_path,
                model=model,
                speaker_names=speaker_names or {},
                source_kind=self._source_kind,
                source_name=source_name,
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
            from transcriber import TranscriptionResult, Segment, Word
            store = get_history_store()
            record = store.get_record(record_id)
            if record is None:
                return False
            payload = record["payload"]
            source_name = record["source_name"] or ""
            source_path = record["source_path"] or ""

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
            result = TranscriptionResult(
                segments=segments,
                language=payload.get("language", ""),
                duration=payload.get("duration", 0.0),
                speaker_names=payload.get("speaker_names") or {},
            )
            self._current_result = result
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
            self.inspector.set_artifacts(record.get("artifacts", ["transcript"]))
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
        """Reset UI to ready state — unless a preset chain is still
        running a later step (e.g. clean finished, article generation
        about to start), in which case Cancel must stay reachable and
        Process must stay disabled so the user can't start a second
        transcription mid-chain."""
        chain_active = bool(
            self._preset_chain.is_active()
            or self._chain_extra_active
            or self._chain_extra_steps
        )
        self.draft_record.set_process_enabled(
            not chain_active and self.file_selector.get_file() is not None
        )
        self.transcribe_btn.setVisible(True)
        self.cancel_btn.setVisible(chain_active)
        self.progress_bar.setVisible(False)
        self.progress_timeline.setVisible(False)
        self.operation_bar.setVisible(chain_active)

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

    # ===== AI Processing Methods =====

    def _get_text_for_ai(self) -> str | None:
        """Get text to use for AI processing (cleaned if available, else raw)."""
        if self._cleaned_text:
            return self._cleaned_text
        if self._current_result:
            return self._current_result.full_text
        return None

    def _start_text_cleaning(self):
        """Start text cleaning with AI."""
        if not self._current_result:
            self.status_label.setText(tr("status_no_transcription_to_clean"))
            return

        self.ai_panel.set_processing(True)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)
        self.progress_timeline.setVisible(True)
        self.progress_timeline.set_stage(4, 0)   # Clean

        self._ai_worker = AIProcessingWorker("clean", self._current_result.full_text)
        self._ai_worker.progress.connect(self._on_ai_progress)
        self._ai_worker.finished.connect(self._on_clean_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()

    def _start_article_generation(self, format_key: str):
        """Start single article generation."""
        text = self._get_text_for_ai()
        if not text:
            self.status_label.setText(tr("status_no_text_to_process"))
            return

        self.ai_panel.set_processing(True)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)
        self.progress_timeline.setVisible(True)
        self.progress_timeline.set_stage(5, 0)   # Generate

        self._ai_worker = AIProcessingWorker("generate", text, format=format_key)
        self._ai_worker.progress.connect(self._on_ai_progress)
        self._ai_worker.finished.connect(self._on_generate_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()

    def _start_generate_all(self):
        """Start generation of all article formats."""
        text = self._get_text_for_ai()
        if not text:
            self.status_label.setText(tr("status_no_text_to_process"))
            return

        self.ai_panel.set_processing(True)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)
        self.progress_timeline.setVisible(True)
        self.progress_timeline.set_stage(5, 0)   # Generate

        self._ai_worker = AIProcessingWorker("generate_all", text)
        self._ai_worker.progress.connect(self._on_ai_progress)
        self._ai_worker.finished.connect(self._on_generate_all_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()

    def _on_ai_progress(self, percentage: int, message: str):
        """Handle AI processing progress."""
        self.ai_panel.update_progress(percentage, message)
        self.progress_timeline.set_progress(percentage)
        self.status_label.setText(message)

    def _on_clean_finished(self, result):
        """Handle text cleaning completion."""
        from text_processor import ProcessingResult

        self.ai_panel.set_processing(False)
        self._reset_ui()
        self._ai_worker = None

        if isinstance(result, ProcessingResult):
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

        if self._preset_chain.consume_auto_article():
            self._start_generate_all()

    def _on_generate_finished(self, result):
        """Handle single article generation completion."""
        from article_generator import Article

        self.ai_panel.set_processing(False)
        self._reset_ui()
        self._ai_worker = None

        if isinstance(result, Article):
            self.article_view.set_article(result)

            # Switch to articles tab
            self.tools_tabs.setCurrentIndex(0)

            self.status_label.setText(
                tr(
                    "status_article_generated",
                    title=result.title,
                    words=result.word_count,
                )
            )

    def _on_generate_all_finished(self, result):
        """Handle all articles generation completion."""
        from article_generator import GenerationResult

        self.ai_panel.set_processing(False)
        self._reset_ui()
        self._ai_worker = None

        if isinstance(result, GenerationResult):
            self.article_view.set_articles(result.articles)

            # Switch to articles tab
            self.tools_tabs.setCurrentIndex(0)

            self.status_label.setText(tr(
                "status_articles_generated",
                count=len(result.articles),
                seconds=f"{result.generation_time:.1f}",
            ))

        self._preset_chain.on_generate_all_finished()

    def _on_ai_error(self, error_message: str):
        """Handle AI processing error."""
        self.ai_panel.set_processing(False)
        self._ai_worker = None

        if self._preset_chain.on_ai_error():
            self._reset_ui()
        else:
            self._reset_ui()
            self.status_label.setText(
                tr("status_ai_error", error=f"{error_message[:50]}...")
            )
            QMessageBox.warning(
                self,
                tr("error_ai"),
                tr("error_occurred", detail=error_message),
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
        self.operation_bar.set_operation(tr("toast_assembling"))
        self._draft_worker = DraftAssemblyWorker(src, segs, out_path, self)
        self._draft_worker.progress.connect(self.status_label.setText)
        self._draft_worker.assembled.connect(self._on_draft_assembled)
        self._draft_worker.error.connect(self._on_draft_assembly_error)
        self._draft_worker.start()

    def _on_draft_assembled(self, output_path: str) -> None:
        self._draft_worker = None
        self.operation_bar.clear()
        show_toast(self, tr("toast_assembled", name=os.path.basename(output_path)), kind="success")

    def _on_draft_assembly_error(self, error: str) -> None:
        self._draft_worker = None
        self.operation_bar.clear()
        show_toast(self, tr("toast_assemble_error", detail=error[:80]), kind="error")

    # ===== Book Pipeline Methods =====

    def _on_book_run(self, do_unwrap: bool, do_custom: bool, custom_prompt_path: str):
        """Start book pipeline processing for the current transcript."""
        if not self._current_result:
            self.status_label.setText(tr("status_no_transcript_for_book"))
            return

        text = self._current_result.full_text
        source_path = self._source_filepath or "transcript"

        self.book_panel.set_processing(True)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)

        from application.artifact_provenance import source_fingerprint, transcript_revision

        self._ai_worker = AIProcessingWorker(
            "book_unwrap", text,
            do_unwrap=do_unwrap,
            do_custom=do_custom,
            custom_prompt_path=custom_prompt_path,
            source_path=source_path,
            record_id=self._last_record_id if self._last_record_id is not None else "unsaved",
            source_hash=source_fingerprint(self._source_filepath),
            transcript_revision=transcript_revision(
                self._current_result.segments, self._current_result.language
            ),
        )
        self._ai_worker.progress.connect(self._on_book_progress)
        self._ai_worker.finished.connect(self._on_book_finished)
        self._ai_worker.error.connect(self._on_book_error)
        self._ai_worker.start()

    def _on_book_progress(self, percentage: int, message: str):
        """Handle book pipeline progress."""
        self.book_panel.update_progress(percentage, message)
        self.status_label.setText(message)

    def _on_book_finished(self, result):
        """Handle book pipeline completion."""
        from book_pipeline import BookResult

        self.book_panel.set_processing(False)
        self._reset_ui()
        self._ai_worker = None

        book_succeeded = False
        if isinstance(result, BookResult) and result.stages:
            saved_paths = [s.output_path for s in result.stages if s.success and s.output_path]
            if saved_paths:
                book_succeeded = True
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
                failed = [s.error for s in result.stages if not s.success]
                error = failed[0] if failed else tr("error_unknown")
                self.status_label.setText(tr("status_book_error", error=error))
        else:
            self.status_label.setText(tr("status_book_pipeline_done"))

        if self._chain_extra_active == "book":
            if book_succeeded:
                self._chain_extra_ran.add("book")
            else:
                self._chain_extra_had_error = True
            self._chain_extra_active = ""
            self._start_next_extra_chain_step()

    def _on_book_error(self, error_message: str):
        """Handle book pipeline error."""
        self.book_panel.set_processing(False)
        self._reset_ui()
        self._ai_worker = None

        self.status_label.setText(tr("status_book_error", error=error_message[:60]))
        if self._chain_extra_active == "book":
            self._chain_extra_had_error = True
            self._chain_extra_active = ""
            self._start_next_extra_chain_step()
        else:
            QMessageBox.warning(
                self,
                tr("error_book_pipeline_title"),
                tr("error_occurred", detail=error_message),
            )
