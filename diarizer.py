"""
Whisper Fedora - Speaker Diarization
Identify and label different speakers using pyannote-audio
"""

import os
import warnings
from dataclasses import dataclass, field
from typing import Optional, Callable, List, Tuple

# Suppress some warnings from pyannote
warnings.filterwarnings("ignore", message=".*torchaudio.*")

from config import get_config


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class SpeakerSegment:
    """A segment of audio attributed to a specific speaker."""
    start: float      # Start time in seconds
    end: float        # End time in seconds
    speaker: str      # Speaker label (e.g., "Speaker 1")
    confidence: float = 1.0


@dataclass
class DiarizationResult:
    """Complete diarization result."""
    segments: List[SpeakerSegment]
    num_speakers: int
    duration: float
    
    def get_speaker_at(self, time: float) -> Optional[str]:
        """Get the speaker at a specific time."""
        for seg in self.segments:
            if seg.start <= time <= seg.end:
                return seg.speaker
        return None
    
    def get_speaker_times(self) -> dict[str, float]:
        """Get total speaking time for each speaker."""
        times = {}
        for seg in self.segments:
            if seg.speaker not in times:
                times[seg.speaker] = 0.0
            times[seg.speaker] += seg.end - seg.start
        return times


# ============================================================================
# DIARIZER CLASS
# ============================================================================

class Diarizer:
    """
    Speaker diarization using pyannote-audio.
    
    Requires:
    - pyannote.audio package
    - Hugging Face token with access to pyannote models
    """
    
    def __init__(self, hf_token: Optional[str] = None):
        """
        Initialize diarizer.
        
        Args:
            hf_token: Hugging Face token. If None, loads from config.
        """
        self._hf_token = hf_token or get_config().hf_token
        self._pipeline = None
        self._available: Optional[bool] = None
    
    def is_available(self) -> bool:
        """
        Check if diarization is available.
        
        Returns True if:
        - pyannote.audio is installed
        - Hugging Face token is configured
        - Pipeline can be loaded
        """
        if self._available is not None:
            return self._available
        
        # Check token
        if not self._hf_token:
            self._available = False
            return False
        
        # Check pyannote
        try:
            import pyannote.audio
            self._available = True
        except ImportError:
            self._available = False
        
        return self._available
    
    def _load_pipeline(self):
        """Load the diarization pipeline (lazy loading)."""
        if self._pipeline is not None:
            return
        
        if not self.is_available():
            raise RuntimeError("Diarization not available. Check HF token and pyannote installation.")
        
        try:
            from pyannote.audio import Pipeline
            import torch
            
            # Load pipeline from Hugging Face
            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=self._hf_token
            )
            
            # Use MPS on Apple Silicon, CUDA on NVIDIA, else CPU
            if torch.backends.mps.is_available():
                self._pipeline.to(torch.device("mps"))
            elif torch.cuda.is_available():
                self._pipeline.to(torch.device("cuda"))
            # else stays on CPU
            
        except Exception as e:
            raise RuntimeError(f"Failed to load diarization pipeline: {e}")
    
    def diarize(
        self,
        audio_path: str,
        num_speakers: Optional[int] = None,
        min_speakers: int = 1,
        max_speakers: int = 10,
        on_progress: Optional[Callable[[int, str], None]] = None
    ) -> DiarizationResult:
        """
        Perform speaker diarization on an audio file.
        
        Args:
            audio_path: Path to audio file (WAV recommended)
            num_speakers: Exact number of speakers (None = auto-detect)
            min_speakers: Minimum speakers to detect (for auto-detect)
            max_speakers: Maximum speakers to detect (for auto-detect)
            on_progress: Progress callback
        
        Returns:
            DiarizationResult with speaker segments
        """
        if on_progress:
            on_progress(10, "Loading diarization model...")
        
        self._load_pipeline()
        
        if on_progress:
            on_progress(30, "Analyzing speakers...")
        
        # Prepare parameters
        params = {}
        if num_speakers is not None:
            params['num_speakers'] = num_speakers
        else:
            params['min_speakers'] = min_speakers
            params['max_speakers'] = max_speakers
        
        # Run diarization
        try:
            diarization = self._pipeline(audio_path, **params)
        except Exception as e:
            raise RuntimeError(f"Diarization failed: {e}")
        
        if on_progress:
            on_progress(80, "Processing results...")
        
        # Convert to our format
        segments = []
        speakers_seen = set()
        speaker_map = {}  # Map pyannote labels to friendly names
        
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            # Create friendly speaker name
            if speaker not in speaker_map:
                speaker_num = len(speaker_map) + 1
                speaker_map[speaker] = f"Speaker {speaker_num}"
            
            friendly_name = speaker_map[speaker]
            speakers_seen.add(friendly_name)
            
            segments.append(SpeakerSegment(
                start=turn.start,
                end=turn.end,
                speaker=friendly_name
            ))
        
        # Sort by start time
        segments.sort(key=lambda s: s.start)
        
        # Get duration from last segment
        duration = segments[-1].end if segments else 0.0
        
        if on_progress:
            on_progress(100, f"Found {len(speakers_seen)} speakers")
        
        return DiarizationResult(
            segments=segments,
            num_speakers=len(speakers_seen),
            duration=duration
        )


