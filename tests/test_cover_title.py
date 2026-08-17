from covers.title import parse_title_suggestions


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
