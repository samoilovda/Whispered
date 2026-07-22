"""
Whispered UI - Utility Functions
"""

import os
import platform
import subprocess
import shutil
import threading
import time
from typing import Optional, Tuple


_GPU_CACHE: Optional[Tuple[str, str]] = None
_GPU_CACHE_LOCK = threading.Lock()


# Supported audio/video formats
SUPPORTED_FORMATS = {
    # Audio
    '.mp3', '.wav', '.flac', '.m4a', '.ogg', '.opus', '.wma', '.aac',
    # Video
    '.mp4', '.mkv', '.avi', '.mov', '.webm', '.wmv', '.flv', '.m4v'
}


def is_supported_format(filepath: str) -> bool:
    """Check if the file format is supported."""
    ext = os.path.splitext(filepath)[1].lower()
    return ext in SUPPORTED_FORMATS



def format_duration(seconds: float) -> str:
    """Format duration in seconds to HH:MM:SS format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_timestamp_srt(seconds: float) -> str:
    """Format seconds to SRT timestamp format (HH:MM:SS,mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_timestamp_vtt(seconds: float) -> str:
    """Format seconds to VTT timestamp format (HH:MM:SS.mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def get_cached_gpu() -> Optional[Tuple[str, str]]:
    """Return the last detected device without running system commands."""
    with _GPU_CACHE_LOCK:
        return _GPU_CACHE


def clear_gpu_cache() -> None:
    """Clear cached detection (primarily for diagnostics and tests)."""
    global _GPU_CACHE
    with _GPU_CACHE_LOCK:
        _GPU_CACHE = None


def _run_probe(
    args: list[str],
    timeout: float,
    cancel_event: Optional[threading.Event],
) -> tuple[bool, str]:
    """Run a hardware probe with prompt cancellation."""
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + timeout
        while True:
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                try:
                    process.communicate(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                return False, ""
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.communicate()
                return False, ""
            try:
                stdout, _stderr = process.communicate(timeout=min(0.2, remaining))
                return process.returncode == 0, stdout
            except subprocess.TimeoutExpired:
                continue
    except Exception:
        return False, ""


def detect_gpu(
    refresh: bool = False,
    cancel_event: Optional[threading.Event] = None,
) -> Tuple[str, str]:
    """
    Detect available GPU acceleration.
    Returns: (gpu_type, description)
    - gpu_type: 'cuda', 'rocm', 'metal', or 'cpu'
    """
    global _GPU_CACHE
    if not refresh:
        cached = get_cached_gpu()
        if cached is not None:
            return cached

    # Apple Silicon has a built-in Metal backend and needs no subprocess.
    if platform.system() == 'Darwin' and platform.machine().lower() in {'arm64', 'aarch64'}:
        detected = ('metal', "Apple Metal (Apple Silicon)")
        with _GPU_CACHE_LOCK:
            _GPU_CACHE = detected
        return detected

    detected: Tuple[str, str] = ('cpu', "CPU (No GPU detected)")

    # Check for NVIDIA CUDA
    if shutil.which('nvidia-smi'):
        success, output = _run_probe(
            ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
            5,
            cancel_event,
        )
        if success and output.strip():
            gpu_name = output.strip().split('\n')[0]
            detected = ('cuda', f"NVIDIA {gpu_name}")

    # Check for AMD ROCm
    if detected[0] == 'cpu' and shutil.which('rocminfo'):
        success, output = _run_probe(['rocminfo'], 5, cancel_event)
        if success:
            for line in output.split('\n'):
                if 'Marketing Name:' in line:
                    gpu_name = line.split(':', 1)[1].strip()
                    detected = ('rocm', f"AMD {gpu_name}")
                    break
            else:
                detected = ('rocm', "AMD GPU (ROCm)")

    if cancel_event is None or not cancel_event.is_set():
        with _GPU_CACHE_LOCK:
            _GPU_CACHE = detected
    return detected


def get_audio_duration(filepath: str) -> Optional[float]:
    """Get the duration of an audio/video file using ffprobe."""
    if not shutil.which('ffprobe'):
        return None

    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            filepath
        ], capture_output=True, text=True, timeout=10)

        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, Exception):
        pass

    return None


def get_models_dir() -> str:
    """
    Get the models directory path.
    Uses cross-platform user data directories to keep models outside the app bundle.
    """
    system = platform.system()

    if system == 'Windows':
        app_data = os.environ.get('APPDATA', os.path.expanduser('~\\AppData\\Roaming'))
        base_dir = os.path.join(app_data, 'Whispered')
    elif system == 'Darwin':
        base_dir = os.path.expanduser('~/Library/Application Support/Whispered')
    else:  # Linux and others
        base_dir = os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
        base_dir = os.path.join(base_dir, 'Whispered')

    models_dir = os.path.join(base_dir, 'models')
    os.makedirs(models_dir, exist_ok=True)
    return models_dir


# Available Whisper models
WHISPER_MODELS = [
    ('tiny', 'Tiny (~75MB) - Fastest, lowest accuracy'),
    ('base', 'Base (~142MB) - Good for most uses'),
    ('small', 'Small (~466MB) - Balanced speed/accuracy'),
    ('medium', 'Medium (~1.5GB) - High accuracy'),
    ('large-v3', 'Large v3 (~3GB) - Highest accuracy'),
    ('large-v3-turbo', 'Turbo (~1.6GB) - Fast, high accuracy'),
    ('large-v3-turbo-q5_0', 'Turbo Q5 (~547MB) - Smallest, fastest'),
    ('large-v3-turbo-q8_0', 'Turbo Q8 (~834MB) - Better quality'),
]

# Supported languages (subset of most common)
WHISPER_LANGUAGES = [
    ('auto', 'Auto-detect'),
    ('en', 'English'),
    ('es', 'Spanish'),
    ('fr', 'French'),
    ('de', 'German'),
    ('it', 'Italian'),
    ('pt', 'Portuguese'),
    ('ru', 'Russian'),
    ('ja', 'Japanese'),
    ('ko', 'Korean'),
    ('zh', 'Chinese'),
    ('ar', 'Arabic'),
    ('hi', 'Hindi'),
    ('nl', 'Dutch'),
    ('pl', 'Polish'),
    ('tr', 'Turkish'),
    ('uk', 'Ukrainian'),
    ('vi', 'Vietnamese'),
    ('th', 'Thai'),
    ('id', 'Indonesian'),
]


def language_name_for_code(code: Optional[str]) -> Optional[str]:
    """Map a whisper language code (e.g. 'ru') to its English display name,
    for use in an LLM prompt directive like "Write all output in Russian."

    Falls back to the code itself if it's not in WHISPER_LANGUAGES (still
    usually understood by the model), and returns None for falsy/'auto'
    input (nothing to resolve to).
    """
    if not code or code == 'auto':
        return None
    names = {k: v for k, v in WHISPER_LANGUAGES if k != 'auto'}
    return names.get(code, code)


# Performance modes for energy/speed tradeoff
# (mode_key, display_name, thread_multiplier, description)
# thread_multiplier: fraction of CPU cores to use
PERFORMANCE_MODES = [
    ('efficiency', '🔋 Efficiency', 0.25, 'Low CPU, battery-friendly'),
    ('balanced', '⚡ Balanced', 0.50, 'Moderate CPU usage'),
    ('performance', '🚀 Performance', 1.0, 'Maximum speed, high CPU'),
]


def get_thread_count(mode: str = 'balanced') -> int:
    """
    Get optimal thread count based on performance mode.

    Args:
        mode: 'efficiency', 'balanced', or 'performance'

    Returns:
        Number of threads to use for transcription
    """
    import os
    cpu_count = os.cpu_count() or 4

    # Find the mode's thread multiplier
    for mode_key, _, multiplier, _ in PERFORMANCE_MODES:
        if mode_key == mode:
            # Calculate threads: minimum 1, maximum cpu_count
            threads = max(1, int(cpu_count * multiplier))
            return min(threads, cpu_count)

    # Default to balanced if mode not found
    return max(2, cpu_count // 2)
