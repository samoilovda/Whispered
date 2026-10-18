"""domain.cut_plan — anchors instead of bare indices (UX-06 / T06)."""

from __future__ import annotations

from domain.cut_plan import CUT_PLAN_SCHEMA_VERSION, CutPlan, plan_for, resolve
from domain.transcription import Segment


def _segs():
    return [
        Segment(0.0, 2.0, "intro", speaker="A"),
        Segment(2.0, 4.0, "um, so", speaker="B"),
        Segment(4.0, 6.0, "main point", speaker="A"),
    ]


def test_round_trip_through_dict():
    plan = plan_for(_segs(), {1}, "v2:abc")
    again = CutPlan.from_dict(plan.to_dict())
    assert again == plan
    assert resolve(again.cuts, _segs()).indices == {1}


def test_text_correction_keeps_the_cut():
    segs = _segs()
    plan = plan_for(segs, {1}, "r1")
    segs[1] = Segment(2.0, 4.0, "so", speaker="B")
    assert resolve(plan.cuts, segs).indices == {1}


def test_inserted_line_shifts_indices_but_not_the_cut():
    segs = _segs()
    plan = plan_for(segs, {2}, "r1")
    segs.insert(1, Segment(1.0, 2.0, "new line", speaker="A"))
    segs[0] = Segment(0.0, 1.0, "intro", speaker="A")
    assert resolve(plan.cuts, segs).indices == {3}


def test_split_line_is_flagged_not_guessed():
    segs = _segs()
    plan = plan_for(segs, {1}, "r1")
    segs[1:2] = [Segment(2.0, 3.0, "um,", speaker="B"), Segment(3.0, 4.0, "so", speaker="B")]
    resolution = resolve(plan.cuts, segs)
    assert resolution.indices == set()
    assert len(resolution.unresolved) == 1


def test_ambiguous_text_match_is_flagged():
    segs = _segs()
    plan = plan_for(segs, {1}, "r1")
    moved = [Segment(10.0, 11.0, "um, so", speaker="B"), Segment(12.0, 13.0, "um, so", speaker="B")]
    assert len(resolve(plan.cuts, moved).unresolved) == 1


def test_speaker_change_is_flagged():
    segs = _segs()
    plan = plan_for(segs, {1}, "r1")
    segs[1] = Segment(2.0, 4.0, "um, so", speaker="A")
    resolution = resolve(plan.cuts, segs)
    assert not resolution.indices and resolution.unresolved


def test_newer_schema_is_not_read():
    data = plan_for(_segs(), {1}, "r").to_dict()
    data["schema_version"] = CUT_PLAN_SCHEMA_VERSION + 1
    assert CutPlan.from_dict(data) is None
