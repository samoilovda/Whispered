"""application/cover_setup.py: a record's cover setup on disk."""

from __future__ import annotations

from application.cover_setup import (
    PHOTO_DIR,
    CoverSetup,
    PhotoSetup,
    load_setup,
    save_setup,
    store_photo,
)


def test_round_trip_keeps_everything(tmp_path):
    art = tmp_path / "talk-7"
    guest = tmp_path / "frame.png"
    guest.write_bytes(b"guest")
    kept = store_photo(art, "photo_b", str(guest))
    setup = CoverSetup(
        layout="duo", variant="auto", shuffle=3, title="T", names="A и B",
        photos={
            "photo_a": PhotoSetup(None),
            "photo_b": PhotoSetup(kept, (0.5, 0.25), 1.6),
        },
    )
    save_setup(art, setup)
    assert load_setup(art) == setup


def test_photo_is_copied_once_under_a_content_name(tmp_path):
    art = tmp_path / "talk-7"
    first = tmp_path / "a.png"
    first.write_bytes(b"one")
    kept = store_photo(art, "photo_b", str(first))
    assert kept.startswith(str(art / PHOTO_DIR))
    assert store_photo(art, "photo_b", kept) == kept

    second = tmp_path / "b.png"
    second.write_bytes(b"two")
    newer = store_photo(art, "photo_b", str(second))
    assert newer != kept
    assert [p.name for p in (art / PHOTO_DIR).iterdir()] == [newer.rsplit("/", 1)[-1]]


def test_missing_photo_and_broken_file(tmp_path):
    art = tmp_path / "talk-7"
    save_setup(art, CoverSetup("duo", "auto", photos={
        "photo_b": PhotoSetup(str(tmp_path / "gone.png")),
    }))
    assert load_setup(art) == CoverSetup("duo", "auto")

    (art / "cover.setup.json").write_text("{nope", encoding="utf-8")
    assert load_setup(art) is None
    assert load_setup(tmp_path / "nothing") is None
