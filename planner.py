"""Glue between the rotation engine and Dinner records. No Home Assistant imports.

Every mutation goes through ``_commit`` so that persistence, change records,
events and listener updates always see a fully applied state.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from .models import (
    STATUS_DONE,
    STATUS_MISSED,
    STATUS_PLANNED,
    STATUS_SKIPPED,
    STATUSES,
    ChangeRecord,
    Cook,
    Dinner,
    Settings,
)
from .rotation import (
    Assignment,
    PastDinner,
    RotationEngine,
    RotationError,
    UnknownCook,
)

EVENT_CHANGED = "dinnersense_changed"
EVENT_SWAPPED = "dinnersense_swapped"
EVENT_ROLLED = "dinnersense_rolled"

FireEvent = Callable[[str, dict[str, Any]], None]


class PlannerError(Exception):
    """A planner operation was refused."""


class Planner:
    def __init__(
        self,
        cooks: list[Cook],
        settings: Settings,
        dinners: dict[date, Dinner] | None = None,
        debt: dict[str, int] | None = None,
        *,
        fire_event: FireEvent,
        save: Callable[[], None],
        now: Callable[[], datetime],
    ) -> None:
        self.cooks = sorted(cooks, key=lambda c: c.order)
        self.settings = settings
        self.dinners: dict[date, Dinner] = dinners or {}
        self.debt: dict[str, int] = debt or {}
        self._fire = fire_event
        self._save = save
        self._now = now
        self._listeners: list[Callable[[], None]] = []

    # -- listeners / lookups ---------------------------------------------------

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    def cook(self, cook_id: str) -> Cook | None:
        return next((c for c in self.cooks if c.id == cook_id), None)

    def cook_by_name(self, name: str) -> Cook | None:
        lowered = name.casefold()
        return next((c for c in self.cooks if c.name.casefold() == lowered), None)

    def dinner_on(self, day: date) -> Dinner | None:
        return self.dinners.get(day)

    @property
    def engine(self) -> RotationEngine:
        return RotationEngine(
            [c.id for c in self.cooks], self.settings.dinner_days
        )

    # -- reads -------------------------------------------------------------------

    def get_plan(self, start: date, end: date) -> list[Dinner]:
        return [d for day, d in sorted(self.dinners.items()) if start <= day <= end]

    # -- roll / generation -------------------------------------------------------

    def roll(self, today: date) -> None:
        """Close out the past, then fill the horizon. Never deletes history."""
        closed = 0
        for day, dinner in self.dinners.items():
            if day < today and dinner.status == STATUS_PLANNED:
                self._set_status(dinner, self.settings.unconfirmed_past, "roll")
                closed += 1
        added = self._fill(today)
        self._commit([(EVENT_ROLLED, {"date": today.isoformat(),
                                      "closed": closed, "added": added})])

    def regenerate(self, today: date) -> None:
        """Rebuild unlocked future planned dinners."""
        for day in [
            d
            for d, v in self.dinners.items()
            if d >= today and v.status == STATUS_PLANNED and not v.locked
        ]:
            del self.dinners[day]
        added = self._fill(today)
        self._commit([(EVENT_ROLLED, {"date": today.isoformat(),
                                      "closed": 0, "added": added})])

    def _fill(self, today: date) -> int:
        plan = _to_plan(self.dinners)
        result = self.engine.generate(
            plan, today, self.settings.horizon_days, self.debt
        )
        self.debt = result.debt
        for a in result.added:
            self.dinners[a.date] = Dinner(
                id=uuid4().hex,
                date=a.date,
                cook=a.cook_id,
                repay=a.repay,
                history=[ChangeRecord(self._stamp(), "generated", {"cook": a.cook_id})],
            )
        return len(result.added)

    def set_cooks(self, cooks: list[Cook], today: date) -> None:
        """Replace the cook list; unlocked future dinners are rebuilt."""
        self.cooks = sorted(cooks, key=lambda c: c.order)
        known = {c.id for c in self.cooks}
        for day in [
            d
            for d, v in self.dinners.items()
            if d >= today
            and v.status == STATUS_PLANNED
            and (not v.locked or v.cook not in known)
        ]:
            del self.dinners[day]
        self.debt = {k: v for k, v in self.debt.items() if k in known}
        self._fill(today)
        self._commit([])

    # -- user actions ------------------------------------------------------------

    def swap(self, day: date, cook_id: str, today: date) -> None:
        dinner = self._require_planned(day)
        try:
            result = self.engine.swap(
                _to_plan(self.dinners),
                day,
                cook_id,
                today,
                self.settings.horizon_days,
                self.debt,
                swappable=lambda a: self.dinners[a.date].status == STATUS_PLANNED
                if a.date in self.dinners
                else True,
            )
        except PastDinner as err:
            raise PlannerError(str(err)) from err
        except UnknownCook as err:
            raise PlannerError(f"Unknown cook: {cook_id}") from err
        except RotationError as err:
            raise PlannerError(str(err)) from err
        if not result.changed:
            return
        self.debt = result.debt
        # The engine may have extended the plan beyond the horizon.
        for a in result.plan.values():
            if a.date not in self.dinners:
                self.dinners[a.date] = Dinner(
                    id=uuid4().hex, date=a.date, cook=a.cook_id, repay=a.repay
                )
        partner = self.dinners[result.date_b]  # type: ignore[index]
        before = [_snapshot(dinner), _snapshot(partner)]
        dinner.cook, partner.cook = result.plan[day].cook_id, result.plan[
            partner.date
        ].cook_id
        dinner.locked = partner.locked = True
        dinner.repay = partner.repay = False
        if self.settings.menu_follows_cook:
            dinner.menu, partner.menu = partner.menu, dinner.menu
        after = [_snapshot(dinner), _snapshot(partner)]
        for d, b in zip((dinner, partner), before, strict=True):
            d.history.append(
                ChangeRecord(self._stamp(), "swap", {"from": b["cook"], "to": d.cook})
            )
        payload = {"before": before, "after": after}
        self._commit([(EVENT_SWAPPED, payload), (EVENT_CHANGED, payload)])

    def reassign(self, day: date, cook_id: str) -> None:
        dinner = self.dinners.get(day)
        if dinner is None:
            raise PlannerError(f"No dinner on {day}")
        if self.cook(cook_id) is None:
            raise PlannerError(f"Unknown cook: {cook_id}")
        if dinner.cook == cook_id:
            return
        before = _snapshot(dinner)
        dinner.history.append(
            ChangeRecord(
                self._stamp(), "reassign", {"from": dinner.cook, "to": cook_id}
            )
        )
        dinner.cook = cook_id
        dinner.locked = True
        dinner.repay = False
        self._commit(
            [(EVENT_CHANGED, {"before": [before], "after": [_snapshot(dinner)]})]
        )

    def set_status(self, day: date, status: str) -> None:
        dinner = self.dinners.get(day)
        if dinner is None:
            raise PlannerError(f"No dinner on {day}")
        if status not in STATUSES:
            raise PlannerError(f"Unknown status: {status}")
        before = _snapshot(dinner)
        self._set_status(dinner, status, "status")
        self._commit(
            [(EVENT_CHANGED, {"before": [before], "after": [_snapshot(dinner)]})]
        )

    def skip(self, day: date) -> None:
        self.set_status(day, STATUS_SKIPPED)

    def complete(self, day: date) -> None:
        self.set_status(day, STATUS_DONE)

    # -- internals ---------------------------------------------------------------

    def _require_planned(self, day: date) -> Dinner:
        dinner = self.dinners.get(day)
        if dinner is None:
            raise PlannerError(f"No dinner on {day}")
        if dinner.status != STATUS_PLANNED:
            raise PlannerError(f"Dinner on {day} is {dinner.status}, not planned")
        return dinner

    def _set_status(self, dinner: Dinner, status: str, action: str) -> None:
        if status not in (STATUS_DONE, STATUS_MISSED, STATUS_SKIPPED, STATUS_PLANNED):
            raise PlannerError(f"Unknown status: {status}")
        dinner.history.append(
            ChangeRecord(
                self._stamp(), action, {"from": dinner.status, "to": status}
            )
        )
        dinner.status = status

    def _stamp(self) -> str:
        return self._now().isoformat()

    def _commit(self, events: list[tuple[str, dict[str, Any]]]) -> None:
        """Single path for every change: persist, notify entities, fire events."""
        self._save()
        self.notify()
        for name, data in events:
            self._fire(name, data)


def _to_plan(dinners: dict[date, Dinner]) -> dict[date, Assignment]:
    return {
        day: Assignment(day, d.cook, d.locked, d.repay) for day, d in dinners.items()
    }


def _snapshot(dinner: Dinner) -> dict[str, Any]:
    return {
        "date": dinner.date.isoformat(),
        "cook": dinner.cook,
        "status": dinner.status,
        "locked": dinner.locked,
    }

