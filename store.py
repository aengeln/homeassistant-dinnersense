"""Persistence for one household via ``homeassistant.helpers.storage.Store``."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .models import Dinner, Dish
from .planner import Planner

STORAGE_VERSION = 1
SAVE_DELAY = 1


def migrate(version: int, data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade stored data from ``version`` to STORAGE_VERSION, one step at a time."""
    # No migrations yet. Add `if version < 2: ...` blocks here as the format grows.
    return data


class _MigratingStore(Store[dict[str, Any]]):
    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        return migrate(old_major_version, old_data)


class HouseholdStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = _MigratingStore(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")

    async def async_load(
        self,
    ) -> tuple[dict[date, Dinner], dict[str, int], dict[str, Dish]]:
        data = await self._store.async_load()
        if not data:
            return {}, {}, {}
        dinners = {
            d.date: d for d in (Dinner.from_dict(x) for x in data.get("dinners", []))
        }
        dishes = {d.id: d for d in (Dish.from_dict(x) for x in data.get("dishes", []))}
        return dinners, dict(data.get("debt", {})), dishes

    @staticmethod
    def _payload(planner: Planner) -> dict[str, Any]:
        return {
            "dinners": [d.to_dict() for _, d in sorted(planner.dinners.items())],
            "debt": dict(planner.debt),
            "cooks": [c.to_dict() for c in planner.cooks],
            "dishes": [d.to_dict() for d in planner.dishes.values()],
        }

    def schedule_save(self, planner: Planner) -> None:
        self._store.async_delay_save(lambda: self._payload(planner), SAVE_DELAY)

    async def async_save_now(self, planner: Planner) -> None:
        await self._store.async_save(self._payload(planner))
