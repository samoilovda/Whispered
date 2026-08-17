"""Contract checks for the record workspace export surface."""

import ast
from pathlib import Path


def test_export_menu_exposes_every_implemented_format():
    root = Path(__file__).resolve().parents[1]
    exporters_tree = ast.parse((root / "exporters.py").read_text(encoding="utf-8"))
    record_view_tree = ast.parse((root / "ui" / "record_view.py").read_text(encoding="utf-8"))

    exports = _dict_keys_assignment(exporters_tree, "EXPORT_FORMATS")
    menu_formats = _literal_assignment(record_view_tree, "_FORMAT_KEYS")

    assert tuple(menu_formats) == tuple(exports)


def _literal_assignment(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
    raise AssertionError(f"Assignment {name} not found")


def _dict_keys_assignment(tree: ast.Module, name: str) -> list[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                assert isinstance(node.value, ast.Dict)
                return [ast.literal_eval(key) for key in node.value.keys]
    raise AssertionError(f"Assignment {name} not found")
