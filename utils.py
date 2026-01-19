"""
Whisper Fedora UI - Utility Functions
"""

import os
import platform
import subprocess
import shutil
from typing import Optional, Tuple


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


def get_file_extension(filepath: str) -> str:
    """Get the lowercase file extension."""
    return os.path.splitext(filepath)[1].lower()


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


def detect_gpu() -> Tuple[str, str]:
    """
    Detect available GPU acceleration.
    Returns: (gpu_type, description)
    - gpu_type: 'cuda', 'rocm', 'metal', or 'cpu'
    """
    # Check for NVIDIA CUDA
    if shutil.which('nvidia-smi'):
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                gpu_name = result.stdout.strip().split('\n')[0]
                return ('cuda', f"NVIDIA {gpu_name}")
        except (subprocess.TimeoutExpired, Exception):
            pass
    
    # Check for AMD ROCm
    if shutil.which('rocminfo'):
        try:
            result = subprocess.run(
                ['rocminfo'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                # Parse GPU name from rocminfo output
                for line in result.stdout.split('\n'):
                    if 'Marketing Name:' in line:
                        gpu_name = line.split(':')[1].strip()
                        return ('rocm', f"AMD {gpu_name}")
                return ('rocm', "AMD GPU (ROCm)")
        except (subprocess.TimeoutExpired, Exception):
            pass
    
    # Check for Apple Metal (macOS with Apple Silicon)
    if platform.system() == 'Darwin':
        try:
            result = subprocess.run(
                ['sysctl', '-n', 'machdep.cpu.brand_string'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                chip_name = result.stdout.strip()
                if 'Apple' in chip_name:
                    return ('metal', f"Apple Metal ({chip_name})")
        except (subprocess.TimeoutExpired, Exception):
            pass
    
    return ('cpu', "CPU (No GPU detected)")


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
    """Get the models directory path."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
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
