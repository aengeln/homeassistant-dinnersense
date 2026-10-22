"""Recipe schema, including timers."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.dinnersense.services import RECIPE_SCHEMA

STEPS = ["Boil water", "Cook the noodles", "Fry the chicken", "Mix"]


def recipe(**extra):
    return {"steps": STEPS, **extra}


def test_timers_are_accepted_and_returned_unchanged():
    timers = [
        {"name": "Noodles", "minutes": 4, "step": 3},
        {"name": "Chicken", "minutes": 8, "step": 1},
    ]
    got = RECIPE_SCHEMA(recipe(timers=timers))
    assert got["timers"] == timers
    assert [type(t["minutes"]) for t in got["timers"]] == [int, int]


def test_step_is_optional_and_minutes_may_be_fractional():
    got = RECIPE_SCHEMA(recipe(timers=[{"name": "Rest", "minutes": 2.5}]))
    assert got["timers"] == [{"name": "Rest", "minutes": 2.5}]
    assert (
        RECIPE_SCHEMA({"timers": [{"name": "Rest", "minutes": "10"}]})["timers"][0][
            "minutes"
        ]
        == 10
    )


@pytest.mark.parametrize(
    "timer",
    [
        {"minutes": 4},  # no name
        {"name": "x"},  # no minutes
        {"name": "x", "minutes": 0},
        {"name": "x", "minutes": -3},
        {"name": "x", "minutes": "soon"},
        {"name": "x", "minutes": 4, "step": 0},
        {"name": "x", "minutes": 4, "step": 5},  # only 4 steps
        {"name": "x", "minutes": 4, "colour": "red"},  # unknown key
    ],
)
def test_bad_timers_are_rejected(timer):
    with pytest.raises(vol.Invalid):
        RECIPE_SCHEMA(recipe(timers=[timer]))


def test_timer_for_a_step_needs_steps():
    with pytest.raises(vol.Invalid, match="0 step"):
        RECIPE_SCHEMA({"timers": [{"name": "x", "minutes": 4, "step": 1}]})


def test_recipe_without_timers_still_valid():
    assert RECIPE_SCHEMA({"servings": 4}) == {"servings": 4}
