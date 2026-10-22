"""Integration tests against a real (test) Home Assistant core."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.dinnersense.availability import AvailabilityTracker
from custom_components.dinnersense.const import DOMAIN
from custom_components.dinnersense.models import Settings, build_cooks
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

NOW = "2026-03-02 12:00:00+00:00"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    cooks = build_cooks(["Ann", "Bob", "Cy"])
    e = MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        data={"name": "Test Household"},
        options={
            "cooks": [c.to_dict() for c in cooks],
            "settings": Settings().to_dict(),
        },
    )
    e.add_to_hass(hass)
    return e


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("freezer")
async def test_setup_creates_plan_and_entities(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    assert hass.states.get("sensor.test_household_dinner_today").state == "Ann"
    assert hass.states.get("sensor.test_household_dinner_tomorrow").state == "Bob"
    assert len(hass.states.async_entity_ids("select")) == 10
    day3 = hass.states.get("select.test_household_dinner_day_3")
    assert day3.state == "Ann"
    assert day3.attributes["date"] == "2026-03-05"
    assert day3.attributes["options"] == ["Ann", "Bob", "Cy"]


async def test_select_performs_swap_and_fires_events(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    swapped = async_capture_events(hass, "dinnersense_swapped")
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.test_household_dinner_day_3", "option": "Cy"},
        blocking=True,
    )
    assert hass.states.get("select.test_household_dinner_day_3").state == "Cy"
    assert hass.states.get("select.test_household_dinner_day_2").state == "Ann"
    assert len(swapped) == 1  # no feedback loop
    assert swapped[0].data["config_entry_id"] == entry.entry_id


async def test_get_plan_service(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    resp = await hass.services.async_call(
        DOMAIN,
        "get_plan",
        {"start": "2026-03-02", "end": "2026-03-04"},
        blocking=True,
        return_response=True,
    )
    assert [d["cook_name"] for d in resp["dinners"]] == ["Ann", "Bob", "Cy"]


async def test_swap_service_by_name_and_reload_preserves_state(
    hass, entry, freezer
) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN, "swap", {"date": "2026-03-05", "cook": "bob"}, blocking=True
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("select.test_household_dinner_day_3").state == "Bob"


async def test_nightly_roll_shifts_slots(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    rolled = async_capture_events(hass, "dinnersense_rolled")
    freezer.move_to(
        "2026-03-03 10:00:01+00:00"
    )  # 02:00 in the test time zone (US/Pacific)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert rolled
    assert hass.states.get("sensor.test_household_dinner_today").state == "Bob"
    assert hass.states.get("select.test_household_dinner_day_9").state != "unavailable"


async def test_unique_ids_are_stable(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    reg = er.async_get(hass)
    assert reg.async_get_entity_id("select", DOMAIN, f"{entry.entry_id}_day_3")


# -- Phase 2: menus, dishes, calendar -----------------------------------------


async def test_future_menu_shows_in_text_select_attrs_and_calendar(
    hass, entry, freezer
) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    # Next Thursday 2026-03-05 is day 3.
    await hass.services.async_call(
        DOMAIN, "set_menu", {"date": "2026-03-05", "title": "Lasagne"}, blocking=True
    )
    assert hass.states.get("text.test_household_dinner_menu_day_3").state == "Lasagne"
    assert (
        hass.states.get("select.test_household_dinner_day_3").attributes["menu_title"]
        == "Lasagne"
    )
    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.test_household_dinner",
            "start_date_time": "2026-03-05 00:00:00",
            "end_date_time": "2026-03-06 00:00:00",
        },
        blocking=True,
        return_response=True,
    )
    summaries = [
        e["summary"] for e in events["calendar.test_household_dinner"]["events"]
    ]
    assert summaries == ["Ann — Lasagne"]


async def test_typing_tacos_links_the_dish_via_text_entity(
    hass, entry, freezer
) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "create_dish",
        {
            "name": "Tacos",
            "tags": ["mexican"],
            "recipe": {"ingredients": [{"name": "Tortillas"}]},
        },
        blocking=True,
        return_response=True,
    )
    await hass.services.async_call(
        "text",
        "set_value",
        {"entity_id": "text.test_household_dinner_menu_day_1", "value": "tacos"},
        blocking=True,
    )
    attrs = hass.states.get("text.test_household_dinner_menu_day_1").attributes
    assert attrs["dish_id"] == "tacos"
    assert hass.states.get("text.test_household_dinner_menu_day_1").state == "Tacos"
    dishes = await hass.services.async_call(
        DOMAIN, "list_dishes", {}, blocking=True, return_response=True
    )
    assert [d["name"] for d in dishes["dishes"]] == ["Tacos"]


async def test_edit_past_menu_and_it_persists_across_reload(
    hass, entry, freezer
) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "set_menu",
        {"date": "2026-02-23", "title": "Burgers", "rating": 5},
        blocking=True,
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hist = await hass.services.async_call(
        DOMAIN,
        "get_history",
        {"start": "2026-02-20", "end": "2026-03-04"},
        blocking=True,
        return_response=True,
    )
    assert [(d["date"], d["menu_title"]) for d in hist["dinners"] if d["menu"]] == [
        ("2026-02-23", "Burgers")
    ]


# -- Phase 3: availability, conflicts, scores ---------------------------------


def _entry_with(hass, availability_by_cook=None):
    cooks = build_cooks(["Ann", "Bob", "Cy"])
    for c in cooks:
        c.availability = (availability_by_cook or {}).get(c.id, [])
    e = MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        data={"name": "Test Household"},
        options={
            "cooks": [c.to_dict() for c in cooks],
            "settings": Settings().to_dict(),
        },
    )
    e.add_to_hass(hass)
    return e


def _slot_cooks(hass, n=10):
    return [
        hass.states.get(f"select.test_household_dinner_day_{i}").state for i in range(n)
    ]


async def test_input_datetime_until_keeps_cook_out_and_follows_changes(
    hass, freezer
) -> None:
    freezer.move_to(NOW)
    hass.states.async_set("input_datetime.bob_away", "2026-03-04")
    entry = _entry_with(
        hass, {"bob": [{"type": "until", "entity_id": "input_datetime.bob_away"}]}
    )
    await _setup(hass, entry)
    assert "Bob" not in _slot_cooks(hass)[:3]  # Mar 2-4
    hass.states.async_set("input_datetime.bob_away", "2026-03-01")  # already past
    await hass.async_block_till_done()
    assert _slot_cooks(hass)[1] == "Bob"


async def test_binary_sensor_moves_todays_cook_only_while_on(hass, freezer) -> None:
    freezer.move_to(NOW)
    hass.states.async_set("binary_sensor.ann_away", "on")
    entry = _entry_with(
        hass,
        {"ann": [{"type": "binary_sensor", "entity_id": "binary_sensor.ann_away"}]},
    )
    await _setup(hass, entry)
    assert hass.states.get("sensor.test_household_dinner_today").state != "Ann"
    hass.states.async_set("binary_sensor.ann_away", "off")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.test_household_dinner_today").state == "Ann"


async def test_calendar_source_blocks_event_days(hass, freezer) -> None:
    freezer.move_to(NOW)
    events = [{"start": "2026-03-03", "end": "2026-03-05", "summary": "Bob away"}]
    entry = _entry_with(
        hass,
        {
            "bob": [
                {"type": "calendar", "entity_id": "calendar.family", "keyword": "away"}
            ]
        },
    )
    with patch.object(AvailabilityTracker, "_calendar_events", return_value=events):
        await _setup(hass, entry)
    slots = _slot_cooks(hass)
    assert slots[1] != "Bob"  # Mar 3 is Bob's natural day, now blocked
    assert "Bob" not in slots[1:3]  # Mar 3 and 4


async def test_set_and_clear_unavailable_services(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    before = _slot_cooks(hass)
    await hass.services.async_call(
        DOMAIN,
        "set_unavailable",
        {"cook": "Bob", "start": "2026-03-02", "end": "2026-03-05"},
        blocking=True,
    )
    assert "Bob" not in _slot_cooks(hass)[:4]
    await hass.services.async_call(
        DOMAIN, "clear_unavailable", {"cook": "bob"}, blocking=True
    )
    assert _slot_cooks(hass) == before


async def test_manual_unavailability_survives_reload(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "set_unavailable",
        {"cook": "bob", "start": "2026-03-02", "end": "2026-03-05"},
        blocking=True,
    )
    expected = _slot_cooks(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _slot_cooks(hass) == expected


async def test_conflict_event_for_locked_dinner(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN, "reassign", {"date": "2026-03-06", "cook": "cy"}, blocking=True
    )
    conflicts = async_capture_events(hass, "dinnersense_conflict")
    await hass.services.async_call(
        DOMAIN,
        "set_unavailable",
        {"cook": "cy", "start": "2026-03-06", "end": "2026-03-06"},
        blocking=True,
    )
    assert [c.data["date"] for c in conflicts] == ["2026-03-06"]
    assert hass.states.get("select.test_household_dinner_day_4").state == "Cy"


async def test_score_sensors_count_swaps(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.test_household_dinner_day_3", "option": "Cy"},
        blocking=True,
    )
    ann = hass.states.get("sensor.test_household_dinner_score_ann")
    cy = hass.states.get("sensor.test_household_dinner_score_cy")
    assert ann.attributes["swaps_given"] == 1 and ann.attributes["swaps_taken"] == 0
    assert cy.attributes["swaps_taken"] == 1
    assert ann.state == "0" and ann.attributes["debt"] == 0


async def test_options_flow_saves_cook_availability(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cooks"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"cook": "bob"}
    )
    assert result["step_id"] == "cook"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"color": "#3b82f6", "away_until": "input_datetime.bob_away"},
    )
    assert result["type"] == "create_entry"
    bob = next(c for c in entry.options["cooks"] if c["id"] == "bob")
    assert bob["color"] == "#3b82f6"
    assert bob["availability"] == [
        {"type": "until", "entity_id": "input_datetime.bob_away"}
    ]


# -- Phase 4: stats and tips ------------------------------------------------------


async def _burger_history(hass) -> None:
    for day in ("2026-02-01", "2026-02-08", "2026-02-15"):
        await hass.services.async_call(
            DOMAIN, "set_menu", {"date": day, "title": "Burgers"}, blocking=True
        )


def _fake_ai(hass, answer=None, error=None):
    calls = []

    async def handler(call):
        calls.append(call.data)
        if error:
            raise error
        return {"conversation_id": "x", "data": answer}

    hass.services.async_register(
        "ai_task", "generate_data", handler, supports_response=SupportsResponse.ONLY
    )
    return calls


async def _entry_with_ai(hass):
    cooks = build_cooks(["Ann", "Bob", "Cy"])
    settings = Settings(ai_task_entity="ai_task.fake").to_dict()
    e = MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        data={"name": "Test Household"},
        options={"cooks": [c.to_dict() for c in cooks], "settings": settings},
    )
    e.add_to_hass(hass)
    return e


async def test_rule_tips_without_ai_task(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    await _burger_history(hass)
    resp = await hass.services.async_call(
        DOMAIN, "generate_tips", {}, blocking=True, return_response=True
    )
    assert resp["source"] == "rules"
    assert "No Burgers for 15 days" in resp["tips"]
    state = hass.states.get("sensor.test_household_dinner_tips")
    assert state.attributes["source"] == "rules"
    assert state.attributes["tips"] == resp["tips"]
    assert state.state == resp["tips"][0]


async def test_llm_tips_used_when_numbers_check_out(hass, freezer) -> None:
    freezer.move_to(NOW)
    calls = _fake_ai(hass, {"tips": ["Burgers haven't been on the table for 15 days"]})
    entry = await _entry_with_ai(hass)
    await _setup(hass, entry)
    await _burger_history(hass)
    resp = await hass.services.async_call(
        DOMAIN, "generate_tips", {}, blocking=True, return_response=True
    )
    assert resp["source"] == "llm"
    assert resp["tips"] == ["Burgers haven't been on the table for 15 days"]
    assert calls[-1]["entity_id"] == "ai_task.fake"
    assert '"days_since_last": 15' in calls[-1]["instructions"]
    assert (
        hass.states.get("sensor.test_household_dinner_tips").attributes["source"]
        == "llm"
    )


async def test_llm_tip_with_invented_number_falls_back_to_rules(hass, freezer) -> None:
    freezer.move_to(NOW)
    _fake_ai(hass, {"tips": ["Burgers: 99 days without! Wow."]})
    entry = await _entry_with_ai(hass)
    await _setup(hass, entry)
    await _burger_history(hass)
    resp = await hass.services.async_call(
        DOMAIN, "generate_tips", {}, blocking=True, return_response=True
    )
    assert resp["source"] == "rules"
    assert "No Burgers for 15 days" in resp["tips"]


async def test_ai_task_failure_falls_back_to_rules(hass, freezer) -> None:
    freezer.move_to(NOW)
    _fake_ai(hass, error=HomeAssistantError("model offline"))
    entry = await _entry_with_ai(hass)
    await _setup(hass, entry)
    await _burger_history(hass)
    resp = await hass.services.async_call(
        DOMAIN, "generate_tips", {}, blocking=True, return_response=True
    )
    assert resp["source"] == "rules" and resp["tips"]


async def test_menu_change_triggers_debounced_refresh(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    before = hass.states.get("sensor.test_household_dinner_tips").attributes[
        "generated_at"
    ]
    await _burger_history(hass)  # three quick edits
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    after = hass.states.get("sensor.test_household_dinner_tips").attributes
    assert after["generated_at"] != before
    assert "No Burgers for 15 days" in after["tips"]


async def test_get_stats_service(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    await _burger_history(hass)
    stats = await hass.services.async_call(
        DOMAIN, "get_stats", {}, blocking=True, return_response=True
    )
    burgers = next(r for r in stats["dishes"] if r["name"] == "Burgers")
    assert (burgers["days_since"], burgers["count_90"]) == (15, 3)
    short = await hass.services.async_call(
        DOMAIN, "get_stats", {"period": 7}, blocking=True, return_response=True
    )
    assert not any(r["name"] == "Burgers" for r in short["dishes"])
