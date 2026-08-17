"""
Whispered - Transcription Backend
Wrapper for pywhispercpp to handle transcription tasks
"""

import os
import re
import tempfile
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, List, Dict
from PyQt6.QtCore import QObject, pyqtSignal


from utils import get_cached_gpu
from core.base_worker import BaseWorker
from core.logger import get_logger

logger = get_logger(__name__)


class MediaConversionError(RuntimeError):
    """FFmpeg was available but could not produce a usable WAV file."""


class FFmpegUnavailableError(MediaConversionError):
    """No FFmpeg executable could be resolved for a required conversion."""


def _convert_to_wav(input_path: str) -> str:
    """
    Convert audio/video file to WAV format using FFmpeg.
    Return a unique temporary WAV path or raise a diagnostic error.
    """
    from core.external_tools import resolve_tool
    ffmpeg = resolve_tool("ffmpeg")
    if not ffmpeg:
        raise FFmpegUnavailableError("FFmpeg is not installed")

    # Reserve a unique temporary filename.  A deterministic name here lets
    # concurrent transcriptions of equally named files overwrite each other.
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    with tempfile.NamedTemporaryFile(suffix=".wav", prefix=f"{base_name}_", delete=False) as tmp:
        output_path = tmp.name

    error = "FFmpeg did not produce a usable WAV file"
    try:
        # Convert to 16kHz mono WAV (optimal for Whisper)
        result = subprocess.run([
            ffmpeg, '-y', '-i', input_path,
            '-ar', '16000',  # 16kHz sample rate
            '-ac', '1',       # Mono
            '-c:a', 'pcm_s16le',  # 16-bit PCM
            output_path
        ], capture_output=True, text=True, timeout=3600)

        if (
            result.returncode == 0
            and os.path.exists(output_path)
            and os.path.getsize(output_path) > 44
        ):
            return output_path
        detail = (result.stderr or "").strip()[-600:]
        error = f"FFmpeg exited with code {result.returncode}"
        if detail:
            error += f": {detail}"
    except subprocess.TimeoutExpired:
        error = "FFmpeg conversion timed out after 3600 seconds"
    except Exception as exc:
        error = f"FFmpeg conversion failed: {exc}"

    # FFmpeg did not produce a usable file. Do not leave an empty reservation
    # in the system temporary directory.
    try:
        if os.path.exists(output_path):
            os.unlink(output_path)
    except OSError:
        pass

    raise MediaConversionError(error)


# Formats that need FFmpeg conversion
FORMATS_NEEDING_CONVERSION = {'.m4a', '.aac', '.wma', '.opus', '.ogg', '.flac',
                               '.mp4', '.mkv', '.avi', '.mov', '.webm', '.wmv', '.flv', '.m4v'}


@dataclass
class Word:
    """A single word with its timing from word-level transcription."""
    start: float
    end: float
    text: str


@dataclass
class Segment:
    """Represents a transcription segment with timing."""
    start: float  # Start time in seconds
    end: float    # End time in seconds
    text: str     # Transcribed text
    speaker: Optional[str] = None  # Speaker label (e.g., "Speaker 1")
    words: List['Word'] = field(default_factory=list)  # Word-level timings (video mode only)


@dataclass
class TranscriptionResult:
    """Complete transcription result."""
    segments: List[Segment]
    language: str
    duration: float
    # Maps raw speaker id (e.g. "Speaker 1") to a user-assigned display name.
    # Empty by default; populated when the user renames speakers.
    speaker_names: Dict[str, str] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        """Get the complete transcription as plain text."""
        return ' '.join(seg.text.strip() for seg in self.segments)

    def speaker_label(self, speaker_id: Optional[str]) -> Optional[str]:
        """Resolve a speaker id to its display name (or the id if unmapped)."""
        if not speaker_id:
            return None
        return self.speaker_names.get(speaker_id, speaker_id)


import multiprocessing as mp
import queue

# Word-grouping thresholds for video mode phrase segmentation
_PHRASE_PAUSE_GAP = 0.6    # seconds of silence that forces a new phrase
_PHRASE_MAX_WORDS = 14     # max words before forcing a new phrase

# Offline whisper.cpp can emit very short fragments on long, continuous
# recordings.  Keep live ASR untouched, but make saved batch transcripts and
# subtitles sentence-like without creating cues that are too long to edit.
_BATCH_MERGE_MAX_GAP = 0.8
_BATCH_MERGE_MAX_DURATION = 15.0
_BATCH_MERGE_MAX_WORDS = 35
_SENTENCE_END_RE = re.compile(r"[.!?…][\"'”’»)]*$")


