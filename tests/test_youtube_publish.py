from pathlib import Path

from application.youtube_publish import (
    DESCRIPTION_MAX_BYTES,
    TAGS_MAX_CHARS,
    build_package,
    find_video_source,
    fit_tags,
    normalize_titles,
    parse_tags,
    tags_length,
    validate_package,
)


def _video(tmp_path: Path, name: str = "talk.mp4") -> Path:
    path = tmp_path / name
    path.write_bytes(b"x")
    return path


def _kinds(pkg) -> set[str]:
    return {issue.kind for issue in validate_package(pkg)}


def _pkg(tmp_path: Path, **overrides):
    args = dict(video_path=_video(tmp_path), titles=["A title"], description="d", tags="a, b")
    args.update(overrides)
    return build_package(**args)


def test_normalize_titles_strips_numbering_quotes_blanks_and_duplicates():
    raw = ['1. "First"\n\n2) «Second»', "3. first", "  "]
    assert normalize_titles(raw) == ["First", "Second"]


def test_normalize_titles_accepts_a_plain_string_and_none():
    assert normalize_titles("1. One\n2. Two") == ["One", "Two"]
    assert normalize_titles(None) == []


def test_parse_tags_handles_hashes_separators_and_case_duplicates():
    assert parse_tags("#Python, python\nAI , ,  #ml ") == ["Python", "AI", "ml"]
    assert parse_tags(["a, b", "B"]) == ["a", "b"]


def test_tags_length_counts_quotes_for_spaced_tags_and_separators():
    assert tags_length(["ab", "c d"]) == 2 + (3 + 2) + 1


def test_fit_tags_drops_from_the_end_once_over_budget():
    tags = ["x" * 200, "y" * 200, "z" * 200]
    kept, dropped = fit_tags(tags)
    assert kept == tags[:2] and dropped == tags[2:]
    assert tags_length(kept) <= TAGS_MAX_CHARS


def test_build_package_picks_title_by_index_and_falls_back(tmp_path):
    video = _video(tmp_path)
    pkg = build_package(video_path=video, titles=["A", "B"], title_index=1, description="d", tags="")
    assert pkg.title == "B"
    pkg = build_package(video_path=video, titles=["A", "B"], title_index=9, description="d", tags="")
    assert pkg.title == "A"
    assert build_package(video_path=video, titles=[], description="d", tags="").title == ""


def test_valid_package_has_no_issues(tmp_path):
    assert validate_package(_pkg(tmp_path)) == []


def test_audio_source_is_not_a_video(tmp_path):
    pkg = _pkg(tmp_path, video_path=_video(tmp_path, "talk.mp3"))
    assert "source_not_video" in _kinds(pkg)


def test_missing_video_and_empty_title(tmp_path):
    pkg = _pkg(tmp_path, video_path=tmp_path / "gone.mp4", titles=[])
    assert {"video_missing", "title_empty"} <= _kinds(pkg)


def test_title_limits_and_brackets(tmp_path):
    assert "title_too_long" in _kinds(_pkg(tmp_path, titles=["x" * 101]))
    assert "title_brackets" in _kinds(_pkg(tmp_path, titles=["a <b>"]))


def test_description_limit_is_bytes_not_characters(tmp_path):
    # 2600 Cyrillic characters = 5200 bytes: under 5000 chars, over 5000 bytes.
    text = "я" * 2600
    assert len(text) < DESCRIPTION_MAX_BYTES
    assert "description_too_long" in _kinds(_pkg(tmp_path, description=text))
    assert "description_too_long" not in _kinds(_pkg(tmp_path, description="я" * 2500))


def test_description_brackets(tmp_path):
    assert "description_brackets" in _kinds(_pkg(tmp_path, description="a > b"))


def test_overlong_tags_are_a_non_blocking_warning(tmp_path):
    pkg = _pkg(tmp_path, tags=[f"tag{i:03d}" + "x" * 40 for i in range(20)])
    issues = [i for i in validate_package(pkg) if i.kind == "tags_trimmed"]
    assert len(issues) == 1 and issues[0].blocking is False


def test_thumbnail_checks(tmp_path):
    big = tmp_path / "cover.png"
    big.write_bytes(b"0" * (2 * 1024 * 1024 + 1))
    assert "thumbnail_too_large" in _kinds(_pkg(tmp_path, cover_path=big))
    assert "thumbnail_missing" in _kinds(_pkg(tmp_path, cover_path=tmp_path / "none.png"))


def test_find_video_source(tmp_path):
    assert find_video_source(_video(tmp_path)) == tmp_path / "talk.mp4"
    assert find_video_source(_video(tmp_path, "a.mp3")) is None
    assert find_video_source(tmp_path / "gone.mp4") is None
    assert find_video_source(None) is None
