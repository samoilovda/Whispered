"""Machine-readable compatibility matrix for live capture acceptance."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CompatibilityStatus(str, Enum):
    NOT_RUN = "not run"
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


SUPPORTED_APPS = ("Zoom", "Google Meet", "Microsoft Teams")
CHECKS = ("capture", "permissions", "device_switch", "echo", "stop")


@dataclass(frozen=True, slots=True)
class CompatibilityCase:
    """One app run in the matrix."""

    application: str
    duration_minutes: int = 20
    checks: dict[str, CompatibilityStatus] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self) -> None:
        if self.application not in SUPPORTED_APPS:
            raise ValueError(f"unsupported application: {self.application}")
        if self.duration_minutes < 20:
            raise ValueError("compatibility run must be at least 20 minutes")
        checks = dict(self.checks)
        unknown = set(checks) - set(CHECKS)
        if unknown:
            raise ValueError(f"unknown compatibility checks: {sorted(unknown)}")
        object.__setattr__(
            self,
            "checks",
            {check: checks.get(check, CompatibilityStatus.NOT_RUN) for check in CHECKS},
        )

    @property
    def status(self) -> CompatibilityStatus:
        values = set(self.checks.values())
        if CompatibilityStatus.FAIL in values:
            return CompatibilityStatus.FAIL
        if CompatibilityStatus.BLOCKED in values:
            return CompatibilityStatus.BLOCKED
        if values == {CompatibilityStatus.PASS}:
            return CompatibilityStatus.PASS
        return CompatibilityStatus.NOT_RUN


class CompatibilityMatrix:
    """Track all required app runs and render a reviewable report."""

    def __init__(self, cases: tuple[CompatibilityCase, ...] | None = None) -> None:
        self._cases = {
            case.application: case for case in (cases or tuple(CompatibilityCase(app) for app in SUPPORTED_APPS))
        }
        missing = set(SUPPORTED_APPS) - set(self._cases)
        if missing:
            raise ValueError(f"missing compatibility cases: {sorted(missing)}")

    def case(self, application: str) -> CompatibilityCase:
        try:
            return self._cases[application]
        except KeyError as exc:
            raise ValueError(f"unsupported application: {application}") from exc

    def update(
        self,
        application: str,
        *,
        checks: dict[str, CompatibilityStatus],
        notes: str = "",
        duration_minutes: int = 20,
    ) -> CompatibilityCase:
        updated = CompatibilityCase(application, duration_minutes, checks, notes)
        self._cases[application] = updated
        return updated

    def all_passed(self) -> bool:
        return all(case.status is CompatibilityStatus.PASS for case in self._cases.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            application: {
                "duration_minutes": case.duration_minutes,
                "status": case.status.value,
                "checks": {key: value.value for key, value in case.checks.items()},
                "notes": case.notes,
            }
            for application, case in self._cases.items()
        }

    def to_markdown(self) -> str:
        lines = [
            "| Приложение | Capture | Permissions | Device switch | Echo | Stop | Итог |",
            "|---|---|---|---|---|---|---|",
        ]
        for application in SUPPORTED_APPS:
            case = self._cases[application]
            values = [case.checks[check].value for check in CHECKS]
            lines.append(f"| {application} | " + " | ".join(values) + f" | {case.status.value} |")
        return "\n".join(lines)
