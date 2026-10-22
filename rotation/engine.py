"""Deterministic rotation: horizon generation, swap, reassign, fairness debt.

All functions are pure: they take a plan and return a new one. ``today`` is
always passed in so tests can freeze time.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import date, timedelta

from .models import (
    Assignment,
    AvailabilityWindow,
    Debt,
    GenerationResult,
    Plan,
    SwapResult,
)

MAX_SWAP_EXTENSION_DAYS = 60


class RotationError(Exception):
    """Base class for rotation errors."""


class UnknownCook(RotationError):
    """The cook id is not part of the rotation."""


class UnknownDinner(RotationError):
    """There is no assignment on the requested date."""


class PastDinner(RotationError):
    """The operation is not allowed on dates before today."""


class NoUpcomingDinner(RotationError):
    """The cook has no upcoming dinner to swap with, even after extending."""


class RotationEngine:
    """Rotation rules for one household.

    ``cook_order`` is the rotation order. ``dinner_days`` is a set of
    ``date.weekday()`` values on which a dinner is planned.
    """

    def __init__(
        self,
        cook_order: Sequence[str],
        dinner_days: Iterable[int] = range(7),
        availability: Iterable[AvailabilityWindow] = (),
    ) -> None:
        if not cook_order:
            raise ValueError("cook_order must not be empty")
        if len(set(cook_order)) != len(cook_order):
            raise ValueError("cook_order must not contain duplicates")
        self.cook_order = tuple(cook_order)
        self.dinner_days = frozenset(dinner_days)
        self.availability = tuple(availability)

    # -- availability -----------------------------------------------------

    def is_available(self, cook_id: str, day: date) -> bool:
        return not any(
            w.cook_id == cook_id and w.covers(day) for w in self.availability
        )

    # -- generation -------------------------------------------------------

    def generate(
        self,
        plan: Plan,
        today: date,
        horizon_days: int,
        debt: Debt | None = None,
    ) -> GenerationResult:
        """Fill every dinner day in ``today .. today + horizon_days - 1``.

        Existing assignments (locked or not) and the past are never touched.
        Generation continues from the last planned cook before each date.
        """
        new_plan: Plan = dict(plan)
        new_debt: Debt = dict(debt or {})
        added: list[Assignment] = []
        for offset in range(horizon_days):
            day = today + timedelta(days=offset)
            if day.weekday() not in self.dinner_days or day in new_plan:
                continue
            cook_id, repay = self._pick(new_plan, day, new_debt)
            assignment = Assignment(day, cook_id, repay=repay)
            new_plan[day] = assignment
            added.append(assignment)
        return GenerationResult(new_plan, tuple(added), new_debt)

    def _pick(self, plan: Plan, day: date, debt: Debt) -> tuple[str, bool]:
        """Return the cook for ``day`` and whether it is a debt-repayment turn."""
        order = self.cook_order
        earlier = [d for d in plan if d < day]
        previous = plan[max(earlier)] if earlier else None
        # Repayment turns don't move the pointer, so nobody is starved by them.
        regular = [d for d in earlier if not plan[d].repay]
        pointer = plan[max(regular)].cook_id if regular else None
        start = order.index(pointer) + 1 if pointer in order else 0
        candidates = [order[(start + i) % len(order)] for i in range(len(order))]
        available = [c for c in candidates if self.is_available(c, day)]
        if not available:
            # Nobody can cook: keep the rotation moving rather than leave a gap.
            return candidates[0], False

        previous_cook = previous.cook_id if previous else None
        others = len(available) > 1
        # Repay at most every other day, and never the same cook twice in a row.
        if previous is None or not previous.repay:
            repayers = [
                c
                for c in available
                if debt.get(c, 0) > 0 and not (others and c == previous_cook)
            ]
            if repayers:
                chosen = repayers[0]
                debt[chosen] -= 1
                if debt[chosen] == 0:
                    del debt[chosen]
                return chosen, True

        pool = [c for c in available if c != previous_cook] if others else available
        chosen = pool[0]
        for skipped in candidates[: candidates.index(chosen)]:
            if not self.is_available(skipped, day):
                debt[skipped] = debt.get(skipped, 0) + 1
        return chosen, False

    # -- swap / reassign ----------------------------------------------------

    def swap(
        self,
        plan: Plan,
        day: date,
        cook_id: str,
        today: date,
        horizon_days: int,
        debt: Debt | None = None,
        swappable: Callable[[Assignment], bool] | None = None,
    ) -> SwapResult:
        """Give ``day`` to ``cook_id`` and hand that cook's next day to the old cook.

        ``swappable`` filters which of the cook's days may be the partner day
        (e.g. only still-planned dinners).
        """
        self._require_cook(cook_id)
        if day not in plan:
            raise UnknownDinner(f"No dinner planned on {day}")
        if day < today:
            raise PastDinner("Past dinners cannot be swapped; use reassign")
        current = plan[day]
        new_debt: Debt = dict(debt or {})
        if current.cook_id == cook_id:
            return SwapResult(dict(plan), False, day, None, new_debt)

        new_plan: Plan = dict(plan)
        partner = self._next_day_of(new_plan, cook_id, today, day, swappable)
        span = horizon_days
        while partner is None and span < MAX_SWAP_EXTENSION_DAYS:
            span = min(span + 7, MAX_SWAP_EXTENSION_DAYS)
            generated = self.generate(new_plan, today, span, new_debt)
            new_plan, new_debt = generated.plan, generated.debt
            partner = self._next_day_of(new_plan, cook_id, today, day, swappable)
        if partner is None:
            raise NoUpcomingDinner(f"{cook_id} has no upcoming dinner to swap with")

        old_cook = current.cook_id
        new_plan[day] = new_plan[day].with_cook(cook_id, locked=True)
        new_plan[partner] = new_plan[partner].with_cook(old_cook, locked=True)
        return SwapResult(new_plan, True, day, partner, new_debt)

    def reassign(self, plan: Plan, day: date, cook_id: str) -> Plan:
        """Change only ``day``. Allowed in the past, to correct the log."""
        self._require_cook(cook_id)
        if day not in plan:
            raise UnknownDinner(f"No dinner planned on {day}")
        new_plan: Plan = dict(plan)
        new_plan[day] = new_plan[day].with_cook(cook_id, locked=True)
        return new_plan

    # -- helpers --------------------------------------------------------------

    def _require_cook(self, cook_id: str) -> None:
        if cook_id not in self.cook_order:
            raise UnknownCook(cook_id)

    @staticmethod
    def _next_day_of(
        plan: Plan,
        cook_id: str,
        today: date,
        exclude: date,
        swappable: Callable[[Assignment], bool] | None,
    ) -> date | None:
        upcoming = [
            d
            for d, a in plan.items()
            if a.cook_id == cook_id
            and d >= today
            and d != exclude
            and (swappable is None or swappable(a))
        ]
        return min(upcoming) if upcoming else None
