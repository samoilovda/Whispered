"""Real-Qt tests for ui/components.py's KeepOpenMenu (see
docs/IMPROVEMENT_PLAN_2026-08.ru.md, A9(b)).

Regression basis: picking several export formats from RecordView's Export
menu meant reopening the menu once per format — a plain QMenu closes
itself the moment any action inside it, checkable or not, is clicked.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent

from ui.components import KeepOpenMenu


def _release_event() -> QMouseEvent:
    return QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(0, 0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_releasing_on_a_checkable_action_toggles_it_and_stays_open(process_events):
    menu = KeepOpenMenu()
    action = menu.addAction("SRT")
    action.setCheckable(True)
    menu.show()
    process_events()
    menu.setActiveAction(action)

    hidden = []
    menu.aboutToHide.connect(lambda: hidden.append(True))

    menu.mouseReleaseEvent(_release_event())
    process_events()

    assert action.isChecked()
    assert hidden == [], "the menu closed on a checkable action's toggle"

    menu.close()


def test_toggling_twice_flips_back(process_events):
    menu = KeepOpenMenu()
    action = menu.addAction("SRT")
    action.setCheckable(True)
    menu.show()
    process_events()
    menu.setActiveAction(action)

    menu.mouseReleaseEvent(_release_event())
    process_events()
    assert action.isChecked()

    menu.mouseReleaseEvent(_release_event())
    process_events()
    assert not action.isChecked()

    menu.close()


def test_a_disabled_checkable_action_falls_through_to_default_handling(process_events):
    """isEnabled() is checked too — the override must not fight Qt's own
    disabled-action handling by force-triggering it anyway."""
    menu = KeepOpenMenu()
    action = menu.addAction("SRT")
    action.setCheckable(True)
    action.setEnabled(False)
    menu.show()
    process_events()
    menu.setActiveAction(action)

    menu.mouseReleaseEvent(_release_event())
    process_events()

    assert not action.isChecked()

    menu.close()


def test_a_non_checkable_action_still_triggers(process_events):
    """The override only intercepts the checkable-action branch — a plain
    action (e.g. "Export") keeps using QMenu's own default handling."""
    menu = KeepOpenMenu()
    action = menu.addAction("Export")
    menu.show()
    process_events()
    menu.setActiveAction(action)

    fired = []
    action.triggered.connect(lambda: fired.append(True))

    menu.mouseReleaseEvent(_release_event())
    process_events()

    assert fired == [True]

    menu.close()
