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
        cooks = [
            models.Cook(c, c.title(), i) for i, c in enumerate(["ann", "bob", "cy"])
        ]
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
    assert [h.planner.dinners[TODAY + timedelta(days=i)].status for i in range(3)] == [
        "missed"
    ] * 3


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
    assert (menu.title, menu.dish_id, menu.kind) == (
        "Pizza from Bella",
        None,
        "eating_out",
    )
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


# -- availability, debt, conflicts, scores -----------------------------------


def snapshot(h):
    return (
        {d: v.cook for d, v in h.planner.dinners.items()},
        dict(h.planner.debt),
    )


def test_away_cook_gets_no_dinners_in_range_and_counts_even_out_after():
    h = Harness(horizon_days=14)
    h.planner.roll(TODAY)
    h.planner.add_unavailable("bob", days(0), days(5), TODAY)
    assert "bob" not in h.cooks(TODAY, 6)
    # After the horizon is extended far enough, counts even out.
    h.planner.roll(days(14))
    h.planner.roll(days(28))
    counts = {c: 0 for c in ("ann", "bob", "cy")}
    for d in h.planner.dinners.values():
        counts[d.cook] += 1
    assert max(counts.values()) - min(counts.values()) <= 3


def test_rebuild_keeps_menus_ids_and_locked_days():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.set_menu(days(1), TODAY, title="Soup")
    ident = h.planner.dinners[days(1)].id
    h.planner.reassign(days(2), "ann")  # locked
    h.planner.add_unavailable("bob", days(0), days(3), TODAY)
    kept = h.planner.dinners[days(1)]
    assert kept.id == ident and kept.menu.title == "Soup" and kept.cook != "bob"
    assert (
        h.planner.dinners[days(2)].cook == "ann" and h.planner.dinners[days(2)].locked
    )


def test_clear_unavailable_restores_the_original_plan():
    h = Harness()
    h.planner.roll(TODAY)
    original = snapshot(h)
    h.planner.add_unavailable("bob", days(0), days(5), TODAY)
    assert snapshot(h) != original
    h.planner.clear_unavailable("bob", None, None, TODAY)
    assert snapshot(h) == original


def test_rebuilding_twice_does_not_double_count_debt():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.add_unavailable("bob", days(0), days(5), TODAY)
    once = snapshot(h)
    h.planner.regenerate(TODAY)
    h.planner.regenerate(TODAY)
    assert snapshot(h) == once


def test_external_windows_rebuild_only_when_changed():
    h = Harness()
    h.planner.roll(TODAY)
    win = [planner_mod.AvailabilityWindow("bob", days(0), days(2))]
    h.planner.set_external_windows(win, TODAY)
    saves, events = h.saves, len(h.events)
    h.planner.set_external_windows(list(win), TODAY)
    assert (h.saves, len(h.events)) == (saves, events)
    h.planner.set_external_windows([], TODAY)
    assert h.saves == saves + 1


def test_unavailable_validation():
    h = Harness()
    h.planner.roll(TODAY)
    with pytest.raises(planner_mod.PlannerError):
        h.planner.add_unavailable("zed", days(0), days(1), TODAY)
    with pytest.raises(planner_mod.PlannerError):
        h.planner.add_unavailable("bob", days(3), days(1), TODAY)


def conflicts(h):
    return [d for n, d in h.events if n == "dinnersense_conflict"]


def test_locked_dinner_conflict_fires_once_and_is_not_changed():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.reassign(days(4), "cy")
    h.planner.add_unavailable("cy", days(3), days(5), TODAY)
    assert h.planner.dinners[days(4)].cook == "cy"
    assert [c["date"] for c in conflicts(h)] == [days(4).isoformat()]
    h.planner.regenerate(TODAY)
    assert len(conflicts(h)) == 1  # not repeated


def test_unlocked_dinner_never_conflicts():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.add_unavailable("bob", days(0), days(9), TODAY)
    assert conflicts(h) == []


def test_conflict_refires_after_it_clears():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.reassign(days(4), "cy")
    h.planner.add_unavailable("cy", days(4), days(4), TODAY)
    h.planner.clear_unavailable("cy", None, None, TODAY)
    h.planner.add_unavailable("cy", days(4), days(4), TODAY)
    assert len(conflicts(h)) == 2


def test_cook_stats_cooked_counts_and_swaps():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.swap(days(3), "cy", TODAY)  # ann's day handed to cy
    h.planner.roll(days(4))  # days 0..3 become done
    ann = h.planner.cook_stats("ann", days(4))
    cy = h.planner.cook_stats("cy", days(4))
    assert ann["swaps_given"] == 1 and ann["swaps_taken"] == 0
    assert cy["swaps_taken"] == 1 and cy["swaps_given"] == 0
    assert (
        ann["cooked_30"]
        + cy["cooked_30"]
        + h.planner.cook_stats("bob", days(4))["cooked_30"]
        == 4
    )


# -- import_menus -----------------------------------------------------------------


def test_import_menus_sets_past_days_in_one_change():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.roll(days(3))
    h.planner.create_dish("Tacos")
    saves, events = h.saves, len(h.events)
    imported, skipped = h.planner.import_menus(
        {days(0): "tacos", days(1): "Moose stew", days(-20): "Burger"}, days(3)
    )
    assert [i["title"] for i in imported] == ["Burger", "Tacos", "Moose stew"]
    assert skipped == []
    assert (
        h.planner.dinners[days(0)].menu.dish_id == "tacos"
    )  # linked, case-insensitive
    assert h.planner.dinners[days(1)].menu.dish_id is None
    old = h.planner.dinners[days(-20)]
    assert old.cook == "" and old.menu.title == "Burger"
    assert (h.saves, len(h.events)) == (saves + 1, events + 1)
    assert h.events[-1][1]["reason"] == "import"
    assert old.history[-1].detail["source"] == "import"


def test_import_skips_existing_menus_unless_overwrite_and_future_days():
    h = Harness()
    h.planner.roll(TODAY)
    h.planner.roll(days(3))
    h.planner.set_menu(days(1), days(3), title="Keep me")
    imported, skipped = h.planner.import_menus(
        {days(1): "Imported", days(2): "New", days(5): "Later"}, days(3)
    )
    assert [i["date"] for i in imported] == [days(2).isoformat()]
    assert {(s["date"], s["reason"]) for s in skipped} == {
        (days(1).isoformat(), "has_menu"),
        (days(5).isoformat(), "future"),
    }
    assert h.planner.dinners[days(1)].menu.title == "Keep me"
    h.planner.import_menus({days(1): "Imported"}, days(3), overwrite=True)
    assert h.planner.dinners[days(1)].menu.title == "Imported"


def test_import_with_nothing_to_do_commits_nothing():
    h = Harness()
    h.planner.roll(TODAY)
    saves = h.saves
    assert h.planner.import_menus({}, TODAY) == ([], [])
    assert h.saves == saves


def test_import_respects_auto_create_dishes():
    h = Harness(auto_create_dishes=True)
    h.planner.roll(TODAY)
    h.planner.import_menus({days(0): "Moose stew"}, TODAY)
    assert "moose_stew" in h.planner.dishes