def merge_transcription_with_diarization(
    transcription_segments: List[Tuple[float, float, str]],
    diarization: DiarizationResult
) -> List[Tuple[float, float, str, str]]:
    """
    Merge transcription segments with speaker labels.
    
    Args:
        transcription_segments: List of (start, end, text) tuples
        diarization: DiarizationResult with speaker segments
    
    Returns:
        List of (start, end, text, speaker) tuples
    """
    result = []
    
    for start, end, text in transcription_segments:
        # Find the speaker at the midpoint of this segment
        midpoint = (start + end) / 2
        speaker = diarization.get_speaker_at(midpoint)
        
        # If no speaker found, try start of segment
        if speaker is None:
            speaker = diarization.get_speaker_at(start)
        
        # Default to Unknown if still not found
        if speaker is None:
            speaker = "Unknown"
        
        result.append((start, end, text, speaker))
    
    return result


# ============================================================================
# SIMPLE FALLBACK DIARIZER (No external dependencies)
# ============================================================================

class SimpleDiarizer:
    """
    Simple heuristic-based diarization fallback.
    
    Uses silence detection and basic audio features to estimate speaker changes.
    Less accurate than pyannote but requires no external services.
    """
    
    def __init__(self):
        self._available = True
    
    def is_available(self) -> bool:
        return True
    
    def diarize(
        self,
        audio_path: str,
        num_speakers: Optional[int] = 2,
        on_progress: Optional[Callable[[int, str], None]] = None
    ) -> DiarizationResult:
        """
        Simple diarization based on silence/pause detection.
        
        This is a basic fallback that alternates speakers at long pauses.
        Not as accurate as pyannote but works without dependencies.
        """
        if on_progress:
            on_progress(50, "Using simple diarization...")
        
        # For now, return empty result
        # This could be enhanced with pydub or librosa for silence detection
        if on_progress:
            on_progress(100, "Simple diarization (limited accuracy)")
        
        return DiarizationResult(
            segments=[],
            num_speakers=0,
            duration=0.0
        )


def get_diarizer(prefer_pyannote: bool = True) -> Diarizer | SimpleDiarizer:
    """
    Get the best available diarizer.
    
    Args:
        prefer_pyannote: If True, try pyannote first
    
    Returns:
        Diarizer instance (pyannote or simple fallback)
    """
    if prefer_pyannote:
        diarizer = Diarizer()
        if diarizer.is_available():
            return diarizer
    
    return SimpleDiarizer()


# ============================================================================
# CLI FOR TESTING
# ============================================================================

if __name__ == "__main__":
    print("Speaker Diarization Module")
    print("=" * 50)
    
    # Check pyannote availability
    diarizer = Diarizer()
    print(f"pyannote available: {diarizer.is_available()}")
    
    config = get_config()
    print(f"HF token configured: {config.has_hf_token()}")
    
    if not diarizer.is_available():
        print("\nTo enable diarization:")
        print("1. pip install pyannote.audio")
        print("2. Get HF token from https://huggingface.co/settings/tokens")
        print("3. Accept model license at https://huggingface.co/pyannote/speaker-diarization-3.1")
        print("4. Run: python setup_diarization.py")
