"""
Whisper Fedora UI - Transcription Backend
Wrapper for pywhispercpp to handle transcription tasks
"""

import os
import threading
import tempfile
import subprocess
import shutil
from dataclasses import dataclass
from typing import Callable, Optional, List
from PyQt6.QtCore import QObject, pyqtSignal, QThread

from pywhispercpp.model import Model

from utils import get_models_dir, detect_gpu


def _convert_to_wav(input_path: str) -> Optional[str]:
    """
    Convert audio/video file to WAV format using FFmpeg.
    Returns path to temporary WAV file, or None if FFmpeg not available.
    """
    if not shutil.which('ffmpeg'):
        return None
    
    # Create temporary file
    temp_dir = tempfile.gettempdir()
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(temp_dir, f"{base_name}_whisper_temp.wav")
    
    try:
        # Convert to 16kHz mono WAV (optimal for Whisper)
        result = subprocess.run([
            'ffmpeg', '-y', '-i', input_path,
            '-ar', '16000',  # 16kHz sample rate
            '-ac', '1',       # Mono
            '-c:a', 'pcm_s16le',  # 16-bit PCM
            output_path
        ], capture_output=True, text=True, timeout=3600)
        
        if result.returncode == 0 and os.path.exists(output_path):
            return output_path
    except (subprocess.TimeoutExpired, Exception):
        pass
    
    return None


# Formats that need FFmpeg conversion
FORMATS_NEEDING_CONVERSION = {'.m4a', '.aac', '.wma', '.opus', '.ogg', '.flac', 
                               '.mp4', '.mkv', '.avi', '.mov', '.webm', '.wmv', '.flv', '.m4v'}


@dataclass
class Segment:
    """Represents a transcription segment with timing."""
    start: float  # Start time in seconds
    end: float    # End time in seconds
    text: str     # Transcribed text
    speaker: Optional[str] = None  # Speaker label (e.g., "Speaker 1")


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
        n_threads: int = 4,
        enable_diarization: bool = False,
        num_speakers: Optional[int] = None,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)
        self.filepath = filepath
        self.model_name = model_name
        self.language = language
        self.translate = translate
        self.n_threads = n_threads
        self.enable_diarization = enable_diarization
        self.num_speakers = num_speakers
        self._cancelled = threading.Event()
    
    def cancel(self):
        """Request cancellation of the transcription."""
        self._cancelled.set()
    
    def run(self):
        """Run the transcription in a separate thread."""
        temp_wav_path = None
        try:
            # Check if file exists
            if not os.path.isfile(self.filepath):
                self.error.emit(f"File not found: {self.filepath}")
                return
            
            # Check if we need to convert the file
            file_ext = os.path.splitext(self.filepath)[1].lower()
            audio_path = self.filepath
            
            if file_ext in FORMATS_NEEDING_CONVERSION:
                self.progress.emit(5, "Converting audio format...")
                temp_wav_path = _convert_to_wav(self.filepath)
                if temp_wav_path:
                    audio_path = temp_wav_path
                else:
                    # FFmpeg not available or conversion failed, try direct if possible
                    self.progress.emit(5, "Conversion failed or FFmpeg not found. Trying direct transcription (may fail)...")
            
            if self._cancelled.is_set():
                return
            
            self.progress.emit(10, "Loading model (downloading if needed)...")
            
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
            
            # Use thread count from settings
            params = {
                'n_threads': self.n_threads,
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
            segments_raw = model.transcribe(audio_path, **params)
            
            if self._cancelled.is_set():
                return
            
            self.progress.emit(90, "Processing results...")
            
            # Convert to our Segment format
            # pywhispercpp returns t0/t1 in centiseconds (1/100th of a second)
            segments = []
            for seg in segments_raw:
                segments.append(Segment(
                    start=seg.t0 / 100.0,  # Convert from centiseconds to seconds
                    end=seg.t1 / 100.0,
                    text=seg.text,
                    speaker=None
                ))
            
            # Run diarization if enabled
            if self.enable_diarization and not self._cancelled.is_set():
                segments = self._add_speaker_labels(segments, audio_path)
            
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
        finally:
            # Clean up temporary WAV file
            if temp_wav_path and os.path.exists(temp_wav_path):
                try:
                    os.remove(temp_wav_path)
                except:
                    pass
    
    def _add_speaker_labels(self, segments: List[Segment], audio_path: str) -> List[Segment]:
        """Add speaker labels to segments using diarization."""
        try:
            from diarizer import Diarizer
            
            self.progress.emit(85, "Identifying speakers...")
            
            diarizer = Diarizer()
            if not diarizer.is_available():
                self.progress.emit(90, "Diarization not available, skipping...")
                return segments
            
            # Run diarization
            diarization = diarizer.diarize(
                audio_path,
                num_speakers=self.num_speakers,
                on_progress=lambda p, m: self.progress.emit(85 + int(p * 0.1), m)
            )
            
            # Merge speaker labels with segments
            for seg in segments:
                midpoint = (seg.start + seg.end) / 2
                speaker = diarization.get_speaker_at(midpoint)
                if speaker is None:
                    speaker = diarization.get_speaker_at(seg.start)
                seg.speaker = speaker
            
            self.progress.emit(95, f"Found {diarization.num_speakers} speakers")
            return segments
            
        except Exception as e:
            self.progress.emit(90, f"Diarization error: {str(e)[:30]}...")
            return segments  # Return original segments without speaker labels


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
        n_threads: int = 4,
        enable_diarization: bool = False,
        num_speakers: Optional[int] = None,
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
            n_threads: Number of CPU threads to use
            enable_diarization: If True, identify speakers
            num_speakers: Number of speakers (None = auto-detect)
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
            translate=translate,
            n_threads=n_threads,
            enable_diarization=enable_diarization,
            num_speakers=num_speakers
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
