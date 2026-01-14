"""
Whisper Fedora UI - Transcription Backend
Wrapper for pywhispercpp to handle transcription tasks
"""

import os
import threading
from dataclasses import dataclass
from typing import Callable, Optional, List
from PyQt6.QtCore import QObject, pyqtSignal, QThread

from pywhispercpp.model import Model

from utils import get_models_dir, detect_gpu


@dataclass
class Segment:
    """Represents a transcription segment with timing."""
    start: float  # Start time in seconds
    end: float    # End time in seconds
    text: str     # Transcribed text


@dataclass
class TranscriptionResult:
    """Complete transcription result."""
    segments: List[Segment]
    language: str
    duration: float
    
    @property
    def full_text(self) -> str:
        """Get the complete transcription as plain text."""
        return ' '.join(seg.text.strip() for seg in self.segments)


class TranscriptionWorker(QThread):
    """Worker thread for running transcription in background."""
    
    # Signals
    progress = pyqtSignal(int, str)  # (percentage, status message)
    finished = pyqtSignal(object)     # TranscriptionResult or None
    error = pyqtSignal(str)           # Error message
    
    def __init__(
        self,
        filepath: str,
        model_name: str,
        language: str = 'auto',
        translate: bool = False,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)
        self.filepath = filepath
        self.model_name = model_name
        self.language = language
        self.translate = translate
        self._cancelled = threading.Event()
    
    def cancel(self):
        """Request cancellation of the transcription."""
        self._cancelled.set()
    
    def run(self):
        """Run the transcription in a separate thread."""
        try:
            # Check if file exists
            if not os.path.isfile(self.filepath):
                self.error.emit(f"File not found: {self.filepath}")
                return
            
            self.progress.emit(5, "Loading model (downloading if needed)...")
            
            # Load the model (will download if not present)
            models_dir = get_models_dir()
            try:
                model = Model(self.model_name, models_dir=models_dir)
            except Exception as e:
                self.error.emit(f"Failed to load model '{self.model_name}': {str(e)}")
                return
            
            if self._cancelled.is_set():
                return
            
            self.progress.emit(15, "Preparing transcription...")
            
            # Configure transcription parameters
            n_threads = os.cpu_count() or 4
            # Use half the cores for better system responsiveness
            n_threads = max(2, n_threads // 2)
            
            params = {
                'n_threads': n_threads,
            }
            
            # Set language if not auto-detect
            if self.language != 'auto':
                params['language'] = self.language
            
            # Enable translation if requested
            if self.translate:
                params['translate'] = True
            
            if self._cancelled.is_set():
                return
            
            # Run transcription
            self.progress.emit(20, "Transcribing audio...")
            segments_raw = model.transcribe(self.filepath, **params)
            
            if self._cancelled.is_set():
                return
            
            self.progress.emit(90, "Processing results...")
            
            # Convert to our Segment format
            # pywhispercpp returns t0/t1 in milliseconds
            segments = []
            for seg in segments_raw:
                segments.append(Segment(
                    start=seg.t0 / 1000.0,  # Convert from milliseconds to seconds
                    end=seg.t1 / 1000.0,
                    text=seg.text
                ))
            
            if not segments:
                self.error.emit("No speech detected in the audio file.")
                return
            
            # Calculate total duration (segments is guaranteed non-empty here)
            duration = segments[-1].end
            
            # Create result
            result = TranscriptionResult(
                segments=segments,
                language=self.language if self.language != 'auto' else 'detected',
                duration=duration
            )
            
            self.progress.emit(100, "Complete!")
            self.finished.emit(result)
            
        except Exception as e:
            error_msg = str(e)
            if 'CUDA' in error_msg or 'cuda' in error_msg:
                error_msg += "\n\nTip: Try selecting CPU mode in settings."
            self.error.emit(error_msg)


class Transcriber:
    """High-level transcription manager."""
    
    def __init__(self):
        self.current_worker: Optional[TranscriptionWorker] = None
        self.gpu_type, self.gpu_name = detect_gpu()
    
    def is_busy(self) -> bool:
        """Check if a transcription is in progress."""
        return self.current_worker is not None and self.current_worker.isRunning()
    
    def transcribe(
        self,
        filepath: str,
        model_name: str,
        language: str = 'auto',
        translate: bool = False,
        on_progress: Optional[Callable[[int, str], None]] = None,
        on_finished: Optional[Callable[[TranscriptionResult], None]] = None,
        on_error: Optional[Callable[[str], None]] = None
    ) -> TranscriptionWorker:
        """
        Start a transcription job.
        
        Args:
            filepath: Path to the audio/video file
            model_name: Whisper model name (tiny, base, small, medium, large, turbo)
            language: Language code or 'auto' for auto-detection
            translate: If True, translate to English
            on_progress: Callback for progress updates (percentage, message)
            on_finished: Callback when transcription completes
            on_error: Callback for errors
        
        Returns:
            The worker thread for additional control
        """
        # Cancel any existing job
        if self.current_worker and self.current_worker.isRunning():
            self.current_worker.cancel()
            self.current_worker.wait()
        
        # Create new worker
        worker = TranscriptionWorker(
            filepath=filepath,
            model_name=model_name,
            language=language,
            translate=translate
        )
        
        # Connect signals
        if on_progress:
            worker.progress.connect(on_progress)
        if on_finished:
            worker.finished.connect(on_finished)
        if on_error:
            worker.error.connect(on_error)
        
        self.current_worker = worker
        worker.start()
        
        return worker
    
    def cancel(self):
        """Cancel the current transcription job."""
        if self.current_worker and self.current_worker.isRunning():
            self.current_worker.cancel()
            self.current_worker.wait()
    
    def get_available_models(self) -> List[str]:
        """Get list of downloaded models."""
        models_dir = get_models_dir()
        available = []
        for model_name in ['tiny', 'base', 'small', 'medium', 'large', 'turbo']:
            model_file = os.path.join(models_dir, f'ggml-{model_name}.bin')
            if os.path.exists(model_file):
                available.append(model_name)
        return available
