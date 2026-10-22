"""Glue between the rotation engine and Dinner records. No Home Assistant imports.

Every mutation goes through ``_commit`` so that persistence, change records,
events and listener updates always see a fully applied state.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4

from .models import (
    MENU_KINDS,
    STATUS_DONE,
    STATUS_MISSED,
    STATUS_PLANNED,
    STATUS_SKIPPED,
    STATUSES,
    ChangeRecord,
    Cook,
    Dinner,
    Dish,
    MenuEntry,
    Settings,
    slugify,
)
from .rotation import (
    Assignment,
    AvailabilityWindow,
    PastDinner,
    RotationEngine,
    RotationError,
    UnknownCook,
)

EVENT_CHANGED = "dinnersense_changed"
EVENT_SWAPPED = "dinnersense_swapped"
EVENT_ROLLED = "dinnersense_rolled"
EVENT_CONFLICT = "dinnersense_conflict"

MAX_FUTURE_DAYS = 60

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
        dishes: dict[str, Dish] | None = None,
        manual_windows: list[AvailabilityWindow] | None = None,
        *,
        fire_event: FireEvent,
        save: Callable[[], None],
        now: Callable[[], datetime],
    ) -> None:
        self.cooks = sorted(cooks, key=lambda c: c.order)
        self.settings = settings
        self.dinners: dict[date, Dinner] = dinners or {}
        self.debt: dict[str, int] = debt or {}
        self.dishes: dict[str, Dish] = dishes or {}
        self.manual_windows: list[AvailabilityWindow] = manual_windows or []
        self.external_windows: list[AvailabilityWindow] = []
        self._reported_conflicts: set[tuple[date, str]] = set()
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
            [c.id for c in self.cooks],
            self.settings.dinner_days,
            [*self.manual_windows, *self.external_windows],
        )

    # -- reads -------------------------------------------------------------------

    def get_plan(self, start: date, end: date) -> list[Dinner]:
        return [d for day, d in sorted(self.dinners.items()) if start <= day <= end]

    def get_history(self, start: date, end: date, today: date) -> list[Dinner]:
        """Dinners up to and including today within the range."""
        return self.get_plan(start, min(end, today))

    def find_dish(self, title: str) -> Dish | None:
        return next((d for d in self.dishes.values() if d.matches(title)), None)

    def menu_title(self, dinner: Dinner) -> str | None:
        menu = dinner.menu
        if menu is None:
            return None
        if menu.title:
            return menu.title
        dish = self.dishes.get(menu.dish_id) if menu.dish_id else None
        return dish.name if dish else None

    # -- roll / generation -------------------------------------------------------

    def roll(self, today: date) -> None:
        """Close out the past, then fill the horizon. Never deletes history."""
        closed = 0
        for day, dinner in self.dinners.items():
            if day < today and dinner.status == STATUS_PLANNED:
                self._set_status(dinner, self.settings.unconfirmed_past, "roll")
                closed += 1
        added = self._fill(today)
        self._commit(
            [
                (
                    EVENT_ROLLED,
                    {"date": today.isoformat(), "closed": closed, "added": added},
                )
            ]
        )
        self._check_conflicts(today)

    def regenerate(self, today: date) -> None:
        """Rebuild unlocked future planned dinners (menus are kept)."""
        rebuilt = self._rebuild_future(today)
        self._commit([(EVENT_CHANGED, {"reason": "regenerate", "rebuilt": rebuilt})])
        self._check_conflicts(today)

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
                debt_after=result.debt_trace.get(a.date),
                history=[ChangeRecord(self._stamp(), "generated", {"cook": a.cook_id})],
            )
        return len(result.added)

    def set_cooks(self, cooks: list[Cook], today: date) -> None:
        """Replace the cook list; unlocked future dinners are rebuilt."""
        self.cooks = sorted(cooks, key=lambda c: c.order)
        self._rebuild_future(today)
        known = {c.id for c in self.cooks}
        self.debt = {k: v for k, v in self.debt.items() if k in known}
        self._commit([])
        self._check_conflicts(today)

    def _rebuild_future(self, today: date) -> int:
        """Reassign cooks on unlocked future dinners, keeping ids, menus, history.

        Locked dinners whose cook no longer exists are rebuilt too. Debt is
        reset to what it was before today so it isn't counted twice.
        """
        known = {c.id for c in self.cooks}
        old = {
            d: v
            for d, v in self.dinners.items()
            if d >= today
            and v.status == STATUS_PLANNED
            and (not v.locked or v.cook not in known)
        }
        for day in old:
            del self.dinners[day]
        self.debt = {k: v for k, v in self._debt_base(today).items() if k in known}
        self._fill(today)
        for day, previous in old.items():
            new = self.dinners.get(day)
            if new is None:
                continue  # no longer a dinner day
            if new.cook != previous.cook:
                previous.history.append(
                    ChangeRecord(
                        self._stamp(),
                        "regenerated",
                        {"from": previous.cook, "to": new.cook},
                    )
                )
            previous.cook, previous.repay = new.cook, new.repay
            previous.debt_after, previous.locked = new.debt_after, False
            self.dinners[day] = previous
        return len(old)

    def _debt_base(self, today: date) -> dict[str, int]:
        past = [
            d for d, v in self.dinners.items() if d < today and v.debt_after is not None
        ]
        return dict(self.dinners[max(past)].debt_after or {}) if past else {}

    # -- availability --------------------------------------------------------------

    def add_unavailable(
        self, cook_id: str, start: date, end: date, today: date
    ) -> None:
        if self.cook(cook_id) is None:
            raise PlannerError(f"Unknown cook: {cook_id}")
        if end < start:
            raise PlannerError("end must not be before start")
        self.manual_windows.append(AvailabilityWindow(cook_id, start, end))
        self._availability_changed(today)

    def clear_unavailable(
        self,
        cook_id: str,
        start: date | None,
        end: date | None,
        today: date,
    ) -> None:
        """Remove the cook's manual windows that overlap start..end (all if open)."""
        if self.cook(cook_id) is None:
            raise PlannerError(f"Unknown cook: {cook_id}")
        self.manual_windows = [
            w
            for w in self.manual_windows
            if w.cook_id != cook_id
            or (start is not None and w.end < start)
            or (end is not None and w.start > end)
        ]
        self._availability_changed(today)

    def set_external_windows(
        self, windows: list[AvailabilityWindow], today: date
    ) -> None:
        """Windows derived from calendars/entities. Rebuilds only on change."""

        def key(ws: list[AvailabilityWindow]) -> list[tuple[str, date, date]]:
            return sorted((w.cook_id, w.start, w.end) for w in ws)

        if key(windows) == key(self.external_windows):
            return
        self.external_windows = list(windows)
        self._availability_changed(today)

    def _availability_changed(self, today: date) -> None:
        rebuilt = self._rebuild_future(today)
        self._commit([(EVENT_CHANGED, {"reason": "availability", "rebuilt": rebuilt})])
        self._check_conflicts(today)

    def _check_conflicts(self, today: date) -> None:
        """Locked dinners are never changed; tell the household instead."""
        known = {c.id for c in self.cooks}
        engine = self.engine
        found = {
            (d.date, d.cook)
            for d in self.dinners.values()
            if d.date >= today
            and d.status == STATUS_PLANNED
            and d.locked
            and d.cook in known
            and not engine.is_available(d.cook, d.date)
        }
        for day, cook_id in sorted(found - self._reported_conflicts):
            cook = self.cook(cook_id)
            self._fire(
                EVENT_CONFLICT,
                {
                    "date": day.isoformat(),
                    "cook": cook_id,
                    "cook_name": cook.name if cook else cook_id,
                },
            )
        self._reported_conflicts = found

    # -- scores ----------------------------------------------------------------------

    def cook_stats(self, cook_id: str, today: date) -> dict[str, int]:
        def cooked(days: int) -> int:
            first = today - timedelta(days=days - 1)
            return sum(
                1
                for d in self.dinners.values()
                if d.cook == cook_id
                and d.status == STATUS_DONE
                and first <= d.date <= today
            )

        given = taken = 0
        for dinner in self.dinners.values():
            for rec in dinner.history:
                if rec.action != "swap" or not rec.detail.get("requested"):
                    continue
                given += rec.detail.get("from") == cook_id
                taken += rec.detail.get("to") == cook_id
        return {
            "cooked_30": cooked(30),
            "cooked_90": cooked(90),
            "swaps_given": given,
            "swaps_taken": taken,
            "debt": self.debt.get(cook_id, 0),
        }

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
                swappable=lambda a: (
                    self.dinners[a.date].status == STATUS_PLANNED
                    if a.date in self.dinners
                    else True
                ),
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
        dinner.cook, partner.cook = (
            result.plan[day].cook_id,
            result.plan[partner.date].cook_id,
        )
        dinner.locked = partner.locked = True
        dinner.repay = partner.repay = False
        if self.settings.menu_follows_cook:
            dinner.menu, partner.menu = partner.menu, dinner.menu
        after = [_snapshot(dinner), _snapshot(partner)]
        for d, b in zip((dinner, partner), before, strict=True):
            detail = {"from": b["cook"], "to": d.cook, "requested": d is dinner}
            d.history.append(ChangeRecord(self._stamp(), "swap", detail))
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

    def set_menu(
        self,
        day: date,
        today: date,
        *,
        title: str | None = None,
        dish_id: str | None = None,
        kind: str | None = None,
        note: str | None = None,
        rating: int | None = None,
        going_out: list[str] | None = None,
    ) -> None:
        """Set, update or clear the menu of any day: past, today or future.

        Giving neither ``title`` nor ``dish_id`` (and no other field) clears the
        menu. A title that matches a dish name or alias links that dish.
        """
        if kind is not None and kind not in MENU_KINDS:
            raise PlannerError(f"Unknown kind: {kind}")
        if rating is not None and not 1 <= rating <= 5:
            raise PlannerError("rating must be 1-5")
        if going_out is not None:
            unknown = [c for c in going_out if self.cook(c) is None]
            if unknown:
                raise PlannerError(f"Unknown cook: {unknown[0]}")
            if going_out and kind is None:
                kind = "eating_out"  # naming who goes out implies eating out
            if going_out and kind != "eating_out":
                raise PlannerError("going_out only applies to kind eating_out")
        dinner = self._dinner_for_menu(day, today)
        before = _snapshot(dinner)
        title = (title or "").strip() or None

        if (
            title is None
            and dish_id is None
            and kind is None
            and note is None
            and rating is None
            and going_out is None
        ):
            dinner.menu = None
        else:
            menu = dinner.menu or MenuEntry()
            if dish_id is not None:
                dish = self.dishes.get(dish_id)
                if dish is None:
                    raise PlannerError(f"Unknown dish: {dish_id}")
                menu.dish_id, menu.title = dish.id, dish.name
            elif title is not None:
                self._link_title(menu, title)
            if kind is not None:
                menu.kind = kind
                if kind != "eating_out":
                    menu.going_out = []
            if going_out is not None:
                menu.going_out = list(going_out)
            if note is not None:
                menu.note = note or None
            if rating is not None:
                menu.rating = rating
            dinner.menu = menu
        dinner.history.append(
            ChangeRecord(self._stamp(), "menu", {"title": self.menu_title(dinner)})
        )
        self._commit(
            [(EVENT_CHANGED, {"before": [before], "after": [_snapshot(dinner)]})]
        )

    def attendance(self, dinner: Dinner) -> dict[str, Any]:
        """Who is out, who stays home, and whether anyone has to cook.

        Eating out is a free night: it uses the planned cook's turn either way.
        A planned cook who stays home still has a normal turn, so cooking is
        needed unless the planned cook is among those going out.
        """
        ids = [c.id for c in self.cooks]
        kind = dinner.menu.kind if dinner.menu else "home"
        out: list[str] = []
        home: list[str] = []
        if kind == "eating_out":
            chosen = dinner.menu.going_out if dinner.menu else []
            out = [c for c in ids if not chosen or c in chosen]
            home = [c for c in ids if c not in out]
            needed = dinner.cook in ids and dinner.cook not in out
        else:
            needed = kind == "home" and dinner.cook in ids
        return {
            "going_out": out,
            "at_home": home,
            "cooking_needed": needed and dinner.status == STATUS_PLANNED,
        }

    def _link_title(self, menu: MenuEntry, title: str) -> None:
        """Link a known dish by name/alias, else keep the title as free text."""
        dish = self.find_dish(title)
        if dish is None and self.settings.auto_create_dishes:
            dish = self._create_dish(title)
        menu.dish_id = dish.id if dish else None
        menu.title = dish.name if dish else title

    def import_menus(
        self, titles: dict[date, str], today: date, overwrite: bool = False
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Set menu titles for many past days as one change.

        Days that already have a menu are skipped unless ``overwrite``. Days
        before the integration ran get log-only records. Returns
        (imported, skipped), each a list of {date, title[, reason]}.
        """
        imported: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        before: list[dict[str, Any]] = []
        touched: list[Dinner] = []
        for day, title in sorted(titles.items()):
            item = {"date": day.isoformat(), "title": title}
            if day > today:
                skipped.append({**item, "reason": "future"})
                continue
            existing = self.dinners.get(day)
            if existing and self.menu_title(existing) and not overwrite:
                skipped.append({**item, "reason": "has_menu"})
                continue
            dinner = self._dinner_for_menu(day, today)
            before.append(_snapshot(dinner))
            menu = dinner.menu or MenuEntry()
            self._link_title(menu, title)
            dinner.menu = menu
            dinner.history.append(
                ChangeRecord(
                    self._stamp(), "menu", {"title": menu.title, "source": "import"}
                )
            )
            touched.append(dinner)
            imported.append({**item, "title": menu.title or title})
        if touched:
            self._commit(
                [
                    (
                        EVENT_CHANGED,
                        {
                            "reason": "import",
                            "before": before,
                            "after": [_snapshot(d) for d in touched],
                        },
                    )
                ]
            )
        return imported, skipped

    def _dinner_for_menu(self, day: date, today: date) -> Dinner:
        if day in self.dinners:
            return self.dinners[day]
        if day < today:
            # A day from before the integration ran: keep a log-only record.
            dinner = Dinner(id=uuid4().hex, date=day, cook="", status=STATUS_DONE)
            self.dinners[day] = dinner
            return dinner
        if (day - today).days >= MAX_FUTURE_DAYS:
            raise PlannerError(f"Too far ahead (limit {MAX_FUTURE_DAYS} days)")
        if day.weekday() not in self.settings.dinner_days:
            raise PlannerError(f"{day} is not a dinner day")
        result = self.engine.generate(
            _to_plan(self.dinners), today, (day - today).days + 1, self.debt
        )
        self.debt = result.debt
        for a in result.added:
            self.dinners[a.date] = Dinner(
                id=uuid4().hex, date=a.date, cook=a.cook_id, repay=a.repay
            )
        return self.dinners[day]

    # -- dishes ------------------------------------------------------------------

    def _create_dish(self, name: str, **fields: Any) -> Dish:
        base, dish_id, i = slugify(name, "dish"), "", 2
        dish_id = base
        while dish_id in self.dishes:
            dish_id, i = f"{base}_{i}", i + 1
        stamp = self._stamp()
        dish = Dish(dish_id, name.strip(), created=stamp, updated=stamp, **fields)
        self.dishes[dish_id] = dish
        return dish

    def create_dish(
        self,
        name: str,
        aliases: list[str] | None = None,
        tags: list[str] | None = None,
        recipe: dict[str, Any] | None = None,
    ) -> Dish:
        if not name.strip():
            raise PlannerError("A dish needs a name")
        if self.find_dish(name):
            raise PlannerError(f"A dish called '{name}' already exists")
        dish = self._create_dish(
            name, aliases=aliases or [], tags=tags or [], recipe=recipe
        )
        self._commit([])
        return dish

    def update_dish(self, dish_id: str, **changes: Any) -> Dish:
        dish = self.dishes.get(dish_id)
        if dish is None:
            raise PlannerError(f"Unknown dish: {dish_id}")
        for key in ("name", "aliases", "tags", "recipe"):
            if changes.get(key) is not None:
                setattr(dish, key, changes[key])
        dish.updated = self._stamp()
        for dinner in self.dinners.values():
            if dinner.menu and dinner.menu.dish_id == dish_id:
                dinner.menu.title = dish.name
        self._commit([])
        return dish

    def delete_dish(self, dish_id: str) -> None:
        if self.dishes.pop(dish_id, None) is None:
            raise PlannerError(f"Unknown dish: {dish_id}")
        # Menus keep their title so history stays readable.
        for dinner in self.dinners.values():
            if dinner.menu and dinner.menu.dish_id == dish_id:
                dinner.menu.dish_id = None
        self._commit([])

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
            ChangeRecord(self._stamp(), action, {"from": dinner.status, "to": status})
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
        "menu": dinner.menu.to_dict() if dinner.menu else None,
    }
