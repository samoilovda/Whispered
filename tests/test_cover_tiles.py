import numpy as np

from covers.tiles import Rect, detect_tiles, pick_tile, score_frame


def _gallery(rows: int, columns: int) -> np.ndarray:
    tile_w, tile_h, gutter = 80, 50, 6
    image = np.zeros((rows * tile_h + (rows - 1) * gutter,
                      columns * tile_w + (columns - 1) * gutter, 3), dtype=np.uint8)
    rng = np.random.default_rng(5)
    for row in range(rows):
        for column in range(columns):
            y, x = row * (tile_h + gutter), column * (tile_w + gutter)
            image[y:y + tile_h, x:x + tile_w] = rng.integers(20, 240, (tile_h, tile_w, 3))
    return image


def test_detects_synthetic_two_by_two_gallery():
    tiles = detect_tiles(_gallery(2, 2))
    assert len(tiles) == 4
    assert tiles[0] == Rect(0, 0, 80, 50)
    assert tiles[-1] == Rect(86, 56, 80, 50)


def test_speaker_view_falls_back_to_whole_frame():
    image = np.random.default_rng(1).integers(0, 255, (90, 160, 3), dtype=np.uint8)
    assert detect_tiles(image) == [Rect(0, 0, 160, 90)]
    assert score_frame(image) > 0


def test_pick_tile_prefers_face_and_respects_exclusion():
    tiles = [Rect(0, 0, 100, 60), Rect(100, 0, 100, 60)]
    assert pick_tile(tiles, [Rect(130, 10, 20, 20)]) == tiles[1]
    assert pick_tile(tiles, [], exclude=tiles[0]) == tiles[1]
