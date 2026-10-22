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
from .models import MENU_KINDS
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
HISTORY_SCHEMA = vol.Schema(
    {**_BASE, vol.Required(ATTR_START): cv.date, vol.Required(ATTR_END): cv.date}
)
MENU_SCHEMA = vol.Schema(
    {
        **_DAY,
        vol.Optional("title"): cv.string,
        vol.Optional("dish_id"): cv.string,
        vol.Optional("kind"): vol.In(MENU_KINDS),
        vol.Optional("note"): cv.string,
        vol.Optional("rating"): vol.All(vol.Coerce(int), vol.Range(min=1, max=5)),
    }
)
_RECIPE = vol.Schema(
    {
        vol.Optional("ingredients"): [
            vol.Schema(
                {vol.Required("name"): cv.string, vol.Optional("quantity"): cv.string}
            )
        ],
        vol.Optional("steps"): [cv.string],
        vol.Optional("url"): cv.url,
        vol.Optional("servings"): vol.Coerce(int),
        vol.Optional("notes"): cv.string,
    }
)
_DISH_FIELDS = {
    vol.Optional("aliases"): [cv.string],
    vol.Optional("tags"): [cv.string],
    vol.Optional("recipe"): _RECIPE,
}
CREATE_DISH_SCHEMA = vol.Schema(
    {**_BASE, vol.Required("name"): cv.string, **_DISH_FIELDS}
)
UPDATE_DISH_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Required("dish_id"): cv.string,
        vol.Optional("name"): cv.string,
        **_DISH_FIELDS,
    }
)
DELETE_DISH_SCHEMA = vol.Schema({**_BASE, vol.Required("dish_id"): cv.string})


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
    data["menu_title"] = planner.menu_title(planner.dinners[day])
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

    def get_history(p: Planner, c: ServiceCall) -> ServiceResponse:
        found = p.get_history(c.data[ATTR_START], c.data[ATTR_END], today())
        return {"dinners": [_dinner_dict(p, d.date) for d in found]}

    def set_menu(p: Planner, c: ServiceCall) -> None:
        d = c.data
        p.set_menu(
            d[ATTR_DATE],
            today(),
            title=d.get("title"),
            dish_id=d.get("dish_id"),
            kind=d.get("kind"),
            note=d.get("note"),
            rating=d.get("rating"),
        )

    def create_dish(p: Planner, c: ServiceCall) -> ServiceResponse:
        d = c.data
        dish = p.create_dish(
            d["name"], d.get("aliases"), d.get("tags"), d.get("recipe")
        )
        return dish.to_dict()

    def update_dish(p: Planner, c: ServiceCall) -> None:
        changes = {k: v for k, v in c.data.items() if k not in _BASE and k != "dish_id"}
        p.update_dish(c.data["dish_id"], **changes)

    def delete_dish(p: Planner, c: ServiceCall) -> None:
        p.delete_dish(c.data["dish_id"])

    def list_dishes(p: Planner, c: ServiceCall) -> ServiceResponse:
        dishes = sorted(p.dishes.values(), key=lambda d: d.name.casefold())
        return {"dishes": [d.to_dict() for d in dishes]}

    def skip(p: Planner, c: ServiceCall) -> None:
        p.skip(c.data[ATTR_DATE])

    def complete(p: Planner, c: ServiceCall) -> None:
        p.complete(c.data[ATTR_DATE])

    def regenerate(p: Planner, c: ServiceCall) -> None:
        p.regenerate(today())

    none, only = SupportsResponse.NONE, SupportsResponse.ONLY
    optional = SupportsResponse.OPTIONAL
    for name, schema, action, response in (
        ("swap", SWAP_SCHEMA, swap, none),
        ("reassign", SWAP_SCHEMA, reassign, none),
        ("skip", DAY_SCHEMA, skip, none),
        ("complete", DAY_SCHEMA, complete, none),
        ("regenerate", ENTRY_SCHEMA, regenerate, none),
        ("get_plan", PLAN_SCHEMA, get_plan, only),
        ("get_history", HISTORY_SCHEMA, get_history, only),
        ("set_menu", MENU_SCHEMA, set_menu, none),
        ("create_dish", CREATE_DISH_SCHEMA, create_dish, optional),
        ("update_dish", UPDATE_DISH_SCHEMA, update_dish, none),
        ("delete_dish", DELETE_DISH_SCHEMA, delete_dish, none),
        ("list_dishes", ENTRY_SCHEMA, list_dishes, only),
    ):
        hass.services.async_register(
            DOMAIN, name, guarded(action), schema=schema, supports_response=response
        )
