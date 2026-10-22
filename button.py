"""Button: put today's ingredients on the shopping list."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import DinnerSenseEntity, local_today
from .runtime import DinnerSenseConfigEntry
from .shopping import async_add_ingredients


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DinnerSenseConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([AddIngredientsButton(entry)])


class AddIngredientsButton(DinnerSenseEntity, ButtonEntity):
    _attr_translation_key = "add_ingredients_today"

    def __init__(self, entry: DinnerSenseConfigEntry) -> None:
        super().__init__(entry, "add_ingredients_today")

    @property
    def available(self) -> bool:
        return bool(self.planner.settings.todo_entity)

    async def async_press(self) -> None:
        today = local_today()
        await async_add_ingredients(
            self.hass,
            self.planner.dinner_on(today),
            self.planner.dishes,
            self.planner.settings.todo_entity,
            today,
        )
