"""Resolve per-cook availability sources into unavailable windows.

Source types (stored on ``Cook.availability``):
  calendar      entity_id, keyword (default "away"): events containing the keyword
  until         entity_id: input_datetime "away until"; away from today to that date
  range         from_entity, to_entity: input_datetimes giving a from/to pair
  binary_sensor entity_id: unavailable today while ``on`` (live)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .models import Cook
from .rotation import AvailabilityWindow

_LOGGER = logging.getLogger(__name__)

SOURCE_CALENDAR = "calendar"
SOURCE_UNTIL = "until"
SOURCE_RANGE = "range"
SOURCE_BINARY = "binary_sensor"
DEFAULT_KEYWORD = "away"
CALENDAR_LOOKAHEAD_DAYS = 90
REFRESH_INTERVAL = timedelta(minutes=30)


def watched_entities(cooks: list[Cook]) -> list[str]:
    """Entities whose state changes should trigger a refresh.

    Malformed sources (missing or non-string entity ids) are skipped.
    """
    found: set[str] = set()
    for cook in cooks:
        for src in cook.availability:
            kind = src.get("type")
            if kind in (SOURCE_UNTIL, SOURCE_BINARY):
                keys = ("entity_id",)
            elif kind == SOURCE_RANGE:
                keys = ("from_entity", "to_entity")
            else:
                continue
            found.update(
                value
                for key in keys
                if isinstance(value := src.get(key), str) and value
            )
    return sorted(found)


def parse_date(value: str | None) -> date | None:
    """Accept '2026-03-06' or '2026-03-06 10:00:00'; anything else is None."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def event_days(start: str, end: str) -> tuple[date, date] | None:
    """First and last local day an event covers (all-day ends are exclusive)."""
    if len(start) == 10:
        first, last_excl = parse_date(start), parse_date(end)
        if first is None or last_excl is None:
            return None
        return first, max(first, last_excl - timedelta(days=1))
    try:
        s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError:
        return None
    s, e = dt_util.as_local(s), dt_util.as_local(e)
    last = e.date() - timedelta(days=1) if e.time() == datetime.min.time() else e.date()
    return s.date(), max(s.date(), last)


def source_windows(
    cook_id: str,
    source: dict[str, Any],
    today: date,
    states: Callable[[str], str | None],
    events: list[dict[str, Any]],
) -> list[AvailabilityWindow]:
    kind = source.get("type")
    if kind == SOURCE_CALENDAR:
        keyword = (source.get("keyword") or DEFAULT_KEYWORD).casefold()
        out = []
        for ev in events:
            text = " ".join(
                str(ev.get(k) or "") for k in ("summary", "description", "location")
            ).casefold()
            if keyword not in text:
                continue
            if span := event_days(str(ev.get("start", "")), str(ev.get("end", ""))):
                out.append(AvailabilityWindow(cook_id, *span))
        return out
    if kind == SOURCE_UNTIL:
        until = parse_date(states(source["entity_id"]))
        return (
            [AvailabilityWindow(cook_id, today, until)]
            if until and until >= today
            else []
        )
    if kind == SOURCE_RANGE:
        start = parse_date(states(source["from_entity"]))
        end = parse_date(states(source["to_entity"]))
        return (
            [AvailabilityWindow(cook_id, start, end)]
            if start and end and end >= start
            else []
        )
    if kind == SOURCE_BINARY:
        return (
            [AvailabilityWindow(cook_id, today, today)]
            if states(source["entity_id"]) == "on"
            else []
        )
    return []


class AvailabilityTracker:
    """Keeps the planner's external availability windows up to date."""

    def __init__(self, hass: HomeAssistant, planner: Any) -> None:
        self.hass = hass
        self.planner = planner

    async def async_refresh(self, _: Any = None) -> None:
        """Re-read all sources. Never raises: a broken source must not stop setup.

        At boot a calendar or helper may not be ready yet; then that source just
        counts as "no windows" until the next refresh.
        """
        try:
            await self._async_refresh()
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Could not refresh cook availability; keeping the last known"
            )

    async def _async_refresh(self) -> None:
        today = dt_util.now().date()
        windows: list[AvailabilityWindow] = []
        calendar_cache: dict[str, list[dict[str, Any]]] = {}
        for cook in self.planner.cooks:
            for source in cook.availability:
                try:
                    events: list[dict[str, Any]] = []
                    if source.get("type") == SOURCE_CALENDAR:
                        entity_id = source["entity_id"]
                        if entity_id not in calendar_cache:
                            calendar_cache[entity_id] = await self._calendar_events(
                                entity_id, today
                            )
                        events = calendar_cache[entity_id]
                    windows += source_windows(
                        cook.id, source, today, self._state, events
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Ignoring availability source %s for %s", source, cook.name
                    )
        self.planner.set_external_windows(windows, today)

    def _state(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        return state.state if state else None

    async def _calendar_events(
        self, entity_id: str, today: date
    ) -> list[dict[str, Any]]:
        start = dt_util.start_of_local_day(today)
        try:
            result = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": entity_id,
                    "start_date_time": start.isoformat(),
                    "end_date_time": (
                        start + timedelta(days=CALENDAR_LOOKAHEAD_DAYS)
                    ).isoformat(),
                },
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("Could not read calendar %s: %s", entity_id, err)
            return []
        return list((result or {}).get(entity_id, {}).get("events", []))

    @callback
    def async_start(self) -> list[CALLBACK_TYPE]:
        @callback
        def _changed(_: Event[EventStateChangedData]) -> None:
            self.hass.async_create_task(self.async_refresh())

        unsubs: list[CALLBACK_TYPE] = [
            async_track_time_interval(self.hass, self.async_refresh, REFRESH_INTERVAL)
        ]
        if watched := watched_entities(self.planner.cooks):
            unsubs.append(async_track_state_change_event(self.hass, watched, _changed))
        return unsubs
