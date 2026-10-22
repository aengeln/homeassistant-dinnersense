"""Menu text entities: type a dish name or any free text for a day."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
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
    async_add_entities(DinnerMenuText(entry, n) for n in range(horizon))


class DinnerMenuText(DinnerSenseEntity, TextEntity):
    _attr_translation_key = "dinner_menu_day"
    _attr_native_max = 255

    def __init__(self, entry: DinnerSenseConfigEntry, offset: int) -> None:
        super().__init__(entry, f"menu_day_{offset}")
        self._offset = offset
        self._attr_translation_placeholders = {"n": str(offset)}

    @property
    def available(self) -> bool:
        return self.dinner_for_offset(self._offset) is not None

    @property
    def native_value(self) -> str:
        dinner = self.dinner_for_offset(self._offset)
        return (self.planner.menu_title(dinner) or "") if dinner else ""

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        dinner = self.dinner_for_offset(self._offset)
        return self.dinner_attributes(dinner) if dinner else {}

    async def async_set_value(self, value: str) -> None:
        dinner = self.dinner_for_offset(self._offset)
        if dinner is None:
            raise HomeAssistantError("No dinner planned for this day")
        try:
            self.planner.set_menu(dinner.date, local_today(), title=value)
        except PlannerError as err:
            raise HomeAssistantError(str(err)) from err
