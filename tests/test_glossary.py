"""core/glossary.py and the glossary's way into the cleaning and insight prompts."""

from core.glossary import glossary_block, parse_glossary, select_terms
from core.prompts import load_prompt
from domain.transcription import Segment

GLOSSARY = """<!-- editing help, never shown to the model -->
Use these spellings.

## AI
- ChatGPT | чат джипити, чат в джипе | чат-бот OpenAI
- промпт | промт
- API | апи

## Psychology
- абьюз | абьюзер | насилие в отношениях
- травма
- баг
- Cursor | | редактор кода
"""


def _terms(text):
    return [e.term for e in select_terms(text, GLOSSARY)]


def test_parse_splits_instruction_and_entries():
    intro, entries = parse_glossary(GLOSSARY)
    assert intro == "Use these spellings."
    assert [e.term for e in entries] == [
        "ChatGPT", "промпт", "API", "абьюз", "травма", "баг", "Cursor",
    ]
    assert entries[0].variants == ("чат джипити", "чат в джипе")
    assert entries[0].note == "чат-бот OpenAI"
    assert entries[-1].variants == () and entries[-1].note == "редактор кода"


def test_parse_without_instruction():
    intro, entries = parse_glossary("## Only\n- Python | питон\n")
    assert intro == ""
    assert [e.term for e in entries] == ["Python"]


def test_garbled_forms_and_russian_endings_match():
    assert _terms("Клоду даешь или чат в Джипе, он не сделает") == ["ChatGPT"]
    assert _terms("он был абьюзером, это абьюзом и было") == ["абьюз"]
    assert _terms("после травмы") == ["травма"]
    assert _terms("напиши промты получше") == ["промпт"]
    assert _terms("через апи") == ["API"]


def test_short_keys_and_lookalikes_do_not_match():
    assert _terms("собрал багаж") == []
    assert _terms("капитал и капитан") == []
    # A Latin-only entry is not triggered by the ordinary Russian word.
    assert _terms("наведи курсор на кнопку") == []


def test_select_respects_limit_and_glossary_order():
    text = "травма, абьюз, ChatGPT"
    assert [e.term for e in select_terms(text, GLOSSARY)] == ["ChatGPT", "абьюз", "травма"]
    assert [e.term for e in select_terms(text, GLOSSARY, limit=2)] == ["ChatGPT", "абьюз"]


def test_block_lists_only_terms_found():
    block = glossary_block("спросил у чат джипити", GLOSSARY)
    assert block.startswith("Use these spellings.\n")
    assert "- ChatGPT (heard as: чат джипити, чат в джипе) — чат-бот OpenAI" in block
    assert "абьюз" not in block
    assert "editing help" not in block
    assert glossary_block("ничего такого", GLOSSARY) == ""
    assert glossary_block("чат джипити", "") == ""


def test_shipped_glossary_parses_and_has_an_instruction():
    text = load_prompt("glossary")
    intro, entries = parse_glossary(text)
    assert intro.startswith("GLOSSARY.")
    assert len(entries) > 200
    assert [e.term for e in select_terms("Клоду даешь или чат в Джипе", text)] == [
        "ChatGPT", "Claude",
    ]


def test_cleaning_prompt_carries_found_terms_only(monkeypatch):
    import text_processor

    monkeypatch.setattr(
        text_processor, "glossary_block",
        lambda text: glossary_block(text, GLOSSARY),
    )
    plain = text_processor._cleaning_prompt("просто текст")
    assert plain == text_processor.CLEANING_PROMPT_TEMPLATE.format(text="просто текст", glossary="")
    with_terms = text_processor._cleaning_prompt("спросил у чат джипити")
    assert "- ChatGPT" in with_terms
    assert with_terms.index("спросил") < with_terms.index("- ChatGPT")
    assert with_terms.rstrip().endswith("Output the cleaned text only:")


def test_insight_prompt_puts_terms_after_transcript(monkeypatch):
    import core.insights as insights

    monkeypatch.setattr(insights, "glossary_block", lambda text: glossary_block(text, GLOSSARY))
    segs = [Segment(0.0, 5.0, "абьюзер снова звонил")]
    prompt = insights._build_prompt_text("action_items", segs, notes="позвонить юристу")
    assert prompt.index("абьюзер снова") < prompt.index("- абьюз")
    assert prompt.index("- абьюз") < prompt.index("позвонить юристу")
    plain = insights._build_prompt_text("action_items", [Segment(0.0, 5.0, "hello")])
    assert "Use these spellings." not in plain
