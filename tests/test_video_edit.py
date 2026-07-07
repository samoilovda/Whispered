"""Tests for video_edit.mark_pauses() and times_to_indices() — pure logic, no Qt."""
import sys
import types

# Stub PyQt6 before importing video_edit so we don't need a real Qt install
_pyqt6 = types.ModuleType("PyQt6")
_pyqt6.QtCore = types.ModuleType("PyQt6.QtCore")
_pyqt6.QtCore.pyqtSignal = lambda *a, **kw: None
_pyqt6.QtCore.QObject = object
sys.modules.setdefault("PyQt6", _pyqt6)
sys.modules.setdefault("PyQt6.QtCore", _pyqt6.QtCore)

# Stub core deps that aren't needed for pure-logic tests
for _mod in ("core.logger", "core.base_worker", "core.prompts"):
    if _mod not in sys.modules:
        m = types.ModuleType(_mod)
        m.get_logger = lambda *a: __import__("logging").getLogger("test")
        m.BaseWorker = object
        m.load_prompt = lambda name, fallback="": fallback
        sys.modules[_mod] = m

from video_edit import mark_pauses, times_to_indices


class _Seg:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


# ---------------------------------------------------------------------------
# mark_pauses — duration
# ---------------------------------------------------------------------------

def test_empty():
    assert mark_pauses([]) == []


def test_normal_segment_not_marked():
    segs = [_Seg(0.0, 1.0, "Hello world")]
    assert mark_pauses(segs) == []


def test_short_segment_marked():
    segs = [_Seg(0.0, 0.3, "short")]
    assert mark_pauses(segs, min_duration=0.5) == [0]


def test_exactly_min_duration_not_marked():
    segs = [_Seg(0.0, 0.5, "fine")]
    assert mark_pauses(segs, min_duration=0.5) == []


def test_multiple_mixed():
    segs = [
        _Seg(0.0, 1.0, "Good sentence"),   # 0 — keep
        _Seg(1.0, 1.2, "uh"),              # 1 — short AND filler
        _Seg(1.2, 2.5, "another one"),     # 2 — keep
        _Seg(2.5, 2.6, "blip"),            # 3 — short
    ]
    result = mark_pauses(segs, min_duration=0.5)
    assert 0 not in result
    assert 1 in result
    assert 2 not in result
    assert 3 in result


# ---------------------------------------------------------------------------
# mark_pauses — filler words
# ---------------------------------------------------------------------------

def test_single_filler_marked():
    segs = [_Seg(0.0, 1.0, "uh")]
    assert mark_pauses(segs) == [0]


def test_filler_with_punctuation_marked():
    segs = [_Seg(0.0, 1.0, "Um,")]
    assert mark_pauses(segs) == [0]


def test_filler_mixed_language_marked():
    segs = [_Seg(0.0, 1.0, "эм")]
    assert mark_pauses(segs) == [0]


def test_non_filler_word_not_marked():
    segs = [_Seg(0.0, 1.0, "actually important content")]
    # "actually" alone would be filler but "important content" are not
    assert mark_pauses(segs) == []


def test_only_filler_multi_word_marked():
    segs = [_Seg(0.0, 2.0, "um uh")]
    assert mark_pauses(segs) == [0]


def test_empty_text_not_marked_by_filler():
    # No words → is_filler is False (bool(words) == False)
    segs = [_Seg(0.0, 2.0, "")]
    assert mark_pauses(segs) == []


def test_russian_filler_marked():
    segs = [_Seg(0.0, 1.5, "ну вот")]
    assert mark_pauses(segs) == [0]


# ---------------------------------------------------------------------------
# mark_pauses — gap_threshold
# ---------------------------------------------------------------------------

def test_gap_threshold_zero_disabled():
    # 2.0s gap between segs 0 and 1, but threshold is 0 (disabled)
    segs = [_Seg(0.0, 1.0, "first"), _Seg(3.0, 4.0, "second")]
    assert mark_pauses(segs, min_duration=0.1, gap_threshold=0.0) == []


def test_gap_threshold_triggers():
    segs = [_Seg(0.0, 1.0, "first"), _Seg(4.0, 5.0, "second")]
    result = mark_pauses(segs, min_duration=0.1, gap_threshold=2.0)
    assert 1 in result   # second segment follows a 3.0s gap > 2.0s threshold


def test_gap_threshold_first_segment_never_marked():
    segs = [_Seg(10.0, 11.0, "late start")]
    result = mark_pauses(segs, min_duration=0.1, gap_threshold=2.0)
    # No previous segment to compare against
    assert result == []


def test_gap_threshold_exact_boundary_not_marked():
    segs = [_Seg(0.0, 1.0, "a"), _Seg(3.0, 4.0, "b")]
    # gap is 2.0s exactly, threshold 2.0 → not triggered (must be strictly >)
    result = mark_pauses(segs, min_duration=0.1, gap_threshold=2.0)
    assert 1 not in result


# ---------------------------------------------------------------------------
# times_to_indices
# ---------------------------------------------------------------------------

def test_times_to_indices_exact():
    segs = [_Seg(0.0, 1.0, "a"), _Seg(1.5, 2.5, "b"), _Seg(3.0, 4.0, "c")]
    result = times_to_indices([0.0, 3.0], segs)
    assert result == [0, 2]


def test_times_to_indices_fuzzy():
    segs = [_Seg(0.0, 1.0, "a"), _Seg(5.0, 6.0, "b")]
    # 4.8 is within tolerance 1.0 of 5.0
    result = times_to_indices([4.8], segs, tolerance=1.0)
    assert result == [1]


def test_times_to_indices_out_of_tolerance():
    segs = [_Seg(0.0, 1.0, "a"), _Seg(5.0, 6.0, "b")]
    # 8.0 is 3.0s away from nearest segment — outside tolerance
    result = times_to_indices([8.0], segs, tolerance=1.0)
    assert result == []


def test_times_to_indices_no_duplicates():
    segs = [_Seg(0.0, 1.0, "a")]
    result = times_to_indices([0.0, 0.1, 0.2], segs, tolerance=1.0)
    assert result == [0]


def test_times_to_indices_empty():
    assert times_to_indices([], []) == []
    segs = [_Seg(0.0, 1.0, "a")]
    assert times_to_indices([], segs) == []


def test_times_to_indices_sorted():
    segs = [_Seg(0.0, 1.0, "a"), _Seg(5.0, 6.0, "b"), _Seg(10.0, 11.0, "c")]
    result = times_to_indices([10.0, 0.0], segs)
    assert result == [0, 2]
