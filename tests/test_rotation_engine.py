"""Tests for the HA-independent rotation engine."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.dinnersense.rotation import (
    AvailabilityWindow,
    NoUpcomingDinner,
    PastDinner,
    RotationEngine,
    UnknownCook,
    UnknownDinner,
)

TODAY = date(2026, 3, 2)  # a Monday
COOKS = ["ann", "bob", "cy"]


def cooks(plan, start=TODAY, days=10):
    return [plan[start + timedelta(days=i)].cook_id for i in range(days)]


def test_generates_full_horizon_in_rotation_order():
    result = RotationEngine(COOKS).generate({}, TODAY, 10)
    assert cooks(result.plan) == [
        "ann",
        "bob",
        "cy",
        "ann",
        "bob",
        "cy",
        "ann",
        "bob",
        "cy",
        "ann",
    ]
    assert len(result.added) == 10


def test_generation_is_deterministic():
    engine = RotationEngine(COOKS)
    assert engine.generate({}, TODAY, 10) == engine.generate({}, TODAY, 10)


def test_generation_is_idempotent():
    engine = RotationEngine(COOKS)
    first = engine.generate({}, TODAY, 10)
    second = engine.generate(first.plan, TODAY, 10)
    assert second.added == ()
    assert second.plan == first.plan


def test_dinner_days_are_respected():
    engine = RotationEngine(COOKS, dinner_days={0, 2, 4})  # Mon, Wed, Fri
    plan = engine.generate({}, TODAY, 7).plan
    assert sorted(d.weekday() for d in plan) == [0, 2, 4]


def test_roll_continues_from_last_planned_cook_after_a_swap():
    engine = RotationEngine(COOKS)
    plan = engine.generate({}, TODAY, 3).plan  # ann bob cy
    swapped = engine.swap(plan, TODAY, "cy", TODAY, 3).plan  # cy bob ann
    tomorrow = TODAY + timedelta(days=1)
    rolled = engine.generate(swapped, tomorrow, 3).plan
    # Last planned cook is ann (day 3 of the old plan), so next is bob.
    assert rolled[TODAY + timedelta(days=3)].cook_id == "bob"


def test_generation_never_overwrites_locked_or_past():
    engine = RotationEngine(COOKS)
    plan = engine.reassign(engine.generate({}, TODAY, 3).plan, TODAY, "cy")
    later = engine.generate(plan, TODAY + timedelta(days=1), 5)
    assert later.plan[TODAY].cook_id == "cy"
    assert later.plan[TODAY].locked


def test_swap_exchanges_with_next_upcoming_day_of_that_cook():
    engine = RotationEngine(COOKS)
    plan = engine.generate({}, TODAY, 10).plan
    day = TODAY + timedelta(days=3)  # ann
    result = engine.swap(plan, day, "cy", TODAY, 10)
    assert result.changed
    assert result.date_b == TODAY + timedelta(days=2)  # cy's next day
    assert result.plan[day].cook_id == "cy"
    assert result.plan[result.date_b].cook_id == "ann"
    assert result.plan[day].locked and result.plan[result.date_b].locked


def test_swap_same_cook_is_noop():
    engine = RotationEngine(COOKS)
    plan = engine.generate({}, TODAY, 5).plan
    result = engine.swap(plan, TODAY, "ann", TODAY, 5)
    assert not result.changed
    assert result.plan == plan


def test_swap_extends_horizon_when_cook_has_no_upcoming_day():
    engine = RotationEngine(COOKS, dinner_days={0})  # Mondays only
    plan = engine.generate({}, TODAY, 7).plan  # one Monday: ann
    result = engine.swap(plan, TODAY, "bob", TODAY, 7)
    assert result.changed
    assert result.date_b == TODAY + timedelta(days=7)
    assert result.plan[TODAY].cook_id == "bob"
    assert result.plan[result.date_b].cook_id == "ann"


def test_swap_with_no_upcoming_day_even_after_extension_raises():
    engine = RotationEngine(
        COOKS,
        dinner_days={0},
        availability=[
            AvailabilityWindow("bob", TODAY, TODAY + timedelta(days=365)),
        ],
    )
    plan = engine.generate({}, TODAY, 7).plan
    with pytest.raises(NoUpcomingDinner):
        engine.swap(plan, TODAY, "bob", TODAY, 7)


def test_swap_past_dinner_is_rejected():
    engine = RotationEngine(COOKS)
    plan = engine.generate({}, TODAY - timedelta(days=2), 5).plan
    with pytest.raises(PastDinner):
        engine.swap(plan, TODAY - timedelta(days=1), "cy", TODAY, 5)


def test_swap_unknown_cook_or_day():
    engine = RotationEngine(COOKS)
    plan = engine.generate({}, TODAY, 3).plan
    with pytest.raises(UnknownCook):
        engine.swap(plan, TODAY, "zed", TODAY, 3)
    with pytest.raises(UnknownDinner):
        engine.swap(plan, TODAY + timedelta(days=30), "bob", TODAY, 3)


def test_swap_ignores_past_days_of_partner():
    engine = RotationEngine(COOKS)
    # ann(-2) bob(-1) cy(0) ann(1) bob(2): bob's past day must not be the partner.
    plan = engine.generate({}, TODAY - timedelta(days=2), 5).plan
    result = engine.swap(plan, TODAY + timedelta(days=1), "bob", TODAY, 5)
    assert result.date_b == TODAY + timedelta(days=2)


def test_reassign_changes_only_one_day_and_works_in_the_past():
    engine = RotationEngine(COOKS)
    past = TODAY - timedelta(days=2)
    plan = engine.generate({}, past, 5).plan
    new = engine.reassign(plan, past, "cy")
    assert new[past].cook_id == "cy" and new[past].locked
    assert {d: a for d, a in new.items() if d != past} == {
        d: a for d, a in plan.items() if d != past
    }


def test_unavailable_cook_is_skipped_and_repays_debt():
    away = AvailabilityWindow("bob", TODAY, TODAY + timedelta(days=5))
    engine = RotationEngine(COOKS, availability=[away])
    result = engine.generate({}, TODAY, 12)
    plan = result.plan
    in_range = cooks(plan, TODAY, 6)
    assert "bob" not in in_range
    # After returning, bob cooks sooner than a plain rotation would give.
    after = cooks(plan, TODAY + timedelta(days=6), 6)
    assert after[0] == "bob" or after[1] == "bob"
    counts = {c: sum(a.cook_id == c for a in plan.values()) for c in COOKS}
    assert max(counts.values()) - min(counts.values()) <= 2


def test_no_back_to_back_repayment_when_others_available():
    away = AvailabilityWindow("bob", TODAY, TODAY + timedelta(days=9))
    engine = RotationEngine(COOKS, availability=[away])
    plan = engine.generate({}, TODAY, 20).plan
    seq = cooks(plan, TODAY, 20)
    assert all(a != b for a, b in zip(seq, seq[1:], strict=False))


def test_everyone_unavailable_still_assigns():
    away = [AvailabilityWindow(c, TODAY, TODAY) for c in COOKS]
    plan = RotationEngine(COOKS, availability=away).generate({}, TODAY, 2).plan
    assert TODAY in plan


def test_engine_rejects_bad_cook_order():
    with pytest.raises(ValueError):
        RotationEngine([])
    with pytest.raises(ValueError):
        RotationEngine(["a", "a"])
