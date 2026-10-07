"""Unit tests for core/youtube_description.py — no Qt required."""

# Qt and core.lm_client/core.ai_worker stand-ins come from tests/conftest.py.
from core.youtube_description import (
    BLOCK_QUESTIONS,
    BLOCK_SIGNATURE,
    BLOCK_TEXT,
    BLOCK_TIMECODES,
    DEFAULT_DESCRIPTION_BLOCKS,
    above_the_fold,
    compose_description,
    ISSUE_DUPLICATE,
    ISSUE_FIRST_MOVED,
    ISSUE_INVALID,
    ISSUE_LONG,
    ISSUE_PAST_END,
    ISSUE_TOO_CLOSE,
    check_chapters,
    compose_full_description,
    format_youtube_timestamp,
    format_chapter_lines,
    format_youtube_description,
    parse_chapter_lines,
    parse_timestamp,
)


# ── format_youtube_timestamp ─────────────────────────────────────────────────

class TestFormatYoutubeTimestamp:
    def test_zero(self):
        assert format_youtube_timestamp(0) == "0:00"

    def test_154_seconds(self):
        assert format_youtube_timestamp(154) == "2:34"

    def test_725_seconds(self):
        assert format_youtube_timestamp(725) == "12:05"

    def test_3600_seconds(self):
        assert format_youtube_timestamp(3600) == "1:00:00"

    def test_3723_seconds(self):
        assert format_youtube_timestamp(3723) == "1:02:03"

    def test_no_leading_zero_on_minutes(self):
        # YouTube format: "2:34" not "02:34"
        result = format_youtube_timestamp(154)
        assert not result.startswith("0")

    def test_seconds_always_two_digits(self):
        assert format_youtube_timestamp(65) == "1:05"


# ── format_youtube_description ───────────────────────────────────────────────

class TestFormatYoutubeDescription:
    def test_empty_input(self):
        assert format_youtube_description([]) == ""

    def test_all_empty_titles_returns_empty(self):
        chapters = [{"start": 0, "title": ""}, {"start": 10, "title": "  "}]
        assert format_youtube_description(chapters) == ""

    def test_first_entry_forced_to_zero(self):
        chapters = [{"start": 30, "title": "Intro"}, {"start": 90, "title": "Body"}]
        result = format_youtube_description(chapters)
        assert result.startswith("0:00 Intro")

    def test_sorted_ascending(self):
        chapters = [
            {"start": 90, "title": "Body"},
            {"start": 0, "title": "Intro"},
            {"start": 180, "title": "Outro"},
        ]
        lines = format_youtube_description(chapters).splitlines()
        assert lines[0].startswith("0:00")
        assert lines[1].startswith("1:30")
        assert lines[2].startswith("3:00")

    def test_skip_blank_titles(self):
        chapters = [
            {"start": 0, "title": "Intro"},
            {"start": 60, "title": ""},
            {"start": 120, "title": "End"},
        ]
        result = format_youtube_description(chapters)
        lines = result.splitlines()
        assert len(lines) == 2
        assert "Intro" in lines[0]
        assert "End" in lines[1]

    def test_skip_duplicate_timestamps(self):
        chapters = [
            {"start": 0, "title": "Intro"},
            {"start": 0, "title": "Duplicate"},
            {"start": 60, "title": "Body"},
        ]
        lines = format_youtube_description(chapters).splitlines()
        assert len(lines) == 2

    def test_skip_duplicate_timestamps_after_sort(self):
        # Both entries have start=60; second must be dropped (not strictly greater)
        chapters = [
            {"start": 0, "title": "Intro"},
            {"start": 60, "title": "Body"},
            {"start": 60, "title": "Also Body"},  # same time as previous
        ]
        lines = format_youtube_description(chapters).splitlines()
        assert len(lines) == 2
        assert "Also Body" not in format_youtube_description(chapters)

    def test_newline_separated(self):
        chapters = [
            {"start": 0, "title": "A"},
            {"start": 60, "title": "B"},
            {"start": 120, "title": "C"},
        ]
        result = format_youtube_description(chapters)
        assert result == "0:00 A\n1:00 B\n2:00 C"

    def test_float_start_coerced(self):
        chapters = [{"start": 60.9, "title": "Body"}]
        result = format_youtube_description(chapters)
        assert result.startswith("0:00")  # forced to 0

    def test_string_start_coerced(self):
        chapters = [{"start": "45", "title": "Mid"}]
        result = format_youtube_description(chapters)
        assert "Mid" in result

    def test_invalid_start_skipped(self):
        chapters = [
            {"start": "bad", "title": "Skip me"},
            {"start": 0, "title": "Keep"},
        ]
        result = format_youtube_description(chapters)
        assert "Skip me" not in result
        assert "Keep" in result


