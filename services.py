"""Service handlers."""

from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_COOK,
    ATTR_DATE,
    ATTR_END,
    ATTR_START,
    DOMAIN,
)
from .planner import Planner, PlannerError
from .runtime import DinnerSenseConfigEntry

_BASE = {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}
_DAY = {**_BASE, vol.Required(ATTR_DATE): cv.date}
SWAP_SCHEMA = vol.Schema({**_DAY, vol.Required(ATTR_COOK): cv.string})
DAY_SCHEMA = vol.Schema(_DAY)
PLAN_SCHEMA = vol.Schema(
    {**_BASE, vol.Optional(ATTR_START): cv.date, vol.Optional(ATTR_END): cv.date}
)
ENTRY_SCHEMA = vol.Schema(_BASE)


def _planner(hass: HomeAssistant, call: ServiceCall) -> Planner:
    entries: list[DinnerSenseConfigEntry] = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    if wanted := call.data.get(ATTR_CONFIG_ENTRY_ID):
        entries = [e for e in entries if e.entry_id == wanted]
    if not entries:
        raise HomeAssistantError("No loaded Dinner Sense household found")
    if len(entries) > 1:
        raise HomeAssistantError("Several households: pass config_entry_id")
    return entries[0].runtime_data.planner


def _cook_id(planner: Planner, value: str) -> str:
    cook = planner.cook(value) or planner.cook_by_name(value)
    if cook is None:
        raise HomeAssistantError(f"Unknown cook: {value}")
    return cook.id


def _dinner_dict(planner: Planner, day: date) -> dict[str, Any]:
    dinner = planner.dinners[day]
    data = dinner.to_dict()
    data.pop("history")
    data.pop("repay")
    cook = planner.cook(dinner.cook)
    data["cook_name"] = cook.name if cook else None
    return data


def async_setup_services(hass: HomeAssistant) -> None:
    def guarded(action):  # noqa: ANN001, ANN202
        async def handler(call: ServiceCall) -> ServiceResponse:
            try:
                return action(_planner(hass, call), call)
            except PlannerError as err:
                raise HomeAssistantError(str(err)) from err

        return handler

    today = lambda: dt_util.now().date()  # noqa: E731

    def swap(p: Planner, c: ServiceCall) -> None:
        p.swap(c.data[ATTR_DATE], _cook_id(p, c.data[ATTR_COOK]), today())

    def reassign(p: Planner, c: ServiceCall) -> None:
        p.reassign(c.data[ATTR_DATE], _cook_id(p, c.data[ATTR_COOK]))

    def get_plan(p: Planner, c: ServiceCall) -> ServiceResponse:
        start = c.data.get(ATTR_START, today())
        end = c.data.get(ATTR_END, date.max)
        return {
            "dinners": [_dinner_dict(p, d.date) for d in p.get_plan(start, end)]
        }

    def skip(p: Planner, c: ServiceCall) -> None:
        p.skip(c.data[ATTR_DATE])

    def complete(p: Planner, c: ServiceCall) -> None:
        p.complete(c.data[ATTR_DATE])

    def regenerate(p: Planner, c: ServiceCall) -> None:
        p.regenerate(today())

    none, only = SupportsResponse.NONE, SupportsResponse.ONLY
    for name, schema, action, response in (
        ("swap", SWAP_SCHEMA, swap, none),
        ("reassign", SWAP_SCHEMA, reassign, none),
        ("skip", DAY_SCHEMA, skip, none),
        ("complete", DAY_SCHEMA, complete, none),
        ("regenerate", ENTRY_SCHEMA, regenerate, none),
        ("get_plan", PLAN_SCHEMA, get_plan, only),
    ):
        hass.services.async_register(
            DOMAIN, name, guarded(action), schema=schema, supports_response=response
        )
