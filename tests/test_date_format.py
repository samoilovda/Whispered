"""core.date_format: list groups and short stamps, localized month names."""

from datetime import datetime

import pytest

from core.date_format import date_group, group_label, parse_iso, short_stamp
from core.i18n import load_locale, plural_form, tr_count

NOW = datetime(2026, 10, 7, 15, 0)


@pytest.fixture(autouse=True)
def _english():
    load_locale("en")
    yield
    load_locale("en")


@pytest.mark.parametrize(
    "when,group",
    [
        (datetime(2026, 10, 7, 0, 5), "today"),
        (datetime(2026, 10, 6, 23, 59), "yesterday"),
        (datetime(2026, 10, 1, 12, 0), "week"),
        (datetime(2026, 9, 10, 12, 0), "month"),
        (datetime(2026, 8, 20, 12, 0), "2026-08"),
        (datetime(2025, 12, 31, 12, 0), "2025-12"),
    ],
)
def test_date_group(when, group):
    assert date_group(when, NOW) == group


def test_group_labels_in_russian():
    load_locale("ru")
    assert group_label("today", NOW) == "Сегодня"
    assert group_label("2026-08", NOW) == "Август"
    assert group_label("2025-12", NOW) == "Декабрь 2025"


def test_short_stamp_uses_locale_months_not_the_c_locale():
    load_locale("ru")
    assert short_stamp(datetime(2026, 10, 5, 22, 5), NOW) == "5 окт., 22:05"
    assert short_stamp(datetime(2026, 10, 7, 9, 3), NOW) == "09:03"
    assert short_stamp(datetime(2025, 3, 1, 9, 3), NOW) == "1 мар. 2025"
    load_locale("en")
    assert short_stamp(datetime(2026, 10, 5, 22, 5), NOW) == "Oct 5, 22:05"


def test_parse_iso_converts_utc_to_naive_local():
    parsed = parse_iso("2026-10-07T06:36:48+00:00")
    assert parsed is not None and parsed.tzinfo is None
    assert parse_iso("garbage") is None


@pytest.mark.parametrize(
    "count,form",
    [(1, "one"), (21, "one"), (2, "few"), (4, "few"), (22, "few"),
     (5, "many"), (11, "many"), (12, "many"), (14, "many"), (0, "many")],
)
def test_russian_plural_forms(count, form):
    assert plural_form(count, "ru") == form


def test_tr_count():
    load_locale("ru")
    assert tr_count("history_count", 1) == "1 запись"
    assert tr_count("history_count", 3) == "3 записи"
    assert tr_count("history_count", 45) == "45 записей"
    load_locale("en")
    assert tr_count("history_count", 1) == "1 record"
    assert tr_count("history_count", 3) == "3 records"
