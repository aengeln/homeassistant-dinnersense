"""Planner tests. The planner is HA-free, but it lives in the component package,
so it is loaded by file path with the package stubbed out."""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    pkg = types.ModuleType("dinnersense_pkg")
    pkg.__path__ = [str(ROOT)]
    sys.modules["dinnersense_pkg"] = pkg
    for name in ("rotation", "models", "planner"):
        full = f"dinnersense_pkg.{name}"
        target = ROOT / name / "__init__.py" if name == "rotation" else ROOT / f"{name}.py"
        spec = importlib.util.spec_from_file_location(
            full, target, submodule_search_locations=[str(ROOT / name)] if name == "rotation" else None
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
    return sys.modules["dinnersense_pkg.planner"], sys.modules["dinnersense_pkg.models"]


planner_mod, models = _load()
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
