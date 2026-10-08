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


def _zoom_side_by_side() -> np.ndarray:
    """1280×720 like Zoom's two-up view: black letterbox, a grey tile with a
    phone's portrait video and a name label, a full-bleed camera tile."""
    rng = np.random.default_rng(7)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[180:540, 0:640] = 28                                   # grey tile
    frame[180:540, 218:420] = rng.integers(60, 230, (360, 202, 3))  # portrait
    frame[525:535, 5:90] = 200                                   # name label
    frame[180:540, 640:1280] = rng.integers(20, 240, (360, 640, 3))
    # A door frame inside the camera picture is not a seam.
    frame[180:540, 850:853] = 250
    return frame


def test_touching_call_tiles_split_into_each_participant():
    from covers.tiles import speaker_tiles

    assert speaker_tiles(_zoom_side_by_side()) == [
        Rect(218, 180, 202, 360), Rect(640, 180, 640, 360),
    ]


def test_gallery_with_gutters_still_goes_through_detect_tiles():
    from covers.tiles import speaker_tiles

    assert len(speaker_tiles(_gallery(2, 2))) == 4


def test_single_speaker_view_is_one_picture():
    from covers.tiles import speaker_tiles

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[90:630] = np.random.default_rng(3).integers(20, 240, (540, 1280, 3))
    assert speaker_tiles(frame) == [Rect(0, 90, 1280, 540)]
