from covers.style import AUTO, pick_style

VARIANTS = ["sand", "mint", "honey", "teal"]
DECOR = ["slide_01", "slide_02", "slide_09"]


def test_same_title_gives_the_same_cover():
    first = pick_style(VARIANTS, DECOR, title="Почему психологу трудно")
    again = pick_style(VARIANTS, DECOR, title="  почему  психологу трудно ")
    assert first == again


def test_different_titles_spread_over_the_styles():
    picks = {
        pick_style(VARIANTS, DECOR, title=f"Эфир номер {n}") for n in range(40)
    }
    assert len(picks) >= 8


def test_shuffle_cycles_through_every_combination():
    picks = [
        pick_style(VARIANTS, DECOR, title="Тема", shuffle=n)
        for n in range(len(VARIANTS) * len(DECOR))
    ]
    assert len(set(picks)) == len(picks)
    assert all(a != b for a, b in zip(picks, picks[1:]))


def test_explicit_choice_is_kept():
    variant, decor = pick_style(
        VARIANTS, DECOR, variant="mint", decor_set=AUTO, title="x", shuffle=5
    )
    assert variant == "mint" and decor in DECOR
    assert pick_style(VARIANTS, DECOR, variant="teal", decor_set="slide_09") == (
        "teal", "slide_09",
    )


def test_template_without_decor_sets():
    variant, decor = pick_style(VARIANTS, [], title="x")
    assert variant in VARIANTS and decor is None
