"""A broad ``except Exception`` must leave a trace: log, re-raise, report
to the user or caller, or at least use the exception. Narrow the type
instead when a specific failure is expected (2026-10 audit)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SKIP = (".venv", "build", "dist", "tests", "tests_qt", "docs", "tools")
# Ways a handler body tells someone what happened.
_REPORTS = (
    "logger", "logging", "raise", "emit", "print(", "warn", "_on_error",
    "error(", "set_error", "show_toast", "QMessageBox", ".put(", "failed",
    "errors.append", "return False",
)


def _silent_handlers() -> list[str]:
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        if path.relative_to(ROOT).parts[0] in _SKIP:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ExceptHandler):
                continue
            kind = node.type
            broad = kind is None or (
                isinstance(kind, ast.Name) and kind.id in ("Exception", "BaseException")
            )
            if not broad:
                continue
            body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
            if any(word in body for word in _REPORTS):
                continue
            if node.name and node.name in body:
                continue
            found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def test_no_broad_except_swallows_errors_silently():
    silent = _silent_handlers()
    assert not silent, "broad except without a trace:\n" + "\n".join(silent)
