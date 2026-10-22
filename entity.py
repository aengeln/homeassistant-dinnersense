"""Shared entity base."""

from __future__ import annotations

from datetime import date, timedelta

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .models import Dinner
from .planner import Planner
from .runtime import DinnerSenseConfigEntry


def local_today() -> date:
    return dt_util.now().date()


class DinnerSenseEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: DinnerSenseConfigEntry, unique_suffix: str) -> None:
        self._entry = entry
        self.planner: Planner = entry.runtime_data.planner
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title
        )

    async def async_added_to_hass(self) -> None:
        # Entity state is only ever written from planner state, never fed back
        # into the planner, so integration-made updates can't trigger actions.
        self.async_on_remove(self.planner.add_listener(self.async_write_ha_state))

    def dinner_for_offset(self, offset: int) -> Dinner | None:
        return self.planner.dinner_on(local_today() + timedelta(days=offset))

    def dinner_attributes(self, dinner: Dinner) -> dict[str, object]:
        cook = self.planner.cook(dinner.cook)
        menu = dinner.menu
        return {
            "date": dinner.date.isoformat(),
            "weekday": dinner.date.strftime("%A"),
            "status": dinner.status,
            "locked": dinner.locked,
            "cook_id": dinner.cook,
            "color": cook.color if cook else None,
            "menu_title": menu.title if menu else None,
            "dish_id": menu.dish_id if menu else None,
            "kind": menu.kind if menu else None,
            "note": menu.note if menu else None,
        }
