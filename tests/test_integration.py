"""Integration tests against a real (test) Home Assistant core."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.dinnersense.const import DOMAIN
from custom_components.dinnersense.models import Settings, build_cooks
from homeassistant.core import HomeAssistant
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
    freezer.move_to("2026-03-03 10:00:01+00:00")  # 02:00 in the test time zone (US/Pacific)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert rolled
    assert hass.states.get("sensor.test_household_dinner_today").state == "Bob"
    assert hass.states.get("select.test_household_dinner_day_9").state != "unavailable"


async def test_unique_ids_are_stable(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    reg = er.async_get(hass)
    assert reg.async_get_entity_id(
        "select", DOMAIN, f"{entry.entry_id}_day_3"
    )


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
    summaries = [e["summary"] for e in events["calendar.test_household_dinner"]["events"]]
    assert summaries == ["Ann — Lasagne"]


async def test_typing_tacos_links_the_dish_via_text_entity(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "create_dish",
        {"name": "Tacos", "tags": ["mexican"], "recipe": {"ingredients": [{"name": "Tortillas"}]}},
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


async def test_edit_past_menu_and_it_persists_across_reload(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN, "set_menu", {"date": "2026-02-23", "title": "Burgers", "rating": 5},
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