def _group_words_into_segments(words: list) -> list:
    """Group word-level items into phrase Segments.

    Called when word_timestamps=True: whisper emits one raw segment per word;
    this re-groups them into readable phrases using punctuation and pause heuristics.
    """
    if not words:
        return []

    segments = []
    phrase: list = []

    def _flush(phrase):
        if not phrase:
            return
        raw = ''.join(w.text for w in phrase)
        # collapse multiple spaces that whisper sometimes emits
        text = re.sub(r' {2,}', ' ', raw).strip()
        segments.append(Segment(
            start=phrase[0].start,
            end=phrase[-1].end,
            text=text,
            speaker=None,
            words=list(phrase),
        ))

    for i, word in enumerate(words):
        if phrase:
            prev = phrase[-1]
            pause = word.start - prev.end
            ends_sentence = prev.text.rstrip().endswith(('.', '?', '!', '…'))
            if ends_sentence or pause > _PHRASE_PAUSE_GAP or len(phrase) >= _PHRASE_MAX_WORDS:
                _flush(phrase)
                phrase = []
        phrase.append(word)

    _flush(phrase)
    return segments


def _coalesce_batch_segments(segments: list[Segment]) -> list[Segment]:
    """Merge adjacent offline fragments into readable sentence-level cues.

    Boundaries are preserved at sentence punctuation, meaningful pauses,
    overlaps and speaker changes.  Start/end timestamps come from the first
    and last fragment, word timing objects are retained, and joining text uses
    the same whitespace semantics as ``TranscriptionResult.full_text``.
    Live transcription intentionally does not call this helper.
    """
    if len(segments) < 2:
        return list(segments)

    result: list[Segment] = []
    group: list[Segment] = []

    def flush() -> None:
        if not group:
            return
        text = " ".join(item.text.strip() for item in group if item.text.strip())
        if not text:
            return
        result.append(Segment(
            start=group[0].start,
            end=group[-1].end,
            text=text,
            speaker=group[0].speaker,
            words=[word for item in group for word in item.words],
        ))
        group.clear()

    for segment in segments:
        if not group:
            group.append(segment)
            continue

        previous = group[-1]
        gap = segment.start - previous.end
        group_duration = segment.end - group[0].start
        group_words = sum(len(item.text.split()) for item in group)
        next_words = len(segment.text.split())
        sentence_complete = bool(_SENTENCE_END_RE.search(previous.text.strip()))
        same_speaker = previous.speaker == segment.speaker
        can_merge = (
            same_speaker
            and not sentence_complete
            and -0.05 <= gap <= _BATCH_MERGE_MAX_GAP
            and group_duration <= _BATCH_MERGE_MAX_DURATION
            and group_words + next_words <= _BATCH_MERGE_MAX_WORDS
        )
        if not can_merge:
            flush()
        group.append(segment)

    flush()
    return result


def _build_initial_prompt(vocabulary: list[str]) -> Optional[str]:
    """Build an initial-prompt string from a vocabulary list.

    Whisper uses the prompt as prior context (~200 tokens max).
    Terms are joined by comma-space; long lists are truncated.
    """
    if not vocabulary:
        return None
    # Roughly 4 chars/token; 200 tokens ≈ 800 chars
    joined = ", ".join(t.strip() for t in vocabulary if t.strip())
    if len(joined) > 800:
        logger.warning("Custom vocabulary truncated to ~200 tokens for whisper prompt")
        joined = joined[:800].rsplit(",", 1)[0]
    return joined or None


