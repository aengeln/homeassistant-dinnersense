"""Shopping list helpers (pure)."""

from __future__ import annotations

from datetime import date

from custom_components.dinnersense.models import Dinner, Dish, MenuEntry
from custom_components.dinnersense.shopping import (
    format_item,
    ingredient_name_key,
    ingredients_for,
    split_new,
)

TACOS = Dish(
    "tacos",
    "Tacos",
    recipe={
        "ingredients": [
            {"name": "Tortillas", "quantity": "8"},
            {"name": "Minced beef", "quantity": "500 g"},
            {"name": "Lime"},
            {"name": "tortillas", "quantity": "4"},  # repeated name
            {"name": "  "},
        ]
    },
)


def dinner(menu):
    return Dinner("i", date(2026, 3, 2), "ann", menu=menu)


def test_format_item_uses_x_and_omits_missing_quantity():
    assert format_item("Tortillas", "8") == "Tortillas x 8"
    assert format_item(" Lime ", None) == "Lime"
    assert format_item("Lime", "  ") == "Lime"


def test_name_key_ignores_case_and_quantity_suffix():
    assert ingredient_name_key("Tortillas x 8") == "tortillas"
    assert ingredient_name_key("tortillas X 500 g") == "tortillas"
    assert ingredient_name_key("Maxi x-ray film") == "maxi x-ray film"
    assert ingredient_name_key("Lime") == "lime"


def test_ingredients_for_linked_dish_in_order_without_repeats():
    got = ingredients_for(
        dinner(MenuEntry(dish_id="tacos", title="Tacos")), {"tacos": TACOS}
    )
    assert [i["name"] for i in got] == ["Tortillas", "Minced beef", "Lime"]


def test_no_ingredients_for_free_text_missing_dish_or_recipeless_dish():
    dishes = {"tacos": TACOS, "soup": Dish("soup", "Soup")}
    assert ingredients_for(dinner(MenuEntry(title="Pizza")), dishes) == []
    assert ingredients_for(dinner(None), dishes) == []
    assert ingredients_for(None, dishes) == []
    assert ingredients_for(dinner(MenuEntry(dish_id="soup")), dishes) == []
    assert ingredients_for(dinner(MenuEntry(dish_id="gone")), dishes) == []


def test_split_new_skips_open_items_in_any_form():
    ings = ingredients_for(dinner(MenuEntry(dish_id="tacos")), {"tacos": TACOS})
    add, skipped = split_new(ings, ["tortillas x 12", "Milk", "LIME"])
    assert add == ["Minced beef x 500 g"]
    assert skipped == ["Tortillas", "Lime"]
