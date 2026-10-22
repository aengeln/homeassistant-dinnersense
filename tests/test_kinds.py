"""Options of the per-day kind select (pure)."""

from __future__ import annotations

from custom_components.dinnersense.kinds import kind_options, option_for_menu
from custom_components.dinnersense.models import Cook, MenuEntry

COOKS = [Cook("ann", "Ann", 0), Cook("bob", "Bob", 1), Cook("cy", "Cy", 2)]


def labels(cooks=COOKS, lang="en"):
    return [o.label for o in kind_options(cooks, lang)]


def test_three_cooks_get_every_combination():
    got = labels()
    assert got[:2] == ["Home", "Eating out: everyone"]
    assert got[-2:] == ["Leftovers", "No dinner"]
    assert got[2:-2] == [
        "Eating out: Ann",
        "Eating out: Bob",
        "Eating out: Cy",
        "Eating out: Ann, Bob",
        "Eating out: Ann, Cy",
        "Eating out: Bob, Cy",
    ]
    assert len(set(got)) == len(got)


def test_swedish_labels():
    assert labels(lang="sv-SE")[:2] == ["Hemma", "Äta ute: alla"]
    assert labels(lang="de")[0] == "Home"  # unknown language falls back to English


def test_many_cooks_only_get_everyone():
    cooks = [Cook(f"c{i}", f"C{i}", i) for i in range(5)]
    assert labels(cooks) == ["Home", "Eating out: everyone", "Leftovers", "No dinner"]


def test_single_cook_has_no_subsets():
    assert labels(COOKS[:1]) == [
        "Home",
        "Eating out: everyone",
        "Leftovers",
        "No dinner",
    ]


def pick(menu):
    return option_for_menu(menu, COOKS, kind_options(COOKS)).label


def test_option_for_menu():
    assert pick(None) == "Home"
    assert pick(MenuEntry(title="Soup")) == "Home"
    assert pick(MenuEntry(kind="eating_out")) == "Eating out: everyone"
    assert (
        pick(MenuEntry(kind="eating_out", going_out=["bob", "ann"]))
        == "Eating out: Ann, Bob"
    )
    assert pick(MenuEntry(kind="eating_out", going_out=["cy"])) == "Eating out: Cy"
    assert (
        pick(MenuEntry(kind="eating_out", going_out=["ann", "bob", "cy"]))
        == "Eating out: everyone"
    )
    assert pick(MenuEntry(kind="leftovers")) == "Leftovers"
    assert pick(MenuEntry(kind="skip")) == "No dinner"