class TestMinimumChapterGap:
    """YouTube silently disables chapters if any two are closer than 10s."""

    def test_gap_below_10s_dropped(self):
        chapters = [
            {"start": 0, "title": "Intro"},
            {"start": 5, "title": "Too close"},
            {"start": 20, "title": "Body"},
        ]
        result = format_youtube_description(chapters)
        assert "Too close" not in result
        assert "Intro" in result
        assert "Body" in result

    def test_gap_exactly_10s_kept(self):
        chapters = [
            {"start": 0, "title": "Intro"},
            {"start": 10, "title": "Body"},
        ]
        lines = format_youtube_description(chapters).splitlines()
        assert len(lines) == 2

    def test_gap_measured_from_last_kept_not_last_seen(self):
        # 0, 5, 12, 25: "5" is dropped (gap 5 from 0); "12" is kept (gap 12
        # from 0, the last *kept* item, not from the dropped "5"); "25" is
        # kept (gap 13 from 12).
        chapters = [
            {"start": 0, "title": "A"},
            {"start": 5, "title": "B"},
            {"start": 12, "title": "C"},
            {"start": 25, "title": "D"},
        ]
        lines = format_youtube_description(chapters).splitlines()
        assert len(lines) == 3
        assert "A" in lines[0] and lines[0].startswith("0:00")
        assert "C" in lines[1] and lines[1].startswith("0:12")
        assert "D" in lines[2] and lines[2].startswith("0:25")

    def test_fewer_than_3_after_filtering_still_returns_result(self):
        # No hard requirement enforced by the formatter itself (it stays
        # content-agnostic); only a warning is logged.
        chapters = [{"start": 0, "title": "Only one"}, {"start": 3, "title": "Too close"}]
        result = format_youtube_description(chapters)
        assert result == "0:00 Only one"


