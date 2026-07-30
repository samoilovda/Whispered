"""
Whispered UI - Shutdownable protocol
Uniform shutdown contract for MainWindow's child panels/components.

Before this existed, MainWindow.closeEvent reached into each panel
individually — a ladder of ``hasattr(self, 'x')`` guards, some of them
into a panel's own private attributes (``book_panel._batch_worker``).
Every panel/component that owns background work (a QThread, a timer, an
in-flight probe) instead exposes ``shutdown()`` and registers itself in
``MainWindow._shutdownables``; ``closeEvent`` just iterates the list.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Shutdownable(Protocol):
    def shutdown(self) -> None:
        """Stop any background work owned by this object. Must not block
        longer than a short, bounded wait — this runs on the GUI thread
        during window close."""
        ...