def _run_transcription_process(
    filepath: str,
    model_name: str,
    language: str,
    translate: bool,
    n_threads: int,
    enable_diarization: bool,
    num_speakers: Optional[int],
    q: mp.Queue,
    initial_prompt: Optional[str] = None,
    word_timestamps: bool = False,
    use_gpu: bool = True,
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
            try:
                temp_wav_path = _convert_to_wav(filepath)
                audio_path = temp_wav_path
            except FFmpegUnavailableError:
                from core.external_tools import ffmpeg_install_hint
                hint = ffmpeg_install_hint()
                q.put(('error',
                    f"Cannot process {file_ext} files: FFmpeg is not installed.\n\n"
                    f"Install it with:\n  {hint}\n\n"
                    "Then restart the application."
                ))
                return
            except MediaConversionError as exc:
                q.put((
                    'error',
                    f"Cannot convert {file_ext} media: {exc}",
                ))
                return

        q.put(('progress', 10, "Loading model (downloading if needed)..."))

        # Load the model (will download if not present)
        from utils import get_models_dir
        models_dir = get_models_dir()
        try:
            from pywhispercpp.model import Model
            model = Model(
                model_name,
                models_dir=models_dir,
                context_params={"use_gpu": use_gpu},
            )
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

        # Use thread count from settings. Heterogeneous by design — this is
        # the pywhispercpp kwargs bag (ints, strs, bools, floats).
        params: dict[str, Any] = {
            'n_threads': n_threads,
        }

        # Resolve language before transcribing. pywhispercpp defaults to an
        # empty language ('' — not real auto-detection) when the parameter is
        # omitted, which makes whisper.cpp decode non-English speech as if it
        # were English and fall into severe repetition-loop hallucinations.
        # detect_language=True alone only runs detection and returns no
        # segments, so auto mode needs an explicit detect-then-transcribe.
        if language != 'auto':
            params['language'] = language
        else:
            q.put(('progress', 12, "Detecting language..."))
            try:
                (detected_lang, confidence), _ = model.auto_detect_language(audio_path)
                logger.info("Auto-detected language: %s (p=%.3f)", detected_lang, confidence)
                params['language'] = detected_lang
            except Exception as e:
                logger.warning("Language auto-detection failed, falling back to English: %s", e)
                params['language'] = 'en'

        # Enable translation if requested
        if translate:
            params['translate'] = True

        # Custom vocabulary / initial prompt
        if initial_prompt:
            params['initial_prompt'] = initial_prompt
            # Custom vocabulary can contain names and other private context;
            # record only diagnostic size, never its contents.
            logger.info("Using initial prompt (%d chars)", len(initial_prompt))

        # Tweaks for improving transcription quality
        params['no_context'] = True  # Equivalent to condition_on_previous_text=False
        params['no_speech_thold'] = 0.6 # no_speech_threshold

        # Video mode: request word-level timing (one raw segment per word)
        if word_timestamps:
            params['token_timestamps'] = True
            params['split_on_word'] = True
            params['max_len'] = 1

        # Get duration for progress bar
        try:
            import wave
            with wave.open(audio_path, 'rb') as f:
                duration = f.getnframes() / float(f.getframerate())
        except Exception:
            duration = 1.0

        def segment_cb(seg):
            if duration > 1.0:
                current_time = seg.t1 / 100.0
                pct = min(89, int(20 + 70 * (current_time / duration)))
                q.put(('progress', pct, f"Transcribing... {int(current_time)}s / {int(duration)}s"))

        # Run transcription
        q.put(('progress', 20, "Transcribing audio..."))
        segments_raw = model.transcribe(audio_path, new_segment_callback=segment_cb, **params)

        q.put(('progress', 90, "Processing results..."))

        # Convert to our Segment format
        if word_timestamps:
            # segments_raw is word-level; regroup into phrase segments
            words = [Word(start=s.t0 / 100.0, end=s.t1 / 100.0, text=s.text) for s in segments_raw]
            segments = _group_words_into_segments(words)
        else:
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

        # Word-timestamp mode deliberately keeps its finer phrase grouping for
        # Cut.  The default offline path benefits from sentence-level cues.
        if not word_timestamps:
            segments = _coalesce_batch_segments(segments)

        if not segments:
            q.put(('error', "No speech detected in the audio file."))
            return

        # Calculate total duration
        duration = segments[-1].end

        # Create result
        result = TranscriptionResult(
            segments=segments,
            language=params['language'],
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
            except OSError:
                pass
        import gc
        gc.collect()
        # A process exit code alone cannot tell the parent whether a terminal
        # result was delivered.  This explicit sentinel closes that protocol.
        try:
            q.put(('terminal',))
        except Exception:
            pass

class TranscriptionWorker(BaseWorker):
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
        initial_prompt: Optional[str] = None,
        word_timestamps: bool = False,
        use_gpu: bool = True,
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
        self.initial_prompt = initial_prompt
        self.word_timestamps = word_timestamps
        self.use_gpu = use_gpu
        self._process = None

    def _on_error(self, msg: str) -> None:
        self.error.emit(msg)

    def _execute(self):
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
                q,
                self.initial_prompt,
                self.word_timestamps,
                self.use_gpu,
            )
        )
        self._process.start()

        try:
            while self._process.is_alive():
                if self.is_cancelled():
                    self.progress.emit(0, "Cancelling...")
                    q.close()
                    self._process.terminate()
                    self._process.join(timeout=5)
                    if self._process.is_alive():
                        logger.warning("Transcription process did not exit after termination; forcing stop")
                        self._process.kill()
                        self._process.join(timeout=3)
                    self.error.emit("Cancelled")
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

            # The multiprocessing feeder may publish the final message just
            # after ``is_alive`` flips to false.  Wait briefly for the
            # explicit terminal sentinel instead of using Queue.empty(),
            # which is documented as unreliable across processes.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                try:
                    msg = q.get(timeout=max(0.01, deadline - time.monotonic()))
                    if msg[0] == 'progress':
                        self.progress.emit(msg[1], msg[2])
                    elif msg[0] == 'result':
                        self.finished.emit(msg[1])
                        return
                    elif msg[0] == 'error':
                        self.error.emit(msg[1])
                        return
                    elif msg[0] == 'terminal':
                        break
                except queue.Empty:
                    break

            if not self.is_cancelled():
                self.error.emit(
                    "Transcription process exited without a result "
                    f"(exit code {self._process.exitcode})."
                )
            else:
                self.error.emit("Cancelled")

        finally:
            if self._process and self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=5)
                if self._process.is_alive():
                    logger.warning("Transcription process still alive after termination; forcing stop")
                    self._process.kill()
                    self._process.join(timeout=3)


