"""One persistent operation, queue, LLM and compute status surface."""

from __future__ import annotations

import re
import time

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr, tr_count
from ui.i18n_helpers import Retranslator
from ui.icons import get_icon
from ui.theme import get_theme


def _place_above(widget: QWidget, anchor: QWidget, *, align_right: bool = False) -> None:
    """Move *widget* (a top-level card) just above *anchor*, kept fully on
    the screen the anchor is on. Unclamped, a card can land off-screen:
    near the left/right edge in a maximized window, or above the very top
    of the screen when the status bar itself sits close to it."""
    rect = anchor.rect()
    pos = anchor.mapToGlobal(rect.topRight() if align_right else rect.topLeft())
    x = pos.x() - widget.width() if align_right else pos.x()
    y = pos.y() - widget.height() - 6
    screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        x = max(available.left(), min(x, available.right() - widget.width()))
        y = max(available.top(), min(y, available.bottom() - widget.height()))
    widget.move(x, y)


class _QueueOverlay(QDialog):
    """Floating card hosting the batch and course-capture queue panels.

    Anchored above its trigger button instead of being embedded in the
    status bar's own layout — the previous embedded popup grew the status
    bar's height whenever opened, shrinking the workspace above it (see
    docs/UI_REDESIGN_PLAN_2026-09.ru.md, A6). A non-modal QDialog rather
    than a ``Qt.WindowType.Popup`` widget: an early version of
    ui/transcribe_options.py used exactly that pattern for a floating
    panel, but its non-embedded branch was never actually constructed
    anywhere (removed in docs/IMPROVEMENT_PLAN_2026-08.ru.md, A8) and it
    turned out not to survive being shown-then-resized cleanly on the
    offscreen QPA platform this app's tests and gallery run under —
    CommandPalette's QDialog is the pattern the gallery already proves
    stable for exactly that sequence.

    Batch (file/book) processing and course capture are shown one at a
    time behind a small switcher instead of two separate status-bar
    buttons/popups — one queue surface, same as everything else this
    phase merges. When course capture is unavailable
    (``Config.live_transcription_enabled`` is off) the switcher itself is
    hidden and the overlay just shows the batch panel, same as before
    course capture existed.
    """

    # Built in __init__ after lambdas that refer to them.
    _course_btn: QPushButton
    _stack: QStackedWidget


    def __init__(self, batch_widget: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.setModal(False)
        self.setProperty("role", "card")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._switcher = QWidget()
        switcher_layout = QHBoxLayout(self._switcher)
        switcher_layout.setContentsMargins(0, 0, 0, 0)
        switcher_layout.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._i18n = Retranslator()
        self._files_btn = QPushButton(tr("queue_tab_files"))
        self._i18n.call(lambda: self._files_btn.setText(tr("queue_tab_files")))
        self._i18n.call(lambda: self._course_btn.setText(tr("queue_tab_course")))
        self._i18n.bind()
        self._files_btn.setCheckable(True)
        self._files_btn.setChecked(True)
        self._files_btn.setProperty("role", "quick-chip")
        self._files_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        self._group.addButton(self._files_btn)
        switcher_layout.addWidget(self._files_btn)
        self._course_btn = QPushButton(tr("queue_tab_course"))
        self._course_btn.setCheckable(True)
        self._course_btn.setProperty("role", "quick-chip")
        self._course_btn.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        self._group.addButton(self._course_btn)
        switcher_layout.addWidget(self._course_btn)
        switcher_layout.addStretch()
        self._switcher.setVisible(False)
        root.addWidget(self._switcher)

        # QStackedWidget manages each page's visibility itself (only the
        # current page is shown) — do not call setVisible() on pages
        # directly, that fights the stack and shows both at once.
        self._stack = QStackedWidget()
        self._stack.addWidget(batch_widget)
        root.addWidget(self._stack, stretch=1)

        self.resize(420, 460)

    def set_course_widget(self, widget: QWidget) -> None:
        self._stack.addWidget(widget)
        self.set_course_available(True)

    def set_course_available(self, available: bool) -> None:
        self._switcher.setVisible(available and self._stack.count() > 1)
        if not available and self._stack.count() > 1:
            self._files_btn.setChecked(True)
            self._stack.setCurrentIndex(0)

    def show_above(self, anchor: QWidget, *, align_right: bool = False) -> None:
        _place_above(self, anchor, align_right=align_right)
        self.show()
        self.raise_()
        self.activateWindow()


# Dot colour per chip state: the dot is never the only signal — the chip's
# caption and its popover say the same thing in words.
_DOT_STATES = ("ok", "idle", "busy", "error")


class StatusChip(QPushButton):
    """Flat status-bar chip: a monochrome icon (or a state dot) and a
    short caption; a click opens its popover. Replaces the old mix of a
    pill badge, a ghost button and emoji-prefixed text."""

    ICON_SIZE = 14

    def __init__(self, icon: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", "status-chip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(self.ICON_SIZE, self.ICON_SIZE))
        self._icon_name = icon
        self._state = ""
        self._render_icon()

    def set_state(self, state: str) -> None:
        """Show a dot in the state's colour instead of the icon; ``""``
        goes back to the icon."""
        self._state = state if state in _DOT_STATES else ""
        self._render_icon()

    def state(self) -> str:
        return self._state

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # A theme switch restyles the app: re-tint the icon/dot with it.
        if event is not None and event.type() == QEvent.Type.StyleChange:
            self._render_icon()
        super().changeEvent(event)

    def _render_icon(self) -> None:
        t = get_theme()
        if self._state:
            color = {
                "ok": t.success, "idle": t.text_muted,
                "busy": t.warning, "error": t.error,
            }[self._state]
            self.setIcon(QIcon(_dot_pixmap(color, self.ICON_SIZE)))
        elif self._icon_name:
            self.setIcon(get_icon(self._icon_name, t.text_secondary, self.ICON_SIZE * 2))
        else:
            self.setIcon(QIcon())


def _dot_pixmap(color: str, size: int) -> QPixmap:
    """A filled circle, a third of *size* across, centred in a *size* square
    (so the dot lines up with the icons of neighbouring chips)."""
    ratio = 2  # drawn at 2x so it stays crisp on Retina
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    diameter = size * ratio * 0.5
    offset = (size * ratio - diameter) / 2
    painter.drawEllipse(int(offset), int(offset), int(diameter), int(diameter))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


class _ChipPopover(QFrame):
    """Small card above a status chip; closes on any click outside it
    (``Qt.WindowType.Popup``). Its content is rebuilt by the owner right
    before each show, so it never needs retranslating."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setProperty("role", "popover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(280)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        self.title = QLabel()
        self.title.setProperty("role", "popover-title")
        root.addWidget(self.title)
        self.form = QFormLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setHorizontalSpacing(16)
        self.form.setVerticalSpacing(6)
        root.addLayout(self.form)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(6)
        root.addLayout(self.body)
        self.action_row = QHBoxLayout()
        self.action_row.setContentsMargins(0, 4, 0, 0)
        self.action_row.setSpacing(6)
        self.action_row.addStretch()
        root.addLayout(self.action_row)

    def add_row(self, label: str, value: QWidget | str) -> QWidget:
        caption = QLabel(label)
        caption.setProperty("role", "muted")
        if isinstance(value, str):
            value = QLabel(value)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.form.addRow(caption, value)
        return value

    def add_action(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(self.close)
        button.clicked.connect(slot)
        self.action_row.addWidget(button)
        return button

    def show_above(self, anchor: QWidget) -> None:
        self.adjustSize()
        _place_above(self, anchor, align_right=True)
        self.show()


def _short_llm_name(model_id: str) -> str:
    """``google/gemma-4-12b`` → ``gemma-4-12b``: the vendor prefix is noise
    in a 30 px footer (the popover and tooltip keep the full id)."""
    return model_id.rsplit("/", 1)[-1]


def _short_whisper_name(key: str) -> str:
    """``large-v3-turbo-q5_0`` → ``large-v3-turbo``: the quantization
    suffix is noise in the footer (the popover shows the full label)."""
    return re.sub(r"-q\d.*$", "", key)


_BACKEND_NAMES = {"metal": "Metal", "cuda": "CUDA", "rocm": "ROCm", "cpu": "CPU"}


class StatusBar(QFrame):
    """The window's footer: the current operation on the left; queue, LLM
    and transcription chips on the right, each opening a popover with the
    details and the actions that go with them."""

    cancel_requested = pyqtSignal()
    queue_requested = pyqtSignal()
    # Re-probe LM Studio now (the LLM popover's "Check again", or after the
    # active AI profile changed).
    llm_refresh_requested = pyqtSignal()
    # Open Settings on a settings_category_* page.
    settings_requested = pyqtSignal(str)
    # The compute popover's GPU/CPU choice: True = use the GPU.
    device_selected = pyqtSignal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", "operation-bar")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(10)
        self.status_label = QLabel(tr("status_idle"))
        self.status_label.setProperty("role", "muted")
        row.addWidget(self.status_label, stretch=1)
        # How long the current operation has been going, ticking every
        # second: an LLM call can sit on one percentage (or stream nothing
        # while it reasons) for minutes, and a clock that moves is what
        # tells "still working" apart from "hung".
        self.elapsed_label = QLabel()
        self.elapsed_label.setProperty("role", "muted")
        self.elapsed_label.setVisible(False)
        row.addWidget(self.elapsed_label)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(300)
        self.progress.setVisible(False)
        row.addWidget(self.progress)
        self.cancel_button = QPushButton(tr("btn_cancel"))
        self.cancel_button.setProperty("variant", "danger")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        row.addWidget(self.cancel_button)
        # Chips, separated by hairlines. The queue chip is shown only while
        # something is queued — "Queue 0" was a button to an empty panel
        # (the sidebar and the View menu still open the queue when empty).
        self.queue_button = StatusChip("layers")
        self.queue_button.clicked.connect(self._toggle_queue)
        self._queue_sep = self._separator()
        row.addWidget(self.queue_button)
        row.addWidget(self._queue_sep)
        self.llm_chip = StatusChip()
        self.llm_chip.set_state("idle")
        self.llm_chip.clicked.connect(self._show_llm_popover)
        row.addWidget(self.llm_chip)
        row.addWidget(self._separator())
        self.device_button = StatusChip("cpu")
        self.device_button.clicked.connect(self._show_compute_popover)
        row.addWidget(self.device_button)
        root.addLayout(row)
        self.detail_container = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_container)
        self.detail_layout.setContentsMargins(16, 0, 16, 8)
        root.addWidget(self.detail_container)
        self._overlay: _QueueOverlay | None = None
        self._batch_count = 0
        self._course_count = 0
        self._status_is_idle = True
        self._llm_checked = False
        self._llm_connected = False
        self._llm_detail = ""
        self._gpu_type = "detecting"
        self._gpu_name = ""
        self._use_gpu = True
        self._whisper_model = ""
        self._popover: _ChipPopover | None = None
        self._busy_since: float | None = None
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)
        self._clock.start()
        self._i18n = Retranslator()
        self._i18n.call(self._retranslate_status)
        self._i18n.bind()
        self._retranslate_status()

    # Shown only once an operation has run this long: a quick one ends
    # before a clock would mean anything.
    ELAPSED_AFTER_S = 3

    def is_busy(self) -> bool:
        """An operation is on: its progress bar or Cancel button is up
        (callers toggle those widgets directly, so this reads them)."""
        return not self.progress.isHidden() or not self.cancel_button.isHidden()

    def _tick(self) -> None:
        if not self.is_busy():
            self._busy_since = None
            self.elapsed_label.setVisible(False)
            return
        now = time.monotonic()
        if self._busy_since is None:
            self._busy_since = now
        elapsed = int(now - self._busy_since)
        if elapsed < self.ELAPSED_AFTER_S:
            return
        self.elapsed_label.setText(
            tr("status_elapsed", elapsed=f"{elapsed // 60}:{elapsed % 60:02d}"))
        self.elapsed_label.setVisible(True)

    def _retranslate_status(self) -> None:
        """Re-apply the count/state-derived captions after a live
        UI-language switch. Operation text mid-run is left alone — it is
        re-emitted in the active language on the next progress update."""
        if self._status_is_idle:
            self.status_label.setText(tr("status_idle"))
        self.cancel_button.setText(tr("btn_cancel"))
        self._sync_queue_count()
        self._sync_llm_chip()
        self._sync_device_chip()

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setProperty("role", "status-sep")
        line.setFixedSize(1, 14)
        return line

    def add_detail_widget(self, widget: QWidget) -> None:
        self.detail_layout.addWidget(widget)

    def bind_queue(self, widget: QWidget) -> None:
        self._overlay = _QueueOverlay(widget, self)

    def bind_course(self, widget: QWidget) -> None:
        if self._overlay is None:
            raise RuntimeError("bind_queue() must be called before bind_course()")
        self._overlay.set_course_widget(widget)

    def set_course_available(self, available: bool) -> None:
        """Whether Course Capture is enabled at all
        (``Config.live_transcription_enabled``) — hides the overlay's
        switcher entirely rather than leaving a dead tab to click."""
        if self._overlay is not None:
            self._overlay.set_course_available(available)

    def _toggle_queue(self) -> None:
        self.show_queue(self._overlay is None or not self._overlay.isVisible())
        self.queue_requested.emit()

    def show_queue(self, visible: bool) -> None:
        if self._overlay is None:
            return
        if visible:
            # With nothing queued the chip is hidden: anchor to the bar's
            # right edge, where the chip would be.
            anchor = self.queue_button if self.queue_button.isVisible() else self
            self._overlay.show_above(anchor, align_right=True)
        else:
            self._overlay.reject()

    def set_operation(
        self,
        text: str,
        *,
        progress: int | None = None,
        cancel_text: str = "",
        details_text: str = "",
        cancellable: bool = True,
    ) -> None:
        self._status_is_idle = False
        self.status_label.setText(text)
        self.progress.setVisible(True)
        if progress is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(max(0, min(100, progress)))
        # Always resolved, not "only when given": a caller that starts a
        # new operation without cancel_text used to inherit whatever
        # custom label an earlier operation left on the button, since the
        # old `if cancel_text:` guard only ever set it, never restored it.
        self.cancel_button.setText(cancel_text or tr("btn_cancel"))
        self.cancel_button.setVisible(cancellable)

    # ── LLM ──────────────────────────────────────────────────────────────

    def set_llm_status(self, connected: bool, detail: str = "") -> None:
        """LM Studio probe result: *detail* is the loaded model's id when
        connected (empty = none loaded yet), else the error message."""
        self._llm_checked = True
        self._llm_connected = connected
        self._llm_detail = detail
        self._sync_llm_chip()

    def _sync_llm_chip(self) -> None:
        chip = self.llm_chip
        if not self._llm_checked:
            chip.set_state("idle")
            chip.setText("LM Studio")
            chip.setToolTip(tr("status_llm_checking"))
        elif not self._llm_connected:
            chip.set_state("error")
            chip.setText(tr("status_llm_offline"))
            chip.setToolTip(tr("status_llm_unavailable"))
        elif self._llm_detail:
            chip.set_state("ok")
            chip.setText(_short_llm_name(self._llm_detail))
            chip.setToolTip(self._llm_detail)
        else:
            chip.set_state("idle")
            chip.setText("LM Studio")
            chip.setToolTip(tr("status_llm_no_model"))

    def _show_llm_popover(self) -> None:
        from application.ai_profiles import active_profile, all_profiles
        from config import get_config

        cfg = get_config()
        profile = active_profile(cfg)
        pop = self._new_popover(tr("status_llm_title"))
        if not self._llm_checked:
            state = tr("status_llm_checking")
        elif self._llm_connected:
            state = tr("status_llm_connected")
        else:
            state = tr("status_llm_offline")
        pop.add_row(tr("status_llm_state"), state)
        pop.add_row(tr("status_llm_server"), profile.endpoint)
        if self._llm_connected:
            pop.add_row(tr("status_llm_loaded"), self._llm_detail or tr("status_llm_no_model"))
        profiles = all_profiles(cfg)
        if len(profiles) > 1:
            combo = QComboBox()
            for p in profiles:
                combo.addItem(p.name, p.name)
            combo.setCurrentIndex(max(0, combo.findData(profile.name)))
            combo.currentIndexChanged.connect(
                lambda _i: self._select_llm_profile(combo.currentData()))
            pop.add_row(tr("status_llm_profile"), combo)
        elif profile.model:
            pop.add_row(tr("status_llm_model_label"), profile.model)
        if self._llm_checked and not self._llm_connected:
            hint = QLabel(tr("status_llm_offline_hint"))
            hint.setWordWrap(True)
            hint.setProperty("role", "muted")
            pop.body.addWidget(hint)
        pop.add_action(tr("status_llm_recheck"), self.llm_refresh_requested.emit)
        pop.add_action(
            tr("status_open_settings"),
            lambda: self.settings_requested.emit("settings_category_ai"))
        pop.show_above(self.llm_chip)

    def _select_llm_profile(self, name: str) -> None:
        """The popover's profile switch: same effect as choosing it in
        Settings → AI profiles, then re-probe the (maybe new) endpoint."""
        from config import get_config, save_config
        from domain.ai_profile import DEFAULT_PROFILE

        cfg = get_config()
        cfg.lm_profile_active = "" if name == DEFAULT_PROFILE else name
        save_config()
        self.llm_refresh_requested.emit()

    # ── Transcription compute ────────────────────────────────────────────

    def set_device(self, gpu_type: str, gpu_name: str, use_gpu: bool) -> None:
        """Hardware detection result plus the current GPU/CPU choice;
        *gpu_type* is ``detecting``, ``metal``, ``cuda``, ``rocm`` or ``cpu``."""
        self._gpu_type = gpu_type
        self._gpu_name = gpu_name
        self._use_gpu = use_gpu and gpu_type not in ("cpu", "detecting")
        self._sync_device_chip()

    def set_whisper_model(self, key: str) -> None:
        """The default transcription model (``Config.default_model``)."""
        self._whisper_model = key
        self._sync_device_chip()

    def _backend_name(self) -> str:
        return _BACKEND_NAMES.get(self._gpu_type, "GPU") if self._use_gpu else "CPU"

    def _sync_device_chip(self) -> None:
        chip = self.device_button
        if self._gpu_type == "detecting":
            backend = tr("device_detecting")
        else:
            backend = self._backend_name()
        model = _short_whisper_name(self._whisper_model)
        chip.setText(f"{model} · {backend}" if model else backend)
        chip.setToolTip(tr("tooltip_device"))

    def _whisper_label(self) -> str:
        key = self._whisper_model
        if not key:
            return "—"
        label = tr(f"whisper_model_{key.replace('-', '_')}")
        return key if label.startswith("whisper_model_") else label

    def _show_compute_popover(self) -> None:
        pop = self._new_popover(tr("status_compute_title"))
        pop.add_row(tr("status_compute_model"), self._whisper_label())
        group = QButtonGroup(pop)
        gpu = QRadioButton(
            self._gpu_name if self._gpu_type not in ("cpu", "detecting")
            else tr("status_compute_no_gpu"))
        gpu.setEnabled(self._gpu_type not in ("cpu", "detecting"))
        cpu = QRadioButton(tr("status_compute_cpu"))
        group.addButton(gpu)
        group.addButton(cpu)
        (gpu if self._use_gpu else cpu).setChecked(True)
        gpu.toggled.connect(self._on_device_toggled)
        caption = QLabel(tr("status_compute_device"))
        caption.setProperty("role", "muted")
        pop.body.addWidget(caption)
        pop.body.addWidget(gpu)
        pop.body.addWidget(cpu)
        pop.add_action(
            tr("status_open_settings"),
            lambda: self.settings_requested.emit("settings_category_transcription"))
        pop.show_above(self.device_button)

    def _on_device_toggled(self, use_gpu: bool) -> None:
        if use_gpu == self._use_gpu:
            return
        self._use_gpu = use_gpu
        self._sync_device_chip()
        self.device_selected.emit(use_gpu)

    def _new_popover(self, title: str) -> _ChipPopover:
        """A fresh popover (content follows state at open time); the
        previous one, if still around, is dropped."""
        if self._popover is not None:
            self._popover.close()
            self._popover.deleteLater()
        self._popover = _ChipPopover(self)
        self._popover.title.setText(title)
        return self._popover

    # ── Queue ────────────────────────────────────────────────────────────

    def set_queue_count(self, count: int) -> None:
        self._batch_count = count
        self._sync_queue_count()

    def set_course_count(self, count: int) -> None:
        self._course_count = count
        self._sync_queue_count()

    def _sync_queue_count(self) -> None:
        count = self._batch_count + self._course_count
        self.queue_button.setText(tr_count("status_queue_count", count))
        self.queue_button.setVisible(count > 0)
        self._queue_sep.setVisible(count > 0)

    def clear(self) -> None:
        self._status_is_idle = True
        self.status_label.setText(tr("status_idle"))
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)

    def set_busy(self, busy: bool) -> None:
        """Show or hide the progress bar and Cancel button together, for
        a caller that wants "an operation just started/ended" as one
        call rather than setting each widget itself.

        Does not touch the bar's own visibility — this frame is
        infrastructure status and stays on screen regardless (see the
        module docstring); a caller used to reach for
        ``self.setVisible(...)`` for that, which a now-removed override
        silently no-op'd into always-True. Nothing ever actually depended
        on hiding the frame itself, only on the progress/cancel indicators
        it hosts — this method names that real intent instead.
        """
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)
