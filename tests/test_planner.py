"""Planner tests (the planner itself has no Home Assistant imports)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from custom_components.dinnersense import models
from custom_components.dinnersense import planner as planner_mod

TODAY = date(2026, 3, 2)


class Harness:
    def __init__(self, **settings):
        self.events: list[tuple[str, dict]] = []
        self.saves = 0
        self.notified = 0
        cooks = [models.Cook(c, c.title(), i) for i, c in enumerate(["ann", "bob", "cy"])]
        self.planner = planner_mod.Planner(
            cooks,
            models.Settings(**settings),
            fire_event=lambda n, d: self.events.append((n, d)),
            save=self._save,
            now=lambda: datetime(2026, 3, 2, 12, 0),
        )
        self.planner.add_listener(self._notify)

    def _save(self):
        self.saves += 1

    def _notify(self):
        self.notified += 1

    def cooks(self, start=TODAY, n=10):
        return [self.planner.dinners[start + timedelta(days=i)].cook for i in range(n)]


def test_roll_fills_horizon_and_fires_event():
    h = Harness()
    h.planner.roll(TODAY)
    assert len(h.planner.dinners) == 10
    assert h.events[-1][0] == "dinnersense_rolled"
    assert h.saves == 1 and h.notified == 1


def test_roll_next_day_shifts_window_and_closes_past_as_done():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.roll(TODAY + timedelta(days=1))
    assert h.planner.dinners[TODAY].status == "done"
    assert len(h.planner.dinners) == 11
    assert h.planner.dinners[TODAY + timedelta(days=10)].cook == "bob"


def test_unconfirmed_past_can_be_missed():
    h = Harness(unconfirmed_past="missed")
    h.planner.roll(TODAY)
    h.planner.roll(TODAY + timedelta(days=3))
    assert [h.planner.dinners[TODAY + timedelta(days=i)].status for i in range(3)] == ["missed"] * 3


def test_roll_never_deletes_history_or_overwrites_locked():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.reassign(TODAY + timedelta(days=4), "cy")
    h.planner.roll(TODAY + timedelta(days=2))
    assert h.planner.dinners[TODAY].status == "done"
    assert h.planner.dinners[TODAY + timedelta(days=4)].cook == "cy"


def test_swap_updates_both_days_locks_and_fires_events():
    h = Harness()
    h.planner.roll(TODAY)
    day = TODAY + timedelta(days=3)  # ann
    h.planner.swap(day, "cy", TODAY)
    p = h.planner.dinners
    assert p[day].cook == "cy" and p[day].locked
    assert p[TODAY + timedelta(days=2)].cook == "ann"
    names = [n for n, _ in h.events]
    assert names[-2:] == ["dinnersense_swapped", "dinnersense_changed"]
    payload = h.events[-1][1]
    assert payload["before"][0]["cook"] == "ann" and payload["after"][0]["cook"] == "cy"
    assert p[day].history[-1].action == "swap"


def test_swap_moves_menus_with_cooks_when_enabled():
    for follows in (True, False):
        h = Harness(menu_follows_cook=follows)
        h.planner.roll(TODAY)
        a, b = TODAY + timedelta(days=3), TODAY + timedelta(days=2)
        h.planner.dinners[a].menu = models.MenuEntry(title="Tacos")
        h.planner.swap(a, "cy", TODAY)
        assert (h.planner.dinners[b].menu is not None) == follows
        assert (h.planner.dinners[a].menu is None) == follows


def test_swap_noop_same_cook_fires_nothing():
    h = Harness()
    h.planner.roll(TODAY)
    n = len(h.events)
    h.planner.swap(TODAY, "ann", TODAY)
    assert len(h.events) == n


def test_swap_past_and_skipped_are_refused():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.roll(TODAY + timedelta(days=1))
    with pytest.raises(planner_mod.PlannerError):
        h.planner.swap(TODAY, "bob", TODAY + timedelta(days=1))
    h.planner.skip(TODAY + timedelta(days=3))
    with pytest.raises(planner_mod.PlannerError):
        h.planner.swap(TODAY + timedelta(days=3), "bob", TODAY + timedelta(days=1))


def test_swap_partner_must_still_be_planned():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.skip(TODAY + timedelta(days=2))  # cy's next day is skipped
    h.planner.swap(TODAY + timedelta(days=3), "cy", TODAY)
    assert h.planner.dinners[TODAY + timedelta(days=2)].status == "skipped"
    assert h.planner.dinners[TODAY + timedelta(days=2)].cook == "cy"
    assert h.planner.dinners[TODAY + timedelta(days=5)].cook == "ann"


def test_reassign_works_in_the_past():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.roll(TODAY + timedelta(days=2))
    h.planner.reassign(TODAY, "cy")
    assert h.planner.dinners[TODAY].cook == "cy"


def test_set_cooks_rebuilds_unlocked_future():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.reassign(TODAY + timedelta(days=1), "ann")
    h.planner.set_cooks(h.planner.cooks[:2], TODAY)
    assert {d.cook for d in h.planner.dinners.values()} <= {"ann", "bob"}
    assert len(h.planner.dinners) == 10


def test_regenerate_keeps_locked():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.reassign(TODAY + timedelta(days=4), "bob")
    h.planner.regenerate(TODAY)
    assert h.planner.dinners[TODAY + timedelta(days=4)].cook == "bob"
    assert len(h.planner.dinners) == 10


def test_dinner_roundtrips_through_dict():
    h = Harness()
    h.planner.roll(TODAY)
    d = h.planner.dinners[TODAY]
    d.menu = models.MenuEntry(title="Tacos", rating=4)
    assert models.Dinner.from_dict(d.to_dict()) == d


# -- menus and dishes -------------------------------------------------------


def days(n):
    return TODAY + timedelta(days=n)


def test_set_menu_free_text_for_future_day():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.set_menu(days(3), TODAY, title="Pizza from Bella", kind="eating_out")
    menu = h.planner.dinners[days(3)].menu
    assert (menu.title, menu.dish_id, menu.kind) == ("Pizza from Bella", None, "eating_out")
    assert h.events[-1][0] == "dinnersense_changed"
    assert h.events[-1][1]["after"][0]["menu"]["title"] == "Pizza from Bella"


def test_typing_a_known_name_or_alias_links_the_dish_case_insensitively():
    h = Harness()
    h.planner.roll(TODAY)
    dish = h.planner.create_dish("Tacos", aliases=["Taco night"])
    h.planner.set_menu(days(1), TODAY, title="tacos")
    h.planner.set_menu(days(2), TODAY, title="TACO NIGHT")
    for n in (1, 2):
        menu = h.planner.dinners[days(n)].menu
        assert menu.dish_id == dish.id and menu.title == "Tacos"


def test_unknown_title_does_not_create_dish_by_default():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.set_menu(days(1), TODAY, title="Moose stew")
    assert h.planner.dishes == {}


def test_auto_create_dishes_when_enabled():
    h = Harness(auto_create_dishes=True)
    h.planner.roll(TODAY)
    h.planner.set_menu(days(1), TODAY, title="Moose stew")
    assert h.planner.dinners[days(1)].menu.dish_id == "moose_stew"
    assert "moose_stew" in h.planner.dishes


def test_set_menu_by_dish_id_and_unknown_dish_refused():
    h = Harness()
    h.planner.roll(TODAY)
    d = h.planner.create_dish("Soup")
    h.planner.set_menu(days(1), TODAY, dish_id=d.id)
    assert h.planner.dinners[days(1)].menu.title == "Soup"
    with pytest.raises(planner_mod.PlannerError):
        h.planner.set_menu(days(1), TODAY, dish_id="nope")


def test_clear_menu():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.set_menu(days(1), TODAY, title="X")
    h.planner.set_menu(days(1), TODAY)
    assert h.planner.dinners[days(1)].menu is None


def test_edit_past_menu_persists_through_roll():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.roll(days(3))
    h.planner.set_menu(days(0), days(3), title="Pasta", rating=4)
    h.planner.roll(days(4))
    menu = h.planner.dinners[days(0)].menu
    assert (menu.title, menu.rating) == ("Pasta", 4)


def test_menu_for_day_before_the_integration_creates_log_record():
    h = Harness()
    h.planner.roll(TODAY)
    old = TODAY - timedelta(days=20)
    h.planner.set_menu(old, TODAY, title="Burger")
    d = h.planner.dinners[old]
    assert d.status == "done" and d.cook == "" and d.menu.title == "Burger"
    h.planner.roll(TODAY)  # roll must cope with the unknown-cook record
    assert len(h.planner.dinners) == 11


def test_menu_beyond_horizon_extends_plan_and_far_future_is_refused():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.set_menu(days(15), TODAY, title="Midsummer")
    assert h.planner.dinners[days(15)].cook in {"ann", "bob", "cy"}
    assert len({days(i) for i in range(16)} - set(h.planner.dinners)) == 0
    with pytest.raises(planner_mod.PlannerError):
        h.planner.set_menu(days(90), TODAY, title="x")


def test_bad_kind_and_rating_refused():
    h = Harness()
    h.planner.roll(TODAY)
    with pytest.raises(planner_mod.PlannerError):
        h.planner.set_menu(days(1), TODAY, title="x", kind="picnic")
    with pytest.raises(planner_mod.PlannerError):
        h.planner.set_menu(days(1), TODAY, title="x", rating=9)


def test_duplicate_dish_refused_and_rename_updates_menus():
    h = Harness()
    h.planner.roll(TODAY)
    d = h.planner.create_dish("Tacos")
    with pytest.raises(planner_mod.PlannerError):
        h.planner.create_dish("tacos")
    h.planner.set_menu(days(1), TODAY, dish_id=d.id)
    h.planner.update_dish(d.id, name="Fish tacos")
    assert h.planner.dinners[days(1)].menu.title == "Fish tacos"


def test_delete_dish_keeps_menu_title():
    h = Harness()
    h.planner.roll(TODAY)
    d = h.planner.create_dish("Tacos")
    h.planner.set_menu(days(1), TODAY, dish_id=d.id)
    h.planner.delete_dish(d.id)
    menu = h.planner.dinners[days(1)].menu
    assert menu.dish_id is None and menu.title == "Tacos"


def test_get_history_stops_at_today():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.roll(days(3))
    got = h.planner.get_history(days(0), days(9), days(3))
    assert [d.date for d in got] == [days(i) for i in range(4)]


def test_swap_menu_follows_cook_event_carries_menu():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.set_menu(days(3), TODAY, title="Tacos")
    h.planner.swap(days(3), "cy", TODAY)
    assert h.planner.dinners[days(2)].menu.title == "Tacos"
