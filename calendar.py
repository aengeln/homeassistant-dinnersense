"""Read-only calendar of past and planned dinners."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .entity import DinnerSenseEntity, local_today
from .models import STATUS_SKIPPED, Dinner
from .runtime import DinnerSenseConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DinnerSenseConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([DinnerCalendar(entry)])


class DinnerCalendar(DinnerSenseEntity, CalendarEntity):
    _attr_translation_key = "dinner_calendar"

    def __init__(self, entry: DinnerSenseConfigEntry) -> None:
        super().__init__(entry, "calendar")

    def _event(self, dinner: Dinner) -> CalendarEvent:
        cook = self.planner.cook(dinner.cook)
        parts = [cook.name] if cook else []
        if title := self.planner.menu_title(dinner):
            parts.append(title)
        if dinner.status == STATUS_SKIPPED:
            parts.append("(skipped)")
        return CalendarEvent(
            start=dinner.date,
            end=dinner.date + timedelta(days=1),
            summary=" — ".join(parts) or "Dinner",
            description=dinner.menu.note if dinner.menu and dinner.menu.note else None,
            uid=dinner.id,
        )

    @property
    def event(self) -> CalendarEvent | None:
        today = local_today()
        upcoming = [d for d in self.planner.dinners.values() if d.date >= today]
        return self._event(min(upcoming, key=lambda d: d.date)) if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        start: date = dt_util.as_local(start_date).date()
        end: date = dt_util.as_local(end_date).date()
        return [
            self._event(d)
            for d in self.planner.get_plan(start, end - timedelta(days=1))
        ]
