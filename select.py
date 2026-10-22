"""Day-slot selects. Choosing a cook performs a swap."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DinnerSenseEntity, local_today
from .kinds import kind_options, option_for_menu
from .planner import PlannerError
from .runtime import DinnerSenseConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DinnerSenseConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    horizon = entry.runtime_data.planner.settings.horizon_days
    async_add_entities(DinnerDaySelect(entry, n) for n in range(horizon))
    async_add_entities(DinnerKindSelect(entry, n) for n in range(horizon))


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


class DinnerKindSelect(DinnerSenseEntity, SelectEntity):
    """Home / eating out (everyone or some cooks) / leftovers / no dinner."""

    _attr_translation_key = "dinner_kind_day"

    def __init__(self, entry: DinnerSenseConfigEntry, offset: int) -> None:
        super().__init__(entry, f"kind_day_{offset}")
        self._offset = offset
        self._attr_translation_placeholders = {"n": str(offset)}

    def _kind_options(self):  # noqa: ANN202
        return kind_options(self.planner.cooks, self.hass.config.language)

    @property
    def available(self) -> bool:
        return self.dinner_for_offset(self._offset) is not None

    @property
    def options(self) -> list[str]:
        return [o.label for o in self._kind_options()]

    @property
    def current_option(self) -> str | None:
        dinner = self.dinner_for_offset(self._offset)
        if dinner is None:
            return None
        options = self._kind_options()
        return option_for_menu(dinner.menu, self.planner.cooks, options).label

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        dinner = self.dinner_for_offset(self._offset)
        return self.dinner_attributes(dinner) if dinner else {}

    async def async_select_option(self, option: str) -> None:
        dinner = self.dinner_for_offset(self._offset)
        chosen = next((o for o in self._kind_options() if o.label == option), None)
        if dinner is None or chosen is None:
            raise HomeAssistantError("Unknown day or option")
        current = option_for_menu(dinner.menu, self.planner.cooks, self._kind_options())
        if current == chosen:
            return
        try:
            self.planner.set_menu(
                dinner.date,
                local_today(),
                kind=chosen.kind,
                going_out=list(chosen.going_out)
                if chosen.kind == "eating_out"
                else None,
            )
        except PlannerError as err:
            raise HomeAssistantError(str(err)) from err
