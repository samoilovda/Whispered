from core.fuzzy import match_score


def test_empty_query_matches_everything():
    assert match_score("", "Anything") == 0.0


def test_word_prefix_initials_and_scattered_letters():
    assert match_score("зап", "Новая запись") is not None
    assert match_score("нз", "Новая запись") is not None
    assert match_score("экпрт", "Экспорт…") is not None
    assert match_score("xyz", "Новая запись") is None


def test_every_word_must_match():
    assert match_score("new rec", "New record") is not None
    assert match_score("new zebra", "New record") is None


def test_prefix_ranks_above_infix_and_scatter():
    prefix = match_score("exp", "Export…")
    infix = match_score("por", "Export…")
    scatter = match_score("ept", "Export…")
    assert prefix > infix > scatter
