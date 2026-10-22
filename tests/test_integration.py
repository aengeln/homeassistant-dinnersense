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
