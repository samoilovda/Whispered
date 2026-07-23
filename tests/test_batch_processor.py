"""Unit tests for batch_processor.py — no Qt runtime, no whisper binary.

Qt stand-ins (QThread/pyqtSignal/QObject) come from tests/conftest.py:
QThread.start() is a no-op there (so BatchWorker.run() never actually
executes and never blocks on the transcription-completion event).
"""
import sys
import types

import pytest


class _FakeTranscriber:
    """Stand-in for transcriber.Transcriber; batch tests never call .start()
    on the worker, so .transcribe() is never actually invoked."""

    def cancel(self):
        pass

    def transcribe(self, **kwargs):
        raise AssertionError("transcribe() should not run: QThread.start() is stubbed")


_stub_transcriber = types.ModuleType("transcriber")
_stub_transcriber.Transcriber = _FakeTranscriber
_stub_transcriber.TranscriptionResult = object
# Direct assignment, not setdefault: tests/test_exporters.py installs its own
# "transcriber" stub (with different attributes) under the same fake module
# name: whichever wins by collection order previously left the other file's
# import broken. Assigning right before our own eager `from batch_processor
# import ...` guarantees *this* import sees the right stub regardless of
# what ran before it.
sys.modules["transcriber"] = _stub_transcriber

from batch_processor import BatchProcessor, BatchItem, BatchStatus, BatchWorker


@pytest.fixture
def media_files(tmp_path):
    paths = []
    for name in ("a.mp3", "b.wav"):
        p = tmp_path / name
        p.write_bytes(b"fake audio")
        paths.append(str(p))
    return paths


class TestBatchItem:
    def test_filename_is_basename(self):
        item = BatchItem(filepath="/some/dir/recording.mp3")
        assert item.filename == "recording.mp3"

    @pytest.mark.parametrize("status,expected", [
        (BatchStatus.PENDING, False),
        (BatchStatus.PROCESSING, False),
        (BatchStatus.COMPLETE, True),
        (BatchStatus.ERROR, True),
        (BatchStatus.CANCELLED, True),
    ])
    def test_is_complete(self, status, expected):
        item = BatchItem(filepath="x.mp3", status=status)
        assert item.is_complete is expected


class TestQueueManagement:
    def test_add_file_requires_existing_path(self):
        processor = BatchProcessor()
        assert processor.add_file("/no/such/file.mp3") is False
        assert processor.count == 0

    def test_add_file_success(self, media_files):
        processor = BatchProcessor()
        assert processor.add_file(media_files[0]) is True
        assert processor.count == 1

    def test_add_file_rejects_duplicates(self, media_files):
        processor = BatchProcessor()
        processor.add_file(media_files[0])
        assert processor.add_file(media_files[0]) is False
        assert processor.count == 1

    def test_add_files_returns_success_count(self, media_files):
        processor = BatchProcessor()
        added = processor.add_files(media_files + ["/missing.mp3"])
        assert added == 2
        assert processor.count == 2

    def test_remove_item_valid_index(self, media_files):
        processor = BatchProcessor()
        processor.add_files(media_files)
        assert processor.remove_item(0) is True
        assert processor.count == 1

    def test_remove_item_out_of_range(self, media_files):
        processor = BatchProcessor()
        processor.add_files(media_files)
        assert processor.remove_item(99) is False
        assert processor.remove_item(-1) is False

    def test_remove_item_refuses_while_processing(self, media_files):
        processor = BatchProcessor()
        processor.add_files(media_files)
        processor._items[0].status = BatchStatus.PROCESSING
        assert processor.remove_item(0) is False

    def test_clear_empties_queue(self, media_files):
        processor = BatchProcessor()
        processor.add_files(media_files)
        processor.clear()
        assert processor.count == 0

    def test_clear_completed_keeps_pending(self, media_files):
        processor = BatchProcessor()
        processor.add_files(media_files)
        processor._items[0].status = BatchStatus.COMPLETE
        processor.clear_completed()
        assert processor.count == 1
        assert processor.items[0].status == BatchStatus.PENDING

    def test_pending_and_complete_counts(self, media_files):
        processor = BatchProcessor()
        processor.add_files(media_files)
        processor._items[0].status = BatchStatus.COMPLETE
        assert processor.pending_count == 1
        assert processor.complete_count == 1

    def test_items_property_is_a_copy(self, media_files):
        processor = BatchProcessor()
        processor.add_files(media_files)
        snapshot = processor.items
        snapshot.append(BatchItem(filepath="ghost.mp3"))
        assert processor.count == len(media_files)


class TestStart:
    def test_start_noop_on_empty_queue(self):
        processor = BatchProcessor()
        processor.start(model_name="base")
        assert processor._worker is None

    def test_start_creates_worker_and_resets_failed_items(self, media_files):
        processor = BatchProcessor()
        processor.add_files(media_files)
        processor._items[0].status = BatchStatus.ERROR
        processor._items[0].error = "boom"

        processor.start(model_name="base")

        assert processor._worker is not None
        assert processor._items[0].status == BatchStatus.PENDING
        assert processor._items[0].error is None

    def test_start_ignored_while_already_processing(self, media_files, monkeypatch):
        processor = BatchProcessor()
        processor.add_files(media_files)
        processor.start(model_name="base")
        first_worker = processor._worker
        monkeypatch.setattr(type(first_worker), "isRunning", lambda self: True)

        processor.start(model_name="base")
        assert processor._worker is first_worker

    def test_cancel_without_running_worker_is_safe(self):
        processor = BatchProcessor()
        processor.cancel()  # should not raise


class TestBatchWorkerFailures:
    def test_model_setup_failure_does_not_wait_forever(self, monkeypatch):
        item = BatchItem(filepath="recording.mp3")
        worker = BatchWorker([item], model_name="base")
        monkeypatch.setattr(worker._transcriber, "transcribe", lambda **kwargs: None)
        errors = []
        worker.item_error.connect(lambda index, error: errors.append((index, error)))

        worker._process_item(0, item)

        assert item.status is BatchStatus.ERROR
        assert item.error == "Model download failed"
        assert errors == [(0, "Model download failed")]


class TestResultsAndExport:
    def test_get_results_only_includes_complete_items(self, media_files):
        processor = BatchProcessor()
        processor.add_files(media_files)
        processor._items[0].status = BatchStatus.COMPLETE
        processor._items[0].result = "fake-result-1"
        processor._items[1].status = BatchStatus.ERROR

        results = processor.get_results()
        assert results == ["fake-result-1"]

    def test_export_all_writes_only_completed_items(self, media_files, tmp_path, monkeypatch):
        processor = BatchProcessor()
        processor.add_files(media_files)
        processor._items[0].status = BatchStatus.COMPLETE
        processor._items[0].result = object()
        processor._items[1].status = BatchStatus.ERROR

        written = []

        def fake_export_result(result, output_path, format_key):
            written.append(output_path)

        monkeypatch.setattr("exporters.export_result", fake_export_result)

        out_dir = tmp_path / "exported"
        created = processor.export_all(str(out_dir), format_key="srt")

        assert len(created) == 1
        assert created[0].endswith(".srt")
        assert written == created

    def test_export_all_skips_failed_export_silently(self, media_files, tmp_path, monkeypatch):
        processor = BatchProcessor()
        processor.add_files(media_files)
        processor._items[0].status = BatchStatus.COMPLETE
        processor._items[0].result = object()

        def raising_export(result, output_path, format_key):
            raise RuntimeError("disk full")

        monkeypatch.setattr("exporters.export_result", raising_export)

        created = processor.export_all(str(tmp_path / "out"), format_key="txt")
        assert created == []
