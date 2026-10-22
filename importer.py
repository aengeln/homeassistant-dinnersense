"""Seed past menus from the recorder history of an existing text entity.

The recorder usually keeps only about ten days, so this is a one-off way to
rescue what an old ``input_text`` (e.g. "today's dinner") remembers.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

EMPTY_STATES = {"", "unknown", "unavailable", "none"}


def titles_by_day(
    states: Iterable[tuple[datetime, str]], today: date, days: int
) -> dict[date, str]:
    """One title per local day: the last real value set on that day.

    Empty and unknown states (the nightly clearing, restarts) are ignored.
    """
    first = today - timedelta(days=days - 1)
    found: dict[date, str] = {}
    for at, value in sorted(states, key=lambda s: s[0]):
        text = value.strip()
        day = dt_util.as_local(at).date()
        if text.casefold() in EMPTY_STATES or not first <= day <= today:
            continue
        found[day] = text  # later entries on the same day win
    return found


async def async_read_states(
    hass: HomeAssistant, entity_id: str, days: int
) -> list[tuple[datetime, str]]:
    """State changes of ``entity_id`` over the last ``days`` days."""
    if "recorder" not in hass.config.components:
        raise ServiceValidationError(
            "The recorder is not running, so there is no history"
        )
    from homeassistant.components.recorder import get_instance, history

    now = dt_util.now()
    start = dt_util.start_of_local_day(now.date() - timedelta(days=days - 1))

    def _read() -> list[tuple[datetime, str]]:
        result = history.get_significant_states(
            hass,
            start,
            now,
            [entity_id],
            include_start_time_state=False,
            significant_changes_only=False,
            no_attributes=True,
        )
        return [(s.last_changed, s.state) for s in result.get(entity_id, [])]  # type: ignore[union-attr]

    return await get_instance(hass).async_add_executor_job(_read)
