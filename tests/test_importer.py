"""Import of past menus from entity history."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from custom_components.dinnersense.importer import titles_by_day

TODAY = date(2026, 3, 12)


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 3, day, hour, 0, tzinfo=UTC)


def test_last_real_value_of_each_day_wins():
    states = [
        (at(10, 8), "Tacoss"),
        (at(10, 9), "Tacos"),  # typo fixed
        (at(11, 12), "Pasta"),
        (at(12, 2), ""),  # nightly clearing
    ]
    assert titles_by_day(states, TODAY, 10) == {
        date(2026, 3, 10): "Tacos",
        date(2026, 3, 11): "Pasta",
    }


def test_empty_unknown_and_unavailable_are_ignored_even_after_a_value():
    states = [
        (at(10, 8), "Soup"),
        (at(10, 20), ""),
        (at(10, 21), "unavailable"),
        (at(10, 22), "unknown"),
    ]
    assert titles_by_day(states, TODAY, 10) == {date(2026, 3, 10): "Soup"}


def test_values_outside_the_window_and_future_are_ignored():
    states = [(at(1), "Old"), (at(11), "In"), (at(13), "Future")]
    assert titles_by_day(states, TODAY, 3) == {date(2026, 3, 11): "In"}


def test_input_order_does_not_matter_and_whitespace_is_trimmed():
    states = [(at(11, 15), "  Pasta  "), (at(11, 9), "Soup")]
    assert titles_by_day(states, TODAY, 10) == {date(2026, 3, 11): "Pasta"}


def test_window_of_one_day_is_today_only():
    states = [(at(11), "Yesterday"), (at(12), "Today")]
    assert titles_by_day(states, TODAY, 1) == {date(2026, 3, 12): "Today"}
    assert timedelta(days=0) == timedelta()