class Transcriber:
    """High-level transcription manager."""

    def __init__(self):
        self.current_worker: Optional[TranscriptionWorker] = None
        cached_device = get_cached_gpu()
        self.gpu_type, self.gpu_name = cached_device or (
            "detecting", "Detecting hardware…"
        )

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
        initial_prompt: Optional[str] = None,
        word_timestamps: bool = False,
        use_gpu: bool = True,
        on_progress: Optional[Callable[[int, str], None]] = None,
        on_finished: Optional[Callable[[TranscriptionResult], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        models_ready: bool = False,
    ) -> Optional[TranscriptionWorker]:
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
            word_timestamps: If True, request word-level timing (video mode)
            use_gpu: Load whisper.cpp with GPU acceleration when available
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

        if not models_ready and not self.prepare_models(model_name, enable_diarization):
            if on_error:
                on_error("Model download was cancelled or failed.")
            return None

        # Create new worker
        worker = TranscriptionWorker(
            filepath=filepath,
            model_name=model_name,
            language=language,
            translate=translate,
            n_threads=n_threads,
            enable_diarization=enable_diarization,
            num_speakers=num_speakers,
            initial_prompt=initial_prompt,
            word_timestamps=word_timestamps,
            use_gpu=use_gpu,
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

    @staticmethod
    def prepare_models(model_name: str, enable_diarization: bool = False) -> bool:
        """Verify/download models from the GUI thread before worker startup.

        This method no longer imports ``ui.model_downloader`` directly.
        Callers on the GUI thread should open download dialogs themselves and
        then call this method, or provide an alternative progress mechanism.

        The bare minimum check: if the model file is present (any size > 0)
        we consider it ready and let the native parser validate it on load.
        Full integrity checks are performed by ``core.model_repository``.
        """
        try:
            from utils import get_models_dir
            import os
            model_file = os.path.join(get_models_dir(), f"ggml-{model_name}.bin")
            if not os.path.exists(model_file) or os.path.getsize(model_file) == 0:
                logger.warning(
                    "prepare_models: model file not found or empty: %s",
                    model_file,
                )
                return False
            if enable_diarization:
                # Diarization models live in the HuggingFace cache;
                # their presence is verified by the UI layer before calling here.
                pass
            return True
        except Exception as exc:
            logger.warning("Model check failed: %s", exc)
            return False

    def cancel(self):
        """Cancel the current transcription job."""
        if self.current_worker and self.current_worker.isRunning():
            self.current_worker.cancel()
            self.current_worker.wait()

    def shutdown(self) -> None:
        """Part of the Shutdownable protocol (ui/shutdownable.py). cancel()
        already no-ops when nothing is running."""
        self.cancel()
