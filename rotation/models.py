"""Plain data types for the rotation engine. No Home Assistant imports."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date


@dataclass(frozen=True)
class Assignment:
    """One cook assigned to one calendar date."""

    date: date
    cook_id: str
    locked: bool = False
    repay: bool = False  # a debt-repayment turn; does not advance the rotation

    def with_cook(self, cook_id: str, *, locked: bool) -> Assignment:
        return replace(self, cook_id=cook_id, locked=locked, repay=False)


@dataclass(frozen=True)
class AvailabilityWindow:
    """A cook is unavailable from start to end, both inclusive."""

    cook_id: str
    start: date
    end: date

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


Plan = dict[date, Assignment]
Debt = dict[str, int]


@dataclass(frozen=True)
class GenerationResult:
    plan: Plan
    added: tuple[Assignment, ...]
    debt: Debt
    # Debt as it stood right after each added date was assigned.
    debt_trace: dict[date, Debt] = field(default_factory=dict)


@dataclass(frozen=True)
class SwapResult:
    """Outcome of a swap. ``changed`` is False when the cook already had the day."""

    plan: Plan
    changed: bool
    date_a: date
    date_b: date | None
    debt: Debt
