"""Push a day's ingredients to a todo list (e.g. the shopping list).

Items are written as "Tortillas x 8" because Home Assistant's todo lists have no
quantity field. Ingredients already open on the list are skipped; completed
items don't count, since those have been bought.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .models import Dinner, Dish

_QUANTITY_SUFFIX = re.compile(r"\s+x\s+\S.*$", re.IGNORECASE)


def ingredient_name_key(text: str) -> str:
    """Compare list items and ingredients by name, ignoring case and 'x 8'."""
    return _QUANTITY_SUFFIX.sub("", text.strip()).casefold()


def format_item(name: str, quantity: str | None) -> str:
    name, quantity = name.strip(), (quantity or "").strip()
    return f"{name} x {quantity}" if quantity else name


def ingredients_for(
    dinner: Dinner | None, dishes: dict[str, Dish]
) -> list[dict[str, Any]]:
    """The linked dish's ingredients, in recipe order, without repeated names."""
    menu = dinner.menu if dinner else None
    dish = dishes.get(menu.dish_id) if menu and menu.dish_id else None
    if dish is None or not dish.recipe:
        return []
    seen: set[str] = set()
    out = []
    for ing in dish.recipe.get("ingredients") or []:
        name = str(ing.get("name", "")).strip()
        key = ingredient_name_key(name)
        if not name or key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "quantity": ing.get("quantity")})
    return out


def split_new(
    ingredients: list[dict[str, Any]], open_items: list[str]
) -> tuple[list[str], list[str]]:
    """Return (items to add, names skipped because already on the list)."""
    have = {ingredient_name_key(s) for s in open_items}
    add, skipped = [], []
    for ing in ingredients:
        if ingredient_name_key(ing["name"]) in have:
            skipped.append(ing["name"])
        else:
            add.append(format_item(ing["name"], ing["quantity"]))
    return add, skipped


async def async_add_ingredients(
    hass: HomeAssistant,
    dinner: Dinner | None,
    dishes: dict[str, Dish],
    todo_entity: str | None,
    day: date,
) -> dict[str, list[str]]:
    """Add the day's missing ingredients to ``todo_entity``."""
    if not todo_entity:
        raise ServiceValidationError(
            "No todo list set. Choose one in the Dinner Sense settings "
            "or pass todo_entity."
        )
    if hass.states.get(todo_entity) is None:
        raise ServiceValidationError(f"Todo list {todo_entity} was not found")
    ingredients = ingredients_for(dinner, dishes)
    if not ingredients:
        raise ServiceValidationError(
            f"No dish with ingredients is planned for {day.isoformat()}"
        )

    try:
        result = await hass.services.async_call(
            "todo",
            "get_items",
            {"entity_id": todo_entity, "status": ["needs_action"]},
            blocking=True,
            return_response=True,
        )
    except HomeAssistantError as err:
        raise HomeAssistantError(f"Could not read {todo_entity}: {err}") from err
    open_items = [
        i.get("summary", "")
        for i in (result or {}).get(todo_entity, {}).get("items", [])
    ]

    add, skipped = split_new(ingredients, open_items)
    for item in add:
        await hass.services.async_call(
            "todo",
            "add_item",
            {"entity_id": todo_entity, "item": item},
            blocking=True,
        )
    return {"added": add, "skipped": skipped}