class TestCheckChapters:
    def test_clean_list_has_no_issues(self):
        chapters = [
            {"start": 0, "title": "A"},
            {"start": 60, "title": "B"},
            {"start": 120, "title": "C"},
        ]
        check = check_chapters(chapters)
        assert check.chapters == ((0, "A"), (60, "B"), (120, "C"))
        assert check.issues == ()
        assert check.shows_on_youtube

    def test_too_close_reports_the_gap(self):
        chapters = [{"start": 0, "title": "A"}, {"start": 6, "title": "B"}]
        check = check_chapters(chapters)
        (issue,) = check.issues_of(ISSUE_TOO_CLOSE)
        assert (issue.start, issue.title, issue.seconds) == (6, "B", 6)
        assert check.chapters == ((0, "A"),)

    def test_duplicate_start_is_reported(self):
        chapters = [{"start": 0, "title": "A"}, {"start": 0, "title": "Dup"}]
        (issue,) = check_chapters(chapters).issues_of(ISSUE_DUPLICATE)
        assert issue.title == "Dup"

    def test_invalid_items_are_reported(self):
        chapters = [
            {"start": 0, "title": "A"},
            {"start": 30, "title": "  "},
            {"start": "soon", "title": "B"},
        ]
        check = check_chapters(chapters)
        assert len(check.issues_of(ISSUE_INVALID)) == 2
        assert check.chapters == ((0, "A"),)

    def test_first_moved_records_original_start(self):
        chapters = [{"start": 30, "title": "Intro"}, {"start": 90, "title": "Body"}]
        check = check_chapters(chapters)
        (issue,) = check.issues_of(ISSUE_FIRST_MOVED)
        assert issue.seconds == 30
        assert check.chapters[0] == (0, "Intro")

    def test_fewer_than_three_does_not_show_on_youtube(self):
        chapters = [{"start": 0, "title": "A"}, {"start": 60, "title": "B"}]
        assert not check_chapters(chapters).shows_on_youtube

    def test_long_chapter_is_kept_but_flagged(self):
        chapters = [
            {"start": 0, "title": "A"},
            {"start": 600, "title": "B"},
            {"start": 700, "title": "C"},
        ]
        check = check_chapters(chapters)
        (issue,) = check.issues_of(ISSUE_LONG)
        assert (issue.title, issue.seconds) == ("A", 600)
        assert len(check.chapters) == 3

    def test_last_chapter_length_uses_duration(self):
        chapters = [{"start": 0, "title": "A"}, {"start": 60, "title": "B"}]
        assert check_chapters(chapters).issues_of(ISSUE_LONG) == ()
        (issue,) = check_chapters(chapters, duration=600).issues_of(ISSUE_LONG)
        assert (issue.title, issue.seconds) == ("B", 540)

    def test_past_end_is_kept_but_flagged(self):
        chapters = [{"start": 0, "title": "A"}, {"start": 120, "title": "Late"}]
        check = check_chapters(chapters, duration=100.5)
        (issue,) = check.issues_of(ISSUE_PAST_END)
        assert issue.title == "Late"
        assert (120, "Late") in check.chapters

    def test_matches_format_youtube_description(self):
        chapters = [
            {"start": 5, "title": "A"},
            {"start": 5, "title": "A2"},
            {"start": 9, "title": "B"},
            {"start": 40, "title": "C"},
            {"start": "x", "title": "D"},
        ]
        kept = check_chapters(chapters).chapters
        expected = "\n".join(f"{format_youtube_timestamp(s)} {t}" for s, t in kept)
        assert format_youtube_description(chapters) == expected


class TestComposeFullDescription:
    def test_folds_timecodes_into_description(self):
        chapters = [
            {"start": 0, "title": "Intro"},
            {"start": 60, "title": "Body"},
            {"start": 120, "title": "Outro"},
        ]
        result = compose_full_description("Hook and summary.", chapters, "Timecodes:")
        assert result == "Hook and summary.\n\nTimecodes:\n0:00 Intro\n1:00 Body\n2:00 Outro"

    def test_custom_label_is_used(self):
        chapters = [{"start": 0, "title": "A"}, {"start": 60, "title": "B"}]
        result = compose_full_description("Desc", chapters, "Тайм-коды:")
        assert "Тайм-коды:" in result

    def test_no_description_returns_none(self):
        chapters = [{"start": 0, "title": "A"}, {"start": 60, "title": "B"}]
        assert compose_full_description(None, chapters) is None
        assert compose_full_description("", chapters) == ""

    def test_no_chapters_returns_description_unchanged(self):
        assert compose_full_description("Just a description.", None) == "Just a description."
        assert compose_full_description("Just a description.", []) == "Just a description."

    def test_chapters_producing_no_valid_timecodes_returns_description_unchanged(self):
        # All titles blank -> format_youtube_description returns ""
        chapters = [{"start": 0, "title": ""}, {"start": 60, "title": "  "}]
        result = compose_full_description("Desc only.", chapters)
        assert result == "Desc only."

    def test_default_label_is_english(self):
        chapters = [{"start": 0, "title": "A"}, {"start": 60, "title": "B"}]
        result = compose_full_description("Desc", chapters)
        assert "Timecodes:" in result


