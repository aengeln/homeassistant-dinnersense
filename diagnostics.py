"""Diagnostics."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .runtime import DinnerSenseConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DinnerSenseConfigEntry
) -> dict[str, Any]:
    planner = entry.runtime_data.planner
    return {
        "cooks": [c.to_dict() for c in planner.cooks],
        "settings": planner.settings.to_dict(),
        "debt": planner.debt,
        "dinners": [d.to_dict() for _, d in sorted(planner.dinners.items())],
    }
