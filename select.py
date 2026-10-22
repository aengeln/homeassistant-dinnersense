"""Day-slot selects. Choosing a cook performs a swap."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DinnerSenseEntity, local_today
from .planner import PlannerError
from .runtime import DinnerSenseConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DinnerSenseConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    horizon = entry.runtime_data.planner.settings.horizon_days
    async_add_entities(DinnerDaySelect(entry, n) for n in range(horizon))


class DinnerDaySelect(DinnerSenseEntity, SelectEntity):
    _attr_translation_key = "dinner_day"

    def __init__(self, entry: DinnerSenseConfigEntry, offset: int) -> None:
        super().__init__(entry, f"day_{offset}")
        self._offset = offset
        self._attr_translation_placeholders = {"n": str(offset)}

    @property
    def available(self) -> bool:
        return self.dinner_for_offset(self._offset) is not None

    @property
    def options(self) -> list[str]:
        return [c.name for c in self.planner.cooks]

    @property
    def current_option(self) -> str | None:
        dinner = self.dinner_for_offset(self._offset)
        if dinner is None:
            return None
        cook = self.planner.cook(dinner.cook)
        return cook.name if cook else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        dinner = self.dinner_for_offset(self._offset)
        return self.dinner_attributes(dinner) if dinner else {}

    async def async_select_option(self, option: str) -> None:
        dinner = self.dinner_for_offset(self._offset)
        cook = self.planner.cook_by_name(option)
        if dinner is None or cook is None:
            raise HomeAssistantError("Unknown day or cook")
        try:
            self.planner.swap(dinner.date, cook.id, local_today())
        except PlannerError as err:
            raise HomeAssistantError(str(err)) from err