class TestChapterLines:
    def test_parse_timestamp(self):
        assert parse_timestamp("0:00") == 0
        assert parse_timestamp("2:05") == 125
        assert parse_timestamp("1:02:05") == 3725
        assert parse_timestamp(" 12:30 ") == 750
        for bad in ("", "2:5", "2:60", "1:60:00", "abc", "2", "1:02:03:04"):
            assert parse_timestamp(bad) is None, bad

    def test_format_keeps_what_youtube_would_drop(self):
        chapters = [
            {"start": 40, "title": "B"},
            {"start": 30, "title": "A"},
            {"start": 34, "title": "Close"},
            {"start": 50, "title": " "},
        ]
        assert format_chapter_lines(chapters) == "0:30 A\n0:34 Close\n0:40 B"

    def test_parse_round_trips_format(self):
        chapters = [{"start": 0, "title": "Intro"}, {"start": 3725, "title": "Late"}]
        parsed, bad = parse_chapter_lines(format_chapter_lines(chapters))
        assert (parsed, bad) == (chapters, [])

    def test_parse_accepts_separators_and_skips_blanks(self):
        text = "0:00 - Intro\n\n1:30 — Body\n2:00: End"
        parsed, bad = parse_chapter_lines(text)
        assert bad == []
        assert [c["title"] for c in parsed] == ["Intro", "Body", "End"]

    def test_parse_reports_bad_lines(self):
        parsed, bad = parse_chapter_lines("0:00 Intro\nno time here\n1:00\n9:99 Bad")
        assert parsed == [{"start": 0, "title": "Intro"}]
        assert bad == [2, 3, 4]


class TestComposeDescription:
    _CH = [{"start": 30, "title": "Intro"}, {"start": 90, "title": "Body"}, {"start": 150, "title": "End"}]
    _Q = [{"start": 95, "title": "Why?"}]

    def _compose(self, blocks, **kw):
        args = dict(text="Hook.", chapters=self._CH, questions=self._Q, signature="Sub!",
                    timecodes_label="T:", questions_label="Q:")
        args.update(kw)
        return compose_description(blocks=blocks, **args)

    def test_default_blocks(self):
        assert self._compose(DEFAULT_DESCRIPTION_BLOCKS) == (
            "Hook.\n\nT:\n0:00 Intro\n1:30 Body\n2:30 End\n\nSub!"
        )

    def test_order_is_fixed_whatever_the_selection_order(self):
        result = self._compose([BLOCK_SIGNATURE, BLOCK_QUESTIONS, BLOCK_TEXT])
        assert result == "Hook.\n\nQ:\n1:35 Why?\n\nSub!"

    def test_questions_keep_their_own_times(self):
        assert "1:35 Why?" in self._compose([BLOCK_QUESTIONS])
        assert "0:00 Why?" not in self._compose([BLOCK_QUESTIONS])

    def test_empty_blocks_are_left_out(self):
        assert self._compose([BLOCK_TEXT, BLOCK_SIGNATURE], signature="  ") == "Hook."
        assert self._compose([BLOCK_TIMECODES], chapters=[]) == ""
        assert self._compose([]) == ""

    def test_matches_compose_full_description_for_text_and_timecodes(self):
        expected = compose_full_description("Hook.", self._CH, "T:")
        assert self._compose([BLOCK_TEXT, BLOCK_TIMECODES]) == expected


class TestAboveTheFold:
    def test_short_text_is_kept_on_one_line(self):
        assert above_the_fold("Hook.\n\nMore") == "Hook. More"

    def test_long_text_is_cut_at_a_word(self):
        text = "word " * 50
        result = above_the_fold(text, limit=22)
        assert result == "word word word word…"
