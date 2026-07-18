"""Unit tests for L14 application compatibility matrix."""

from __future__ import annotations

import pytest

from core.live import (
    CHECKS,
    SUPPORTED_APPS,
    CompatibilityCase,
    CompatibilityMatrix,
    CompatibilityStatus,
)


def test_default_matrix_covers_all_required_apps_and_checks():
    matrix = CompatibilityMatrix()
    assert tuple(matrix.as_dict()) == SUPPORTED_APPS
    assert all(set(matrix.case(app).checks) == set(CHECKS) for app in SUPPORTED_APPS)
    assert matrix.all_passed() is False
    assert "Google Meet" in matrix.to_markdown()


def test_matrix_reports_pass_only_after_every_check_passes():
    matrix = CompatibilityMatrix()
    checks = {check: CompatibilityStatus.PASS for check in CHECKS}
    matrix.update("Zoom", checks=checks, duration_minutes=21, notes="golden run")

    assert matrix.case("Zoom").status is CompatibilityStatus.PASS
    assert matrix.case("Zoom").duration_minutes == 21
    assert matrix.as_dict()["Zoom"]["notes"] == "golden run"
    assert matrix.all_passed() is False


def test_matrix_rejects_short_runs_and_unknown_checks():
    with pytest.raises(ValueError, match="at least 20"):
        CompatibilityCase("Zoom", duration_minutes=19)
    with pytest.raises(ValueError, match="unknown"):
        CompatibilityCase("Zoom", checks={"camera": CompatibilityStatus.PASS})
