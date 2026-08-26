"""Static guard: ui/main_window.py must never assign to
self._current_result — it's a read-only property delegating to
DocumentSession (see application/document_session.py and
docs/IMPROVEMENT_PLAN_2026-08.ru.md, A1).

DocumentSession.apply_result() is now the one place a result is recorded;
a stray assignment anywhere in main_window.py would raise AttributeError
at runtime (no setter exists), but catching it here means CI fails on the
mistake itself rather than on whatever Qt path first exercises it.
"""

from __future__ import annotations

import ast
from pathlib import Path

_TARGET = "_current_result"


def _assignment_targets(node: ast.AST):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            yield target
    elif isinstance(node, ast.AugAssign):
        yield node.target
    elif isinstance(node, ast.AnnAssign):
        yield node.target


def test_main_window_never_assigns_current_result_directly():
    src = Path("ui/main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    offenders = []
    for node in ast.walk(tree):
        for target in _assignment_targets(node):
            if (
                isinstance(target, ast.Attribute)
                and target.attr == _TARGET
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                offenders.append(getattr(node, "lineno", "?"))

    assert not offenders, (
        f"ui/main_window.py assigns self._current_result directly at "
        f"line(s) {offenders} — it must go through "
        f"self._document_session.apply_result(result) instead"
    )
