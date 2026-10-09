"""Static layering guard (AST, so lazy in-function imports count too).

* domain/, application/, infrastructure/ are Qt-free: no PyQt6, ui, core.live,
  and no Qt-importing engines (transcriber, batch_processor).
* domain/ depends on nothing above it.
* infrastructure/ does not reach up into application/.
* core/ does not import application/ or ui/ (application -> core is the only
  allowed direction between them).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
QT_FREE_LAYERS = ("domain", "application", "infrastructure")


def _imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.lineno, node.module


def _violations(layer: str, forbidden) -> list[str]:
    found = []
    for path in sorted((ROOT / layer).rglob("*.py")):
        for lineno, module in _imports(path):
            if forbidden(module):
                found.append(f"{path.relative_to(ROOT)}:{lineno} imports {module}")
    return found


def _is(module: str, *names: str) -> bool:
    return any(module == n or module.startswith(n + ".") for n in names)


@pytest.mark.parametrize("layer", QT_FREE_LAYERS)
def test_layer_is_qt_free(layer):
    bad = _violations(
        layer,
        lambda m: _is(m, "PyQt6", "ui", "core.live", "transcriber", "batch_processor"),
    )
    assert not bad, f"{layer}/ must stay Qt-free:\n" + "\n".join(bad)


def test_domain_depends_on_nothing_above_it():
    bad = _violations("domain", lambda m: _is(m, "core", "application", "infrastructure"))
    assert not bad, "\n".join(bad)


def test_infrastructure_does_not_import_application():
    bad = _violations("infrastructure", lambda m: _is(m, "application"))
    assert not bad, "\n".join(bad)


def test_core_does_not_import_application_or_ui():
    bad = _violations("core", lambda m: _is(m, "application", "ui"))
    assert not bad, "\n".join(bad)
