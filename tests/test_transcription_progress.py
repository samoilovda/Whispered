"""Unit tests for ui/transcription_progress.py — no Qt required at all."""

from core.i18n import load_locale
from ui.transcription_progress import (
    format_eta,
    localized_progress,
    timeline_stage_for_progress,
)


class TestLocalizedProgress:
    def test_exact_match_is_translated(self):
        assert localized_progress("Transcribing audio...") != "Transcribing audio..."

    def test_transcribing_time_pattern(self):
        result = localized_progress("Transcribing... 12s / 34s")
        assert "12" in result and "34" in result

    def test_speakers_found_pattern(self):
        result = localized_progress("Found 3 speakers")
        assert "3" in result

    def test_diarization_error_extracts_detail(self):
        result = localized_progress("Diarization error: no HF token")
        assert "no HF token" in result

    def test_unknown_message_passes_through_unchanged(self):
        assert localized_progress("Some future worker message") == "Some future worker message"


class TestTimelineStageForProgress:
    def test_converting_maps_to_extract_stage(self):
        stage, local = timeline_stage_for_progress(3, "Converting audio format...")
        assert stage == 1
        assert local == 30

    def test_extract_keyword_also_maps_to_stage_one(self):
        stage, _ = timeline_stage_for_progress(1, "Extracting audio...")
        assert stage == 1

    def test_convert_stage_caps_at_100(self):
        _, local = timeline_stage_for_progress(50, "Converting audio format...")
        assert local == 100

    def test_diarization_maps_to_stage_three(self):
        stage, local = timeline_stage_for_progress(90, "Identifying speakers...")
        assert stage == 3
        assert local == 50

    def test_diarization_stage_clamped_below_zero(self):
        _, local = timeline_stage_for_progress(50, "diarizing")
        assert local == 0

    def test_diarization_stage_clamped_above_100(self):
        _, local = timeline_stage_for_progress(100, "diarizing")
        assert local == 100

    def test_default_maps_to_transcribe_stage(self):
        stage, local = timeline_stage_for_progress(50, "Transcribing audio...")
        assert stage == 2
        assert local == 50

    def test_transcribe_stage_clamped_below_zero(self):
        _, local = timeline_stage_for_progress(0, "Transcribing audio...")
        assert local == 0

    def test_transcribe_stage_clamped_above_100(self):
        _, local = timeline_stage_for_progress(100, "Transcribing audio...")
        assert local == 100

    def test_case_insensitive_matching(self):
        stage, _ = timeline_stage_for_progress(3, "CONVERTING...")
        assert stage == 1


class TestFormatEta:
    def setup_method(self):
        # Locale is process-global state (core/i18n.py); pin it so these
        # string comparisons don't depend on whatever another test file
        # left it as.
        load_locale("en")

    def test_seconds_only_under_a_minute(self):
        # 10s elapsed at 50% done -> 10s remaining
        result = format_eta(elapsed_seconds=10.0, percentage=50)
        assert result == "~10s left"

    def test_minutes_and_seconds_when_over_a_minute(self):
        # 60s elapsed at 20% done -> 240s (4 min) remaining
        result = format_eta(elapsed_seconds=60.0, percentage=20)
        assert result == "~4m 0s left"

    def test_exact_minute_boundary_shows_zero_seconds(self):
        # 60s elapsed at 50% done -> 60s (1 min 0s) remaining
        result = format_eta(elapsed_seconds=60.0, percentage=50)
        assert result == "~1m 0s left"
