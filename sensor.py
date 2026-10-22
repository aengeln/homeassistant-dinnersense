"""Tonight / tomorrow sensors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DinnerSenseEntity, local_today
from .runtime import DinnerSenseConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DinnerSenseConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    planner = entry.runtime_data.planner
    async_add_entities(
        [
            DinnerDaySensor(entry, "today", 0),
            DinnerDaySensor(entry, "tomorrow", 1),
            *(DinnerScoreSensor(entry, c.id, c.name) for c in planner.cooks),
        ]
    )


class DinnerDaySensor(DinnerSenseEntity, SensorEntity):
    def __init__(self, entry: DinnerSenseConfigEntry, key: str, offset: int) -> None:
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


class DinnerScoreSensor(DinnerSenseEntity, SensorEntity):
    """Dinners cooked in the last 30 days, plus swaps and fairness debt."""

    _attr_translation_key = "dinner_score"
    _attr_native_unit_of_measurement = "dinners"

    def __init__(
        self, entry: DinnerSenseConfigEntry, cook_id: str, cook_name: str
    ) -> None:
        super().__init__(entry, f"score_{cook_id}")
        self._cook_id = cook_id
        self._attr_translation_placeholders = {"cook": cook_name}

    def _stats(self) -> dict[str, int]:
        return self.planner.cook_stats(self._cook_id, local_today())

    @property
    def native_value(self) -> int:
        return self._stats()["cooked_30"]

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        stats = self._stats()
        return {
            "cook_id": self._cook_id,
            "cooked_90": stats["cooked_90"],
            "swaps_given": stats["swaps_given"],
            "swaps_taken": stats["swaps_taken"],
            "debt": stats["debt"],
        }
