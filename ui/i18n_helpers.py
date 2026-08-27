"""Runtime UI-language switching helpers for ``ui`` widgets.

Whispered's locale layer (``core.i18n``) can now swap languages without an
application restart: ``core.i18n.set_locale()`` reloads the strings and
calls every callback registered through ``core.i18n.on_language_changed``.

Widgets capture their translatable text at construction time via ``tr()``,
so something has to re-apply those captions when the language changes.
``Retranslator`` is the small bookkeeper that does it: a widget records
each caption as a closure while it builds its UI, then ``bind()``s the
whole set to the language-changed signal.

Example::

    from ui.i18n_helpers import Retranslator

    class MyPanel(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._i18n = Retranslator()
            self._title = self._i18n.text(QLabel(), "my_title")
            self._go = self._i18n.text(QPushButton(), "my_go", tooltip="my_go_tip")
            self._i18n.call(self._refresh_rows)   # dynamic content
            self._i18n.bind()
"""

from __future__ import annotations

from typing import Callable

from core.i18n import on_language_changed, tr
from core.logger import get_logger

logger = get_logger(__name__)


class Retranslator:
    """Collects re-translation closures for one widget and replays them
    whenever the UI language changes at runtime."""

    def __init__(self) -> None:
        self._fns: "list[Callable[[], None]]" = []
        self._bound = False

    # ── registration ───────────────────────────────────────────────────

    def add(self, fn: "Callable[[], None]") -> None:
        """Run *fn* now and again on every language change."""
        fn()
        self._fns.append(fn)

    def call(self, fn: "Callable[[], None]") -> None:
        """Register *fn* to run on every language change, but NOT now —
        for a method that re-renders content the constructor already
        produced once."""
        self._fns.append(fn)

    def text(self, widget, key: str, setter: str = "setText",
             tooltip: "str | None" = None, **fmt):
        """Bind ``widget.<setter>(tr(key, **fmt))`` (and optionally a
        tooltip) to language changes. Returns *widget* so it can wrap a
        freshly constructed one inline."""
        def _apply() -> None:
            getattr(widget, setter)(tr(key, **fmt))
            if tooltip is not None:
                widget.setToolTip(tr(tooltip))
        self.add(_apply)
        return widget

    def form_row(self, form, key: str, field) -> None:
        """Add ``field`` to a ``QFormLayout`` with a translatable label that
        re-resolves on language changes (Qt builds the row label widget
        itself, so it is fetched back via ``labelForField``)."""
        form.addRow(tr(key), field)

        def _apply() -> None:
            label = form.labelForField(field)
            if label is not None:
                label.setText(tr(key))

        self._fns.append(_apply)

    def combo_items(self, combo, items) -> None:
        """Populate *combo* from ``(locale_key, data)`` pairs and re-label
        the items (by position, data preserved) on language changes."""
        keys = [k for k, _ in items]
        for key, data in items:
            combo.addItem(tr(key), data)

        def _apply() -> None:
            for i, key in enumerate(keys):
                combo.setItemText(i, tr(key))

        self._fns.append(_apply)

    # ── replay ─────────────────────────────────────────────────────────

    def retranslate(self) -> None:
        for fn in self._fns:
            try:
                fn()
            except RuntimeError:
                # Underlying C++ object already deleted — harmless here.
                pass
            except Exception:
                logger.exception("retranslate closure failed: %r", fn)

    def bind(self) -> None:
        """Subscribe this retranslator to ``core.i18n`` language changes.

        Idempotent. The subscription is weak (see ``core.i18n``), so a
        destroyed widget drops out on its own."""
        if not self._bound:
            on_language_changed(self.retranslate)
            self._bound = True
