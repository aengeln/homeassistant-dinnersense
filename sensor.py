"""Tonight / tomorrow sensors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DinnerSenseEntity
from .runtime import DinnerSenseConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DinnerSenseConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [
            DinnerDaySensor(entry, "today", 0),
            DinnerDaySensor(entry, "tomorrow", 1),
        ]
    )


class DinnerDaySensor(DinnerSenseEntity, SensorEntity):
    def __init__(
        self, entry: DinnerSenseConfigEntry, key: str, offset: int
    ) -> None:
        super().__init__(entry, key)
        self._offset = offset
        self._attr_translation_key = f"dinner_{key}"

    @property
    def native_value(self) -> str | None:
        dinner = self.dinner_for_offset(self._offset)
        if dinner is None:
            return None
        cook = self.planner.cook(dinner.cook)
        return cook.name if cook else dinner.cook

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        dinner = self.dinner_for_offset(self._offset)
        return self.dinner_attributes(dinner) if dinner else {}
