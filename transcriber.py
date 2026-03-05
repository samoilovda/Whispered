"""
Whispered - Transcription Backend
Wrapper for pywhispercpp to handle transcription tasks
"""

import os
import gc
import threading
import tempfile
import subprocess
import shutil
from dataclasses import dataclass
from typing import Callable, Optional, List
from PyQt6.QtCore import QObject, pyqtSignal, QThread

from pywhispercpp.model import Model

from utils import get_models_dir, detect_gpu
from core.logger import get_logger

logger = get_logger(__name__)


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


import multiprocessing as mp
import queue

def _run_transcription_process(
    filepath: str,
    model_name: str,
    language: str,
    translate: bool,
    n_threads: int,
    enable_diarization: bool,
    num_speakers: Optional[int],
    q: mp.Queue
):
    """Run transcription in a separate process to allow hard cancellation."""
    temp_wav_path = None
    try:
        # Check if file exists
        if not os.path.isfile(filepath):
            q.put(('error', f"File not found: {filepath}"))
            return
            
        # Check if we need to convert the file
        file_ext = os.path.splitext(filepath)[1].lower()
        audio_path = filepath
        
        if file_ext in FORMATS_NEEDING_CONVERSION:
            q.put(('progress', 5, "Converting audio format..."))
            temp_wav_path = _convert_to_wav(filepath)
            if temp_wav_path:
                audio_path = temp_wav_path
            else:
                import platform
                if platform.system() == "Darwin":
                    hint = "brew install ffmpeg"
                elif os.path.exists("/etc/fedora-release") or shutil.which("dnf"):
                    hint = "sudo dnf install ffmpeg"
                else:
                    hint = "sudo apt install ffmpeg"
                q.put(('error',
                    f"Cannot process {file_ext} files: FFmpeg is not installed.\n\n"
                    f"Install it with:\n  {hint}\n\n"
                    "Then restart the application."
                ))
                return
        
        q.put(('progress', 10, "Loading model (downloading if needed)..."))
        
        # Load the model (will download if not present)
        from utils import get_models_dir
        models_dir = get_models_dir()
        try:
            from pywhispercpp.model import Model
            model = Model(model_name, models_dir=models_dir)
        except MemoryError:
            q.put(('error',
                f"Not enough memory to load model '{model_name}'.\n\n"
                "Try selecting a smaller model (e.g. 'base' or 'small') "
                "in the Model dropdown."
            ))
            return
        except Exception as e:
            q.put(('error', f"Failed to load model '{model_name}': {str(e)}"))
            return
            
        q.put(('progress', 15, "Preparing transcription..."))
        
        # Use thread count from settings
        params = {
            'n_threads': n_threads,
        }
        
        # Set language if not auto-detect
        if language != 'auto':
            params['language'] = language
        
        # Enable translation if requested
        if translate:
            params['translate'] = True
            
        # Run transcription
        q.put(('progress', 20, "Transcribing audio..."))
        segments_raw = model.transcribe(audio_path, **params)
        
        q.put(('progress', 90, "Processing results..."))
        
        # Convert to our Segment format
        segments = []
        for seg in segments_raw:
            segments.append(Segment(
                start=seg.t0 / 100.0,
                end=seg.t1 / 100.0,
                text=seg.text,
                speaker=None
            ))
            
        # Run diarization if enabled
        if enable_diarization:
            q.put(('progress', 85, "Identifying speakers..."))
            try:
                from diarizer import Diarizer
                diarizer = Diarizer()
                if not diarizer.is_available():
                    q.put(('progress', 90, "Diarization not available, skipping..."))
                else:
                    # Run diarization
                    diarization = diarizer.diarize(
                        audio_path,
                        num_speakers=num_speakers,
                        on_progress=lambda p, m: q.put(('progress', 85 + int(p * 0.1), m))
                    )
                    
                    # Merge speaker labels with segments
                    for seg in segments:
                        midpoint = (seg.start + seg.end) / 2
                        speaker = diarization.get_speaker_at(midpoint)
                        if speaker is None:
                            speaker = diarization.get_speaker_at(seg.start)
                        seg.speaker = speaker
                    
                    q.put(('progress', 95, f"Found {diarization.num_speakers} speakers"))
            except Exception as e:
                q.put(('progress', 90, f"Diarization error: {str(e)[:30]}..."))
        
        if not segments:
            q.put(('error', "No speech detected in the audio file."))
            return
            
        # Calculate total duration
        duration = segments[-1].end
        
        # Create result
        result = TranscriptionResult(
            segments=segments,
            language=language if language != 'auto' else 'detected',
            duration=duration
        )
        
        q.put(('progress', 100, "Complete!"))
        q.put(('result', result))
        
    except Exception as e:
        error_msg = str(e)
        if 'CUDA' in error_msg or 'cuda' in error_msg:
            error_msg += "\n\nTip: Try selecting CPU mode in settings."
        q.put(('error', error_msg))
    finally:
        # Clean up temporary WAV file
        if temp_wav_path and os.path.exists(temp_wav_path):
            try:
                os.remove(temp_wav_path)
            except:
                pass
        import gc
        gc.collect()

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
        self._process = None
    
    def cancel(self):
        """Request cancellation of the transcription."""
        self._cancelled.set()
    
    def run(self):
        """Run the transcription in a separate thread, spawning a child process."""
        import multiprocessing as mp
        ctx = mp.get_context('spawn')  # Use spawn so CUDA/Qt don't conflict
        q = ctx.Queue()

        self._process = ctx.Process(
            target=_run_transcription_process,
            args=(
                self.filepath,
                self.model_name,
                self.language,
                self.translate,
                self.n_threads,
                self.enable_diarization,
                self.num_speakers,
                q
            )
        )
        self._process.start()
        
        try:
            while self._process.is_alive():
                if self._cancelled.is_set():
                    self.progress.emit(0, "Cancelling...")
                    q.close()
                    self._process.terminate()
                    self._process.join()  # wait to finish terminating
                    return

                try:
                    # Poll queue with timeout allowing us to check is_alive
                    msg = q.get(timeout=0.1)
                    if msg[0] == 'progress':
                        self.progress.emit(msg[1], msg[2])
                    elif msg[0] == 'result':
                        self.finished.emit(msg[1])
                        return
                    elif msg[0] == 'error':
                        self.error.emit(msg[1])
                        return
                except queue.Empty:
                    continue
            
            # Subprocess died without posting result or error
            # Check if there's anything left in queue
            while not q.empty():
                try:
                    msg = q.get_nowait()
                    if msg[0] == 'progress':
                        self.progress.emit(msg[1], msg[2])
                    elif msg[0] == 'result':
                        self.finished.emit(msg[1])
                        return
                    elif msg[0] == 'error':
                        self.error.emit(msg[1])
                        return
                except queue.Empty:
                    break
                    
            if not self._cancelled.is_set():
                if self._process.exitcode != 0:
                     self.error.emit(f"Transcription process crashed with exit code {self._process.exitcode}")

        finally:
            if self._process and self._process.is_alive():
                self._process.terminate()
                self._process.join()


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
            
        # Ensure models are downloaded before starting the background thread
        try:
            from ui.model_downloader import ensure_whisper_model, ensure_diarization_models
            from config import get_config
            
            # Check Whisper model
            if not ensure_whisper_model(model_name):
                if on_error:
                    on_error("Whisper model download was cancelled or failed.")
                return None  # Type hint says returns worker, returning None is acceptable on fail 
            
            # Check Diarization models if enabled
            if enable_diarization:
                config = get_config()
                if config.has_hf_token():
                    if not ensure_diarization_models(config.hf_token):
                        if on_error:
                            on_error("Speaker diarization model download was cancelled or failed.")
                        return None
        except Exception as e:
            logger.warning("Model downloader check failed: %s", e)
        
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
