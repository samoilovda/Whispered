from core.thumb_titles import parse_title_suggestions


def test_title_parser_tolerates_preamble_and_marks_long_lines():
    response = """Вот варианты:

1. КОРОТКО
ПОНЯТНЫЙ ЗАГОЛОВОК

2. ЭТА ПЕРВАЯ СТРОКА ОЧЕНЬ ДЛИННАЯ
ВТОРАЯ

3. ТРЕТИЙ
ЕЩЁ ОДИН ВАРИАНТ
"""
    result = parse_title_suggestions(response)
    assert len(result) == 3
    assert result[0].text == "КОРОТКО\nПОНЯТНЫЙ ЗАГОЛОВОК"
    assert result[1].warnings


def test_speaker_line_follows_the_recording_language_not_the_ui():
    from covers.title import join_speakers

    assert join_speakers("Денис Самойлов", "Евгений Чирков", "ru") == (
        "Денис Самойлов и Евгений Чирков")
    assert join_speakers("Ann Lee", "Bob Ray", "en") == "Ann Lee and Bob Ray"
    # Unknown language: the names' own script decides.
    assert join_speakers("Денис", "Евгений", None) == "Денис и Евгений"
    assert join_speakers("Ann", "Bob", "xx") == "Ann and Bob"
    assert join_speakers("Денис", "", "ru") == "Денис"
