"""Tests for core.model_repository — integrity, cancel, and atomic writes."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.model_manifest import ModelEntry
from core.model_repository import Cancelled, IntegrityError, ModelRepository


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_repo(tmp_path):
    """ModelRepository pointing at a fresh temp directory."""
    return ModelRepository(models_dir=tmp_path)


def _make_entry(tmp_path: Path, content: bytes = b"hello model") -> tuple[ModelEntry, Path]:
    """Create a ModelEntry whose sha256 and size match *content*."""
    sha = hashlib.sha256(content).hexdigest()
    entry = ModelEntry(
        key="test-model",
        url="http://example.com/test-model.bin",
        size_bytes=len(content),
        sha256=sha,
        license="MIT",
        filename="test-model.bin",
    )
    return entry, tmp_path / entry.filename


def _fake_response(content: bytes):
    """Minimal requests.Response mock that yields *content* in one chunk."""
    mock_resp = MagicMock()
    mock_resp.headers = {"content-length": str(len(content))}
    mock_resp.iter_content.return_value = [content]
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_valid_file_is_accepted(tmp_repo, tmp_path):
    """An existing file with correct size+sha256 is returned immediately."""
    content = b"good model data"
    entry, target = _make_entry(tmp_path, content)
    target.write_bytes(content)

    with patch.dict("core.model_repository.MANIFEST", {"test-model": entry}):
        path = tmp_repo.ensure("test-model")
    assert path == target


def test_bad_digest_raises_integrity_error(tmp_repo, tmp_path):
    """A downloaded file with wrong sha256 raises IntegrityError; target not created."""
    content = b"correct content"
    entry, target = _make_entry(tmp_path, content)
    wrong_content = b"tampered content"

    with patch("core.model_repository.requests.get", return_value=_fake_response(wrong_content)):
        with patch.dict("core.model_repository.MANIFEST", {"test-model": entry}):
            with pytest.raises(IntegrityError):
                tmp_repo.ensure("test-model")

    assert not target.exists(), "target file must not exist after integrity failure"
    assert not Path(str(target) + ".download").exists(), ".download must be cleaned up"


def test_truncated_existing_file_triggers_redownload(tmp_repo, tmp_path):
    """An existing file with wrong size is re-downloaded."""
    content = b"full model binary data"
    entry, target = _make_entry(tmp_path, content)
    # Write a truncated version
    target.write_bytes(content[:5])

    with patch("core.model_repository.requests.get", return_value=_fake_response(content)):
        with patch.dict("core.model_repository.MANIFEST", {"test-model": entry}):
            path = tmp_repo.ensure("test-model")

    assert path.read_bytes() == content


def test_cancel_removes_download_file(tmp_repo, tmp_path):
    """cancel() during download removes .download; raises Cancelled, not success."""
    content = b"large model data" * 100
    entry, target = _make_entry(tmp_path, content)

    call_count = [0]

    def cancel():
        call_count[0] += 1
        return call_count[0] >= 1  # cancel immediately

    with patch("core.model_repository.requests.get", return_value=_fake_response(content)):
        with patch.dict("core.model_repository.MANIFEST", {"test-model": entry}):
            with pytest.raises(Cancelled):
                tmp_repo.ensure("test-model", cancel=cancel)

    assert not target.exists(), "target must not exist after cancel"
    assert not Path(str(target) + ".download").exists(), ".download must be cleaned up"


def test_network_error_removes_download_file(tmp_repo, tmp_path):
    """Network error during streaming removes .download; exception re-raised."""
    import requests as req
    content = b"partial download"
    entry, target = _make_entry(tmp_path, content)

    mock_resp = MagicMock()
    mock_resp.headers = {"content-length": "1000"}
    mock_resp.raise_for_status.return_value = None
    mock_resp.iter_content.side_effect = req.exceptions.ChunkedEncodingError("broken")

    with patch("core.model_repository.requests.get", return_value=mock_resp):
        with patch.dict("core.model_repository.MANIFEST", {"test-model": entry}):
            with pytest.raises(req.exceptions.ChunkedEncodingError):
                tmp_repo.ensure("test-model")

    assert not target.exists()
    assert not Path(str(target) + ".download").exists()


def test_happy_path_no_part_file_left(tmp_repo, tmp_path):
    """Successful download: target file valid, no .download file left."""
    content = b"complete model binary"
    entry, target = _make_entry(tmp_path, content)

    with patch("core.model_repository.requests.get", return_value=_fake_response(content)):
        with patch.dict("core.model_repository.MANIFEST", {"test-model": entry}):
            path = tmp_repo.ensure("test-model")

    assert path.exists()
    assert path.read_bytes() == content
    assert not Path(str(target) + ".download").exists()


def test_transcriber_does_not_import_ui(tmp_path):
    """Static AST check: transcriber.py must not import any ui.* module."""
    src = Path("transcriber.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("ui."), (
                    f"transcriber.py imports {alias.name!r} — must not import ui.*"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("ui."):
                pytest.fail(
                    f"transcriber.py: 'from {node.module} import …' — must not import ui.*"
                )
            # lazy imports inside functions are allowed: check module names in calls
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            pass  # string literals are fine


def test_unknown_key_raises_key_error(tmp_repo):
    """ensure() with an unknown key raises KeyError."""
    with pytest.raises(KeyError, match="no-such-model"):
        tmp_repo.ensure("no-such-model")
