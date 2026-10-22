"""Pure tests for availability source resolution."""

from __future__ import annotations

from datetime import date

from custom_components.dinnersense.availability import (
    event_days,
    parse_date,
    source_windows,
    watched_entities,
)
from custom_components.dinnersense.models import Cook
from custom_components.dinnersense.rotation import AvailabilityWindow

TODAY = date(2026, 3, 2)


def win(start, end):
    return AvailabilityWindow("bob", start, end)


def states(mapping):
    return mapping.get


def test_parse_date_accepts_date_and_datetime_and_rejects_junk():
    assert parse_date("2026-03-06") == date(2026, 3, 6)
    assert parse_date("2026-03-06 10:00:00") == date(2026, 3, 6)
    for bad in (None, "", "unknown", "10:00:00", "unavailable"):
        assert parse_date(bad) is None


def test_event_days_all_day_end_is_exclusive():
    assert event_days("2026-03-04", "2026-03-06") == (
        date(2026, 3, 4),
        date(2026, 3, 5),
    )
    assert event_days("2026-03-04", "2026-03-05") == (
        date(2026, 3, 4),
        date(2026, 3, 4),
    )


def test_event_days_timed_events():
    assert event_days("2026-03-04T10:00:00+00:00", "2026-03-04T12:00:00+00:00") == (
        date(2026, 3, 4),
        date(2026, 3, 4),
    )
    # Ends exactly at midnight: that day isn't covered.
    assert event_days("2026-03-04T10:00:00+00:00", "2026-03-06T00:00:00+00:00") == (
        date(2026, 3, 4),
        date(2026, 3, 5),
    )
    assert event_days("garbage", "garbage") is None


def test_calendar_keyword_matches_summary_description_case_insensitively():
    events = [
        {"start": "2026-03-04", "end": "2026-03-05", "summary": "Bob AWAY"},
        {
            "start": "2026-03-10",
            "end": "2026-03-11",
            "summary": "x",
            "description": "away trip",
        },
        {"start": "2026-03-12", "end": "2026-03-13", "summary": "Dentist"},
    ]
    got = source_windows(
        "bob", {"type": "calendar", "entity_id": "c"}, TODAY, states({}), events
    )
    assert got == [
        win(date(2026, 3, 4), date(2026, 3, 4)),
        win(date(2026, 3, 10), date(2026, 3, 10)),
    ]
    custom = source_windows(
        "bob",
        {"type": "calendar", "entity_id": "c", "keyword": "dentist"},
        TODAY,
        states({}),
        events,
    )
    assert custom == [win(date(2026, 3, 12), date(2026, 3, 12))]


def test_until_source():
    src = {"type": "until", "entity_id": "input_datetime.a"}
    assert source_windows(
        "bob", src, TODAY, states({"input_datetime.a": "2026-03-06"}), []
    ) == [win(TODAY, date(2026, 3, 6))]
    # In the past, unknown or empty: no window.
    for value in ("2026-03-01", "unknown", None):
        assert (
            source_windows("bob", src, TODAY, states({"input_datetime.a": value}), [])
            == []
        )


def test_range_source_needs_both_and_ordered():
    src = {"type": "range", "from_entity": "f", "to_entity": "t"}
    ok = source_windows(
        "bob", src, TODAY, states({"f": "2026-03-04", "t": "2026-03-08"}), []
    )
    assert ok == [win(date(2026, 3, 4), date(2026, 3, 8))]
    assert (
        source_windows(
            "bob", src, TODAY, states({"f": "2026-03-08", "t": "2026-03-04"}), []
        )
        == []
    )
    assert source_windows("bob", src, TODAY, states({"f": "2026-03-04"}), []) == []


def test_binary_sensor_is_today_only_and_live():
    src = {"type": "binary_sensor", "entity_id": "binary_sensor.b"}
    assert source_windows("bob", src, TODAY, states({"binary_sensor.b": "on"}), []) == [
        win(TODAY, TODAY)
    ]
    assert (
        source_windows("bob", src, TODAY, states({"binary_sensor.b": "off"}), []) == []
    )


def test_watched_entities_excludes_calendars():
    cook = Cook(
        "bob",
        "Bob",
        availability=[
            {"type": "calendar", "entity_id": "calendar.x"},
            {"type": "until", "entity_id": "input_datetime.a"},
            {
                "type": "range",
                "from_entity": "input_datetime.f",
                "to_entity": "input_datetime.t",
            },
            {"type": "binary_sensor", "entity_id": "binary_sensor.b"},
        ],
    )
    assert watched_entities([cook]) == [
        "binary_sensor.b",
        "input_datetime.a",
        "input_datetime.f",
        "input_datetime.t",
    ]
