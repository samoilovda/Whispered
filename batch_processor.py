"""
Whisper Fedora - Batch Processor
Process multiple audio/video files in sequence
"""

import os
from dataclasses import dataclass, field
from typing import Optional, List, Callable
from enum import Enum
from PyQt6.QtCore import QObject, pyqtSignal, QThread

from transcriber import Transcriber, TranscriptionResult


# ============================================================================
# DATA CLASSES
# ============================================================================

class BatchStatus(Enum):
    """Status of a batch item."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class BatchItem:
    """A single item in the batch queue."""
    filepath: str
    status: BatchStatus = BatchStatus.PENDING
    progress: int = 0
    message: str = ""
    result: Optional[TranscriptionResult] = None
    error: Optional[str] = None
    
    @property
    def filename(self) -> str:
        return os.path.basename(self.filepath)
    
    @property
    def is_complete(self) -> bool:
        return self.status in (BatchStatus.COMPLETE, BatchStatus.ERROR, BatchStatus.CANCELLED)


# ============================================================================
# BATCH WORKER
# ============================================================================

class BatchWorker(QThread):
    """Worker thread for processing batch items."""
    
    # Signals
    item_started = pyqtSignal(int)           # index
    item_progress = pyqtSignal(int, int, str)  # index, percentage, message
    item_finished = pyqtSignal(int, object)  # index, TranscriptionResult
    item_error = pyqtSignal(int, str)        # index, error message
    batch_finished = pyqtSignal()
    
    def __init__(
        self,
        items: List[BatchItem],
        model_name: str,
        language: str = 'auto',
        translate: bool = False,
        n_threads: int = 4,
        enable_diarization: bool = False,
        num_speakers: Optional[int] = None,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)
        self.items = items
        self.model_name = model_name
        self.language = language
        self.translate = translate
        self.n_threads = n_threads
        self.enable_diarization = enable_diarization
        self.num_speakers = num_speakers
        self._cancelled = False
        self._transcriber = Transcriber()
        self._current_index = -1
    
    def cancel(self):
        """Cancel the batch processing."""
        self._cancelled = True
        self._transcriber.cancel()
    
    def run(self):
        """Process all items in sequence."""
        for i, item in enumerate(self.items):
            if self._cancelled:
                item.status = BatchStatus.CANCELLED
                continue
            
            if item.status != BatchStatus.PENDING:
                continue
            
            self._current_index = i
            self._process_item(i, item)
        
        self.batch_finished.emit()
    
    def _process_item(self, index: int, item: BatchItem):
        """Process a single batch item."""
        item.status = BatchStatus.PROCESSING
        self.item_started.emit(index)
        
        # Create synchronous processing using events
        import threading
        finished_event = threading.Event()
        error_holder = [None]
        result_holder = [None]
        
        def on_progress(pct, msg):
            item.progress = pct
            item.message = msg
            self.item_progress.emit(index, pct, msg)
        
        def on_finished(result):
            result_holder[0] = result
            finished_event.set()
        
        def on_error(error):
            error_holder[0] = error
            finished_event.set()
        
        # Start transcription
        self._transcriber.transcribe(
            filepath=item.filepath,
            model_name=self.model_name,
            language=self.language,
            translate=self.translate,
            n_threads=self.n_threads,
            enable_diarization=self.enable_diarization,
            num_speakers=self.num_speakers,
            on_progress=on_progress,
            on_finished=on_finished,
            on_error=on_error
        )
        
        # Wait for completion
        finished_event.wait()
        
        if self._cancelled:
            item.status = BatchStatus.CANCELLED
            return
        
        if error_holder[0]:
            item.status = BatchStatus.ERROR
            item.error = error_holder[0]
            self.item_error.emit(index, error_holder[0])
        else:
            item.status = BatchStatus.COMPLETE
            item.result = result_holder[0]
            item.progress = 100
            self.item_finished.emit(index, result_holder[0])


# ============================================================================
# BATCH PROCESSOR
# ============================================================================

class BatchProcessor(QObject):
    """
    Manage batch processing of multiple audio/video files.
    
    Usage:
        processor = BatchProcessor()
        processor.add_files(["/path/to/file1.mp4", "/path/to/file2.mp4"])
        processor.start(model_name="base", language="auto")
    """
    
    # Signals (forwarded from worker)
    item_started = pyqtSignal(int)
    item_progress = pyqtSignal(int, int, str)
    item_finished = pyqtSignal(int, object)
    item_error = pyqtSignal(int, str)
    batch_finished = pyqtSignal()
    
    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._items: List[BatchItem] = []
        self._worker: Optional[BatchWorker] = None
    
    @property
    def items(self) -> List[BatchItem]:
        """Get all batch items."""
        return self._items.copy()
    
    @property
    def count(self) -> int:
        """Get number of items in queue."""
        return len(self._items)
    
    @property
    def pending_count(self) -> int:
        """Get number of pending items."""
        return sum(1 for item in self._items if item.status == BatchStatus.PENDING)
    
    @property
    def complete_count(self) -> int:
        """Get number of completed items."""
        return sum(1 for item in self._items if item.status == BatchStatus.COMPLETE)
    
    @property
    def is_processing(self) -> bool:
        """Check if batch is currently processing."""
        return self._worker is not None and self._worker.isRunning()
    
    def add_file(self, filepath: str) -> bool:
        """Add a file to the batch queue."""
        if not os.path.isfile(filepath):
            return False
        
        # Check for duplicates
        for item in self._items:
            if item.filepath == filepath:
                return False
        
        self._items.append(BatchItem(filepath=filepath))
        return True
    
    def add_files(self, filepaths: List[str]) -> int:
        """Add multiple files. Returns count of successfully added files."""
        count = 0
        for path in filepaths:
            if self.add_file(path):
                count += 1
        return count
    
    def remove_item(self, index: int) -> bool:
        """Remove an item from the queue (only if not processing)."""
        if index < 0 or index >= len(self._items):
            return False
        
        item = self._items[index]
        if item.status == BatchStatus.PROCESSING:
            return False
        
        self._items.pop(index)
        return True
    
    def clear(self):
        """Clear all items (cancels if processing)."""
        if self.is_processing:
            self.cancel()
        self._items.clear()
    
    def clear_completed(self):
        """Remove completed and errored items."""
        self._items = [item for item in self._items if not item.is_complete]
    
    def start(
        self,
        model_name: str,
        language: str = 'auto',
        translate: bool = False,
        n_threads: int = 4,
        enable_diarization: bool = False,
        num_speakers: Optional[int] = None
    ):
        """Start processing the batch queue."""
        if self.is_processing:
            return
        
        if not self._items:
            return
        
        # Reset pending items
        for item in self._items:
            if item.status in (BatchStatus.ERROR, BatchStatus.CANCELLED):
                item.status = BatchStatus.PENDING
                item.progress = 0
                item.error = None
        
        # Create and start worker
        self._worker = BatchWorker(
            items=self._items,
            model_name=model_name,
            language=language,
            translate=translate,
            n_threads=n_threads,
            enable_diarization=enable_diarization,
            num_speakers=num_speakers
        )
        
        # Connect signals
        self._worker.item_started.connect(self.item_started.emit)
        self._worker.item_progress.connect(self.item_progress.emit)
        self._worker.item_finished.connect(self.item_finished.emit)
        self._worker.item_error.connect(self.item_error.emit)
        self._worker.batch_finished.connect(self._on_batch_finished)
        
        self._worker.start()
    
    def cancel(self):
        """Cancel the current batch processing."""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()
    
    def _on_batch_finished(self):
        """Handle batch completion."""
        self._worker = None
        self.batch_finished.emit()
    
    def get_results(self) -> List[TranscriptionResult]:
        """Get all successful transcription results."""
        return [item.result for item in self._items 
                if item.status == BatchStatus.COMPLETE and item.result]
    
    def export_all(
        self,
        output_dir: str,
        format_key: str = 'txt'
    ) -> List[str]:
        """
        Export all completed transcriptions to a directory.
        
        Args:
            output_dir: Directory to save files
            format_key: Export format ('txt', 'srt', 'vtt', 'json')
        
        Returns:
            List of created file paths
        """
        from exporters import export_result
        
        os.makedirs(output_dir, exist_ok=True)
        created_files = []
        
        for item in self._items:
            if item.status != BatchStatus.COMPLETE or not item.result:
                continue
            
            # Generate output filename
            base_name = os.path.splitext(item.filename)[0]
            ext = 'txt' if format_key in ('txt', 'txt_ts') else format_key
            output_path = os.path.join(output_dir, f"{base_name}.{ext}")
            
            try:
                export_result(item.result, output_path, format_key)
                created_files.append(output_path)
            except Exception:
                pass
        
        return created_files


# ============================================================================
# CLI FOR TESTING
# ============================================================================

if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    processor = BatchProcessor()
    print(f"Batch processor created")
    print(f"Items: {processor.count}")
    print(f"Processing: {processor.is_processing}")
    
    # Test adding files
    test_files = [
        "/path/to/file1.mp4",
        "/path/to/file2.wav",
    ]
    
    for f in test_files:
        result = processor.add_file(f)
        print(f"Added {f}: {result}")
    
    print(f"\nTotal items: {processor.count}")
    print(f"Pending: {processor.pending_count}")
