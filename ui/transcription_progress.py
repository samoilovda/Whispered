"""
Whispered UI - Transcription progress formatting
Pure functions turning a raw (percentage, message) progress update from
the transcriber into UI-ready text and timeline positions.

Deliberately free of any PyQt import: ui/main_window.py pulls in
QMainWindow and friends, which the stubbed Qt in tests/conftest.py does
not provide, so nothing in that module can be exercised by the fast
unit-test suite — only the slower real-Qt suite (tests_qt/). Keeping
this formatting logic here instead lets it be tested directly.
"""

from __future__ import annotations

import re

from core.i18n import tr


def localized_progress(message: str) -> str:
    """Translate known worker progress messages emitted across processes."""
    exact = {
        "Converting audio format...": "progress_converting",
        "Loading model (downloading if needed)...": "progress_loading_model",
        "Preparing transcription...": "progress_preparing",
        "Detecting language...": "progress_detecting_language",
        "Transcribing audio...": "progress_transcribing",
        "Processing results...": "progress_processing_results",
        "Identifying speakers...": "progress_identifying_speakers",
        "Diarization not available, skipping...": "progress_diarization_skipped",
        "Complete!": "progress_complete",
        "Cancelling...": "progress_cancelling",
    }
    if message in exact:
        return tr(exact[message])
    match = re.fullmatch(r"Transcribing\.\.\. (\d+)s / (\d+)s", message)
    if match:
        return tr("progress_transcribing_time", current=match[1], total=match[2])
    match = re.fullmatch(r"Found (\d+) speakers", message)
    if match:
        return tr("progress_speakers_found", count=match[1])
    if message.startswith("Diarization error:"):
        return tr("progress_diarization_error", error=message.split(":", 1)[1].strip())
    return message


def timeline_stage_for_progress(percentage: int, message: str) -> tuple[int, int]:
    """Map a transcription progress update to a (stage, local_fill) pair.

    Stage indices: 1=Extract, 2=Transcribe, 3=Diarize. The transcriber's
    global percentages are rescaled to a 0-100 fill within the active stage.
    """
    m = message.lower()
    if "convert" in m or "extract" in m:
        return 1, min(100, percentage * 10)  # ~5% global
    if "speaker" in m or "diariz" in m:
        return 3, max(0, min(100, int((percentage - 85) / 10 * 100)))
    # Loading / preparing / transcribing / processing results: 10-90% global
    return 2, max(0, min(100, int((percentage - 10) / 80 * 100)))


def format_eta(elapsed_seconds: float, percentage: int) -> str:
    """Format the estimated remaining time given elapsed time and percent
    complete. Caller is responsible for only calling this once ``percentage``
    is far enough along that the estimate is meaningful (see status_label
    call sites — below ~5% the ratio is too noisy to show)."""
    eta_seconds = (elapsed_seconds / percentage) * (100 - percentage)
    eta_minutes, eta_secs = divmod(int(eta_seconds), 60)
    if eta_minutes:
        return tr("status_eta_minutes", minutes=eta_minutes, seconds=eta_secs)
    return tr("status_eta_seconds", seconds=eta_secs)
