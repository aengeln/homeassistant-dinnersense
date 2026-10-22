"""Recipe text parsing for the dish editor (pure)."""

from __future__ import annotations

import pytest

from custom_components.dinnersense.recipes import (
    RecipeTextError,
    build_recipe,
    format_ingredients,
    format_steps,
    format_timers,
    parse_ingredients,
    parse_timers,
    split_words,
)


def test_ingredients_with_and_without_quantity():
    got = parse_ingredients(
        "Tortillas x 8\n  Minced beef x 500 g \n\nLime\nOlive oil X 2 tbsp"
    )
    assert got == [
        {"name": "Tortillas", "quantity": "8"},
        {"name": "Minced beef", "quantity": "500 g"},
        {"name": "Lime"},
        {"name": "Olive oil", "quantity": "2 tbsp"},
    ]


def test_an_x_inside_a_name_is_not_a_quantity():
    assert parse_ingredients("Maxi x-ray film") == [{"name": "Maxi x-ray film"}]
    assert parse_ingredients("Box")[0] == {"name": "Box"}


def test_duplicate_ingredients_are_rejected_case_insensitively():
    with pytest.raises(RecipeTextError, match="twice"):
        parse_ingredients("Lime\nlime x 2")


def test_ingredients_roundtrip():
    text = "Tortillas x 8\nLime"
    assert format_ingredients(parse_ingredients(text)) == text


def test_timers_parse_and_roundtrip():
    got = parse_timers("Noodles, 4, 3\nChicken, 8, 1\nRest, 2.5")
    assert got == [
        {"name": "Noodles", "minutes": 4, "step": 3},
        {"name": "Chicken", "minutes": 8, "step": 1},
        {"name": "Rest", "minutes": 2.5},
    ]
    assert format_timers(got) == "Noodles, 4, 3\nChicken, 8, 1\nRest, 2.5"


@pytest.mark.parametrize(
    "line",
    [
        "Noodles",
        "Noodles, soon",
        "Noodles, 0",
        "Noodles, -2",
        "Noodles, 4, x",
        "Noodles, 4, 0",
        ", 4",
        "a, 1, 2, 3",
    ],
)
def test_bad_timer_lines_name_the_line(line):
    with pytest.raises(RecipeTextError, match="Noodles|a, 1|, 4"):
        parse_timers(line)


def test_build_recipe_from_all_fields():
    recipe = build_recipe(
        servings=4,
        ingredients="Tortillas x 8",
        steps="Brown the beef\nWarm the tortillas",
        timers="Beef, 8, 1",
        url=" https://example.com/t ",
        notes="Use lean beef",
    )
    assert recipe == {
        "servings": 4,
        "ingredients": [{"name": "Tortillas", "quantity": "8"}],
        "steps": ["Brown the beef", "Warm the tortillas"],
        "timers": [{"name": "Beef", "minutes": 8, "step": 1}],
        "url": "https://example.com/t",
        "notes": "Use lean beef",
    }
    assert format_steps(recipe["steps"]) == "Brown the beef\nWarm the tortillas"


def test_empty_fields_give_an_empty_recipe():
    assert (
        build_recipe(
            servings=None, ingredients="", steps=None, timers=" ", url="", notes=None
        )
        == {}
    )


def test_timer_for_a_missing_step_is_rejected():
    with pytest.raises(RecipeTextError, match="step 3"):
        build_recipe(
            servings=None,
            ingredients=None,
            steps="One",
            timers="X, 1, 3",
            url=None,
            notes=None,
        )


def test_split_words():
    assert split_words("pasta, Fish ,  , quick") == ["pasta", "Fish", "quick"]
    assert split_words(None) == []
