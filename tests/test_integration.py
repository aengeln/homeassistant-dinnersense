"""Integration tests against a real (test) Home Assistant core."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
import voluptuous as vol
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.dinnersense.availability import AvailabilityTracker
from custom_components.dinnersense.const import DOMAIN
from custom_components.dinnersense.models import Settings, build_cooks
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
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
    day_selects = [
        e for e in hass.states.async_entity_ids("select") if "dinner_day_" in e
    ]
    assert len(day_selects) == 10
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
    hass.states.async_set("ai_task.fake", "unknown")
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


async def _tips_with_ai_setup(hass, freezer, prepare):
    """Set up a household whose AI Task misbehaves in some way; return tips response."""
    freezer.move_to(NOW)
    prepare()
    entry = await _entry_with_ai(hass)
    await _setup(hass, entry)
    await _burger_history(hass)
    return await hass.services.async_call(
        DOMAIN, "generate_tips", {}, blocking=True, return_response=True
    )


async def test_ai_task_entity_configured_but_integration_missing(hass, freezer) -> None:
    # No ai_task service and no entity at all (e.g. the AI integration was removed).
    resp = await _tips_with_ai_setup(hass, freezer, lambda: None)
    assert resp["source"] == "rules" and "No Burgers for 15 days" in resp["tips"]


async def test_ai_task_entity_unavailable_is_not_called(hass, freezer) -> None:
    calls = []

    def prepare():
        calls.extend(_fake_ai(hass, {"tips": ["x"]}))
        hass.states.async_set("ai_task.fake", "unavailable")

    resp = await _tips_with_ai_setup(hass, freezer, prepare)
    assert resp["source"] == "rules" and calls == []


async def test_ai_task_unexpected_errors_never_break_tips(hass, freezer) -> None:
    for error in (RuntimeError("boom"), vol.Invalid("bad"), KeyError("x")):
        hass.services.async_remove("ai_task", "generate_data")
        resp = await _tips_with_ai_setup(
            hass, freezer, lambda error=error: _fake_ai(hass, error=error)
        )
        assert resp["source"] == "rules" and resp["tips"]
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()


async def test_garbage_ai_answers_fall_back(hass, freezer) -> None:
    for answer in (None, {"tips": []}, {"tips": [42]}, "", {"other": 1}):
        hass.services.async_remove("ai_task", "generate_data")
        resp = await _tips_with_ai_setup(
            hass, freezer, lambda answer=answer: _fake_ai(hass, answer)
        )
        assert resp["source"] == "rules"
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()


async def test_household_works_with_no_ai_task_at_all(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    assert entry.options["settings"]["ai_task_entity"] is None
    assert hass.states.get("sensor.test_household_dinner_tips") is not None
    assert hass.states.get("sensor.test_household_dinner_today").state == "Ann"


# -- Phase 5: shopping list -------------------------------------------------------


def _fake_todo(hass, items=None, entity="todo.shopping_list"):
    """Register in-memory todo.get_items/add_item; return the item list."""
    store = [{"summary": s, "status": "needs_action"} for s in (items or [])]

    async def get_items(call):
        wanted = call.data.get("status") or ["needs_action", "completed"]
        return {entity: {"items": [i for i in store if i["status"] in wanted]}}

    async def add_item(call):
        store.append({"summary": call.data["item"], "status": "needs_action"})

    hass.services.async_register(
        "todo", "get_items", get_items, supports_response=SupportsResponse.ONLY
    )
    hass.services.async_register("todo", "add_item", add_item)
    hass.states.async_set(entity, "0")
    return store


async def _household_with_tacos(hass, freezer, todo_entity="todo.shopping_list"):
    freezer.move_to(NOW)
    cooks = build_cooks(["Ann", "Bob", "Cy"])
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Household",
        data={"name": "Test Household"},
        options={
            "cooks": [c.to_dict() for c in cooks],
            "settings": Settings(todo_entity=todo_entity).to_dict(),
        },
    )
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "create_dish",
        {
            "name": "Tacos",
            "recipe": {
                "ingredients": [
                    {"name": "Tortillas", "quantity": "8"},
                    {"name": "Minced beef", "quantity": "500 g"},
                    {"name": "Lime"},
                ]
            },
        },
        blocking=True,
        return_response=True,
    )
    await hass.services.async_call(
        DOMAIN, "set_menu", {"date": "2026-03-02", "title": "Tacos"}, blocking=True
    )
    return entry


async def test_button_adds_todays_ingredients_as_name_x_quantity(hass, freezer) -> None:
    items = _fake_todo(hass)
    await _household_with_tacos(hass, freezer)
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.test_household_add_ingredients_today"},
        blocking=True,
    )
    assert [i["summary"] for i in items] == [
        "Tortillas x 8",
        "Minced beef x 500 g",
        "Lime",
    ]


async def test_second_press_skips_duplicates_but_completed_items_dont_count(
    hass, freezer
) -> None:
    items = _fake_todo(hass, ["milk", "TORTILLAS x 12"])
    items.append({"summary": "Lime", "status": "completed"})  # already bought
    await _household_with_tacos(hass, freezer)
    resp = await hass.services.async_call(
        DOMAIN, "add_ingredients_to_list", {}, blocking=True, return_response=True
    )
    assert resp == {"added": ["Minced beef x 500 g", "Lime"], "skipped": ["Tortillas"]}
    again = await hass.services.async_call(
        DOMAIN, "add_ingredients_to_list", {}, blocking=True, return_response=True
    )
    assert again == {
        "added": [],
        "skipped": ["Tortillas", "Minced beef", "Lime"],
    }
    assert [i["summary"] for i in items if i["status"] == "needs_action"].count(
        "Lime"
    ) == 1


async def test_service_can_target_another_date_and_todo_entity(hass, freezer) -> None:
    other = _fake_todo(hass, entity="todo.other")
    await _household_with_tacos(hass, freezer, todo_entity=None)
    await hass.services.async_call(
        DOMAIN, "set_menu", {"date": "2026-03-03", "title": "tacos"}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN,
        "add_ingredients_to_list",
        {"date": "2026-03-03", "todo_entity": "todo.other"},
        blocking=True,
    )
    assert len(other) == 3


async def test_missing_todo_entity_gives_a_clear_error(hass, freezer) -> None:
    await _household_with_tacos(hass, freezer, todo_entity=None)
    with pytest.raises(ServiceValidationError, match="No todo list set"):
        await hass.services.async_call(
            DOMAIN, "add_ingredients_to_list", {}, blocking=True
        )
    button = "button.test_household_add_ingredients_today"
    assert hass.states.get(button).state == "unavailable"


async def test_unknown_todo_entity_and_nothing_to_add_are_errors(hass, freezer) -> None:
    items = _fake_todo(hass)
    await _household_with_tacos(hass, freezer)
    with pytest.raises(ServiceValidationError, match="not found"):
        await hass.services.async_call(
            DOMAIN,
            "add_ingredients_to_list",
            {"todo_entity": "todo.nope"},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError, match="No dish with ingredients"):
        await hass.services.async_call(
            DOMAIN, "add_ingredients_to_list", {"date": "2026-03-04"}, blocking=True
        )
    assert items == []


# -- Phase 5: import_from_entity (real recorder) ----------------------------------


@pytest.mark.real_recorder
async def test_import_from_entity_reads_real_recorder_history(
    recorder_mock, hass, enable_custom_integrations, entry, freezer
) -> None:
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    # Old input_text: a value per day, cleared at 02:00 (US/Pacific = 10:00 UTC).
    entity = "input_text.today_s_dinner"
    for stamp, value in (
        ("2026-02-27 20:00:00+00:00", "Soup"),
        ("2026-02-28 10:00:00+00:00", ""),
        ("2026-02-28 20:00:00+00:00", "Pasta"),
        ("2026-02-28 21:00:00+00:00", "Pasta carbonara"),
        ("2026-03-01 10:00:00+00:00", ""),
    ):
        freezer.move_to(stamp)
        hass.states.async_set(entity, value)
        await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    freezer.move_to(NOW)
    await _setup(hass, entry)
    resp = await hass.services.async_call(
        DOMAIN,
        "import_from_entity",
        {"entity_id": entity, "days": 10},
        blocking=True,
        return_response=True,
    )
    assert resp["imported"] == [
        {"date": "2026-02-27", "title": "Soup"},
        {"date": "2026-02-28", "title": "Pasta carbonara"},
    ]
    hist = await hass.services.async_call(
        DOMAIN,
        "get_history",
        {"start": "2026-02-20", "end": "2026-03-02"},
        blocking=True,
        return_response=True,
    )
    assert [(d["date"], d["menu_title"]) for d in hist["dinners"] if d["menu"]] == [
        ("2026-02-27", "Soup"),
        ("2026-02-28", "Pasta carbonara"),
    ]
    again = await hass.services.async_call(
        DOMAIN,
        "import_from_entity",
        {"entity_id": entity, "days": 10},
        blocking=True,
        return_response=True,
    )
    assert again["imported"] == [] and len(again["skipped"]) == 2


async def test_import_from_unknown_entity_is_an_error(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    with pytest.raises(HomeAssistantError, match="not found"):
        await hass.services.async_call(
            DOMAIN,
            "import_from_entity",
            {"entity_id": "input_text.nope"},
            blocking=True,
        )


# -- eating out ------------------------------------------------------------------------


async def test_kind_select_sets_eating_out_with_some_cooks(
    hass, entry, freezer
) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    kind = "select.test_household_dinner_kind_day_0"  # Ann cooks today
    state = hass.states.get(kind)
    assert state.state == "Home"
    assert "Eating out: Ann, Bob" in state.attributes["options"]
    assert state.attributes["cooking_needed"] is True

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": kind, "option": "Eating out: Ann, Bob"},
        blocking=True,
    )
    state = hass.states.get(kind)
    assert state.state == "Eating out: Ann, Bob"
    assert state.attributes["going_out"] == ["Ann", "Bob"]
    assert state.attributes["at_home"] == ["Cy"]
    assert state.attributes["cooking_needed"] is False  # Ann cooks but is out
    # The cook is unchanged: eating out is a free night.
    assert hass.states.get("select.test_household_dinner_day_0").state == "Ann"
    assert hass.states.get("sensor.test_household_dinner_today").state == "Ann"
    assert (
        hass.states.get("sensor.test_household_dinner_today").attributes[
            "cooking_needed"
        ]
        is False
    )

    await hass.services.async_call(
        "select", "select_option", {"entity_id": kind, "option": "Home"}, blocking=True
    )
    assert hass.states.get(kind).attributes["cooking_needed"] is True
    assert hass.states.get(kind).attributes["going_out"] == []


async def test_home_on_a_day_without_a_menu_changes_nothing(
    hass, entry, freezer
) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    events = async_capture_events(hass, "dinnersense_changed")
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.test_household_dinner_kind_day_1", "option": "Home"},
        blocking=True,
    )
    assert events == []


async def test_set_menu_going_out_by_name_and_validation(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "set_menu",
        {"date": "2026-03-04", "title": "Bella", "going_out": ["ann", "Cy"]},
        blocking=True,
    )
    attrs = hass.states.get("select.test_household_dinner_kind_day_2").attributes
    assert attrs["going_out"] == ["Ann", "Cy"] and attrs["kind"] == "eating_out"
    assert (
        hass.states.get("select.test_household_dinner_kind_day_2").state
        == "Eating out: Ann, Cy"
    )
    with pytest.raises(HomeAssistantError, match="Unknown cook"):
        await hass.services.async_call(
            DOMAIN,
            "set_menu",
            {"date": "2026-03-04", "going_out": ["zed"]},
            blocking=True,
        )


async def test_recipe_timers_roundtrip_through_services(hass, entry, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "create_dish",
        {
            "name": "Chicken noodles",
            "recipe": {
                "steps": ["Brown the chicken", "Boil water", "Cook noodles"],
                "timers": [
                    {"name": "Noodles", "minutes": 4, "step": 3},
                    {"name": "Chicken", "minutes": 8, "step": 1},
                ],
            },
        },
        blocking=True,
        return_response=True,
    )
    dishes = await hass.services.async_call(
        DOMAIN, "list_dishes", {}, blocking=True, return_response=True
    )
    assert dishes["dishes"][0]["recipe"]["timers"] == [
        {"name": "Noodles", "minutes": 4, "step": 3},
        {"name": "Chicken", "minutes": 8, "step": 1},
    ]
    with pytest.raises(vol.Invalid):  # step 4 doesn't exist
        await hass.services.async_call(
            DOMAIN,
            "update_dish",
            {
                "dish_id": "chicken_noodles",
                "recipe": {
                    "steps": ["Only one"],
                    "timers": [{"name": "x", "minutes": 1, "step": 4}],
                },
            },
            blocking=True,
        )
