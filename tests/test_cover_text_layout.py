from covers.text_layout import fit_text


def _measure(text: str, px: float) -> tuple[float, float]:
    """Monospace stand-in for QFontMetricsF: every char is ``px / 2`` wide."""
    return len(text) * px / 2, px * 1.2


def _lines(text: str, width: float, px: float = 20) -> tuple[str, ...]:
    return fit_text(
        text, (width, 1000), [px], {"max_lines": 4, "min_size": px}, _measure
    ).lines


def test_preposition_stays_with_the_next_word():
    # Greedy wrap at 24 chars would end line one with a dangling "в".
    lines = _lines("Почему психологу трудно в личной терапии?", 240)
    assert lines == ("Почему психологу трудно", "в личной терапии?")


def test_two_lines_are_balanced_not_full_plus_stub():
    lines = _lines("один два три четыре пять шесть семь", 300)
    assert len(lines) == 2
    widths = [len(line) for line in lines]
    assert max(widths) - min(widths) <= 6


def test_dash_never_starts_a_line():
    lines = _lines("Тревога — это сигнал, а не приговор", 200)
    assert not any(line.startswith("—") for line in lines)


def test_text_that_fits_stays_on_one_line():
    assert _lines("Короткий заголовок", 1000) == ("Короткий заголовок",)


def test_explicit_line_breaks_are_kept():
    assert _lines("Первая\nвторая", 1000) == ("Первая", "вторая")


def test_word_wider_than_the_box_still_renders():
    lines = _lines("Сверхдлинноесловобезпробелов и хвост", 100)
    assert "Сверхдлинноесловобезпробелов" in lines[0]


def test_ties_put_the_longer_line_first():
    # Both splits are 20 chars at the widest; the title reads top-heavy.
    lines = _lines("первоеслов второесл третьеслов", 250)
    assert lines == ("первоеслов второесл", "третьеслов")


def _fit(text: str, box: tuple[float, float], **autofit):
    return fit_text(text, box, [40], {"min_size": 10, **autofit}, _measure)


def test_short_title_grows_to_fill_the_box_on_one_line():
    result = _fit("Тревога", (700, 160), max_size=100)
    assert result.lines == ("Тревога",)
    assert result.sizes[0] == 100  # 7 chars * 50 px = 350 px wide, 120 px tall


def test_long_title_breaks_into_lines_instead_of_shrinking():
    text = "Почему психологу трудно в личной терапии?"
    result = _fit(text, (600, 200), max_size=100, max_lines=3)
    one_line = _fit(text, (600, 200), max_size=100, max_lines=1)
    assert len(result.lines) >= 2
    assert result.sizes[0] > one_line.sizes[0]
    assert not result.warning


def test_layout_respects_box_height_and_width():
    result = _fit("один два три четыре пять шесть", (300, 90), max_size=200, max_lines=4)
    assert max(result.widths) <= 300
    assert sum(result.heights) <= 90


def test_max_lines_is_a_hard_cap():
    result = _fit("а б в г д е ж з и к л м н о п", (200, 1000), max_size=40, max_lines=2)
    assert len(result.lines) <= 2 or result.warning


def test_without_max_size_text_only_shrinks():
    assert _fit("Коротко", (2000, 2000)).sizes[0] == 40


def test_explicit_breaks_are_scaled_not_rewrapped():
    result = _fit("Первая строка\nвторая", (500, 400), max_size=80)
    assert result.lines == ("Первая строка", "вторая")
    assert result.sizes[0] > 40


def test_impossible_text_warns():
    result = _fit(" ".join(["СЛОВО"] * 40), (300, 50), max_lines=2)
    assert "does not fit" in result.warning


def test_gives_up_a_little_size_for_a_longer_first_line():
    # At the widest size only "12 chars / 6+11 chars" fits; ~5% smaller
    # the top-heavy "12+6 / 11" split fits too and reads better.
    text = "абвгдежзийкл мнопрс туфхцчшщэюя"
    result = _fit(text, (480, 200), max_size=60, max_lines=2)
    assert result.lines == ("абвгдежзийкл мнопрс", "туфхцчшщэюя")
