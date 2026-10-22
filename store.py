"""Persistence for one household via ``homeassistant.helpers.storage.Store``."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .models import Cook, Dinner

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
    ) -> tuple[dict[date, Dinner], dict[str, int]]:
        data = await self._store.async_load()
        if not data:
            return {}, {}
        dinners = {
            d.date: d for d in (Dinner.from_dict(x) for x in data.get("dinners", []))
        }
        return dinners, dict(data.get("debt", {}))

    def schedule_save(
        self, dinners: dict[date, Dinner], debt: dict[str, int], cooks: list[Cook]
    ) -> None:
        self._store.async_delay_save(
            lambda: {
                "dinners": [d.to_dict() for _, d in sorted(dinners.items())],
                "debt": dict(debt),
                "cooks": [c.to_dict() for c in cooks],
            },
            SAVE_DELAY,
        )

    async def async_save_now(
        self, dinners: dict[date, Dinner], debt: dict[str, int], cooks: list[Cook]
    ) -> None:
        await self._store.async_save(
            {
                "dinners": [d.to_dict() for _, d in sorted(dinners.items())],
                "debt": dict(debt),
                "cooks": [c.to_dict() for c in cooks],
            }
        )
