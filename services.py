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
from .importer import async_read_states, titles_by_day
from .models import MENU_KINDS
from .planner import Planner, PlannerError
from .runtime import DinnerSenseConfigEntry
from .shopping import async_add_ingredients

_BASE = {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}
_DAY = {**_BASE, vol.Required(ATTR_DATE): cv.date}
SWAP_SCHEMA = vol.Schema({**_DAY, vol.Required(ATTR_COOK): cv.string})
DAY_SCHEMA = vol.Schema(_DAY)
UNAVAILABLE_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Required(ATTR_COOK): cv.string,
        vol.Required(ATTR_START): cv.date,
        vol.Required(ATTR_END): cv.date,
    }
)
CLEAR_UNAVAILABLE_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Required(ATTR_COOK): cv.string,
        vol.Optional(ATTR_START): cv.date,
        vol.Optional(ATTR_END): cv.date,
    }
)
PLAN_SCHEMA = vol.Schema(
    {**_BASE, vol.Optional(ATTR_START): cv.date, vol.Optional(ATTR_END): cv.date}
)
ENTRY_SCHEMA = vol.Schema(_BASE)
INGREDIENTS_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Optional(ATTR_DATE): cv.date,
        vol.Optional("todo_entity"): cv.entity_id,
    }
)
IMPORT_SCHEMA = vol.All(
    vol.Schema(
        {
            **_BASE,
            vol.Optional("entity_id"): cv.entity_id,
            vol.Optional("cook_entity"): cv.entity_id,
            vol.Optional("days", default=10): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=365)
            ),
            vol.Optional("overwrite", default=False): cv.boolean,
        }
    ),
    cv.has_at_least_one_key("entity_id", "cook_entity"),
)
STATS_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Optional("period"): vol.All(vol.Coerce(int), vol.Range(min=1, max=3650)),
    }
)
HISTORY_SCHEMA = vol.Schema(
    {**_BASE, vol.Required(ATTR_START): cv.date, vol.Required(ATTR_END): cv.date}
)
MENU_SCHEMA = vol.Schema(
    {
        **_DAY,
        vol.Optional("title"): cv.string,
        vol.Optional("dish_id"): cv.string,
        vol.Optional("kind"): vol.In(MENU_KINDS),
        vol.Optional("going_out"): [cv.string],
        vol.Optional("note"): cv.string,
        vol.Optional("rating"): vol.All(vol.Coerce(int), vol.Range(min=1, max=5)),
    }
)


def _minutes(value: Any) -> int | float:
    """A positive number of minutes; whole numbers stay ints (4, not 4.0)."""
    number = float(value)
    if number <= 0:
        raise vol.Invalid("minutes must be greater than 0")
    return int(number) if number.is_integer() else number


_TIMER = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("minutes"): _minutes,
        # 1-based number of the step the timer belongs to
        vol.Optional("step"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)


def _check_timer_steps(recipe: dict[str, Any]) -> dict[str, Any]:
    """A timer's step must be one of the recipe's steps."""
    steps = len(recipe.get("steps") or [])
    for timer in recipe.get("timers") or []:
        step = timer.get("step")
        if step is not None and step > steps:
            raise vol.Invalid(
                f"Timer '{timer['name']}' is for step {step}, "
                f"but the recipe has {steps} step(s)"
            )
    return recipe


RECIPE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional("ingredients"): [
                vol.Schema(
                    {
                        vol.Required("name"): cv.string,
                        vol.Optional("quantity"): cv.string,
                    }
                )
            ],
            vol.Optional("steps"): [cv.string],
            vol.Optional("url"): cv.url,
            vol.Optional("servings"): vol.Coerce(int),
            vol.Optional("notes"): cv.string,
            vol.Optional("timers"): [_TIMER],
        }
    ),
    _check_timer_steps,
)
_DISH_FIELDS = {
    vol.Optional("aliases"): [cv.string],
    vol.Optional("tags"): [cv.string],
    vol.Optional("recipe"): RECIPE_SCHEMA,
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
SET_INGREDIENT_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Required("dish_id"): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("quantity"): cv.string,
    }
)
REMOVE_INGREDIENT_SCHEMA = vol.Schema(
    {**_BASE, vol.Required("dish_id"): cv.string, vol.Required("name"): cv.string}
)


def _entry(hass: HomeAssistant, call: ServiceCall) -> DinnerSenseConfigEntry:
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
    return entries[0]


def _planner(hass: HomeAssistant, call: ServiceCall) -> Planner:
    return _entry(hass, call).runtime_data.planner


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
        return {"dinners": [_dinner_dict(p, d.date) for d in p.get_plan(start, end)]}

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
            going_out=(
                [_cook_id(p, c) for c in d["going_out"]] if "going_out" in d else None
            ),
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

    def set_ingredient(p: Planner, c: ServiceCall) -> ServiceResponse:
        return p.set_ingredient(
            c.data["dish_id"], c.data["name"], c.data.get("quantity")
        )

    def remove_ingredient(p: Planner, c: ServiceCall) -> None:
        p.remove_ingredient(c.data["dish_id"], c.data["name"])

    def delete_dish(p: Planner, c: ServiceCall) -> None:
        p.delete_dish(c.data["dish_id"])

    def list_dishes(p: Planner, c: ServiceCall) -> ServiceResponse:
        dishes = sorted(p.dishes.values(), key=lambda d: d.name.casefold())
        return {"dishes": [d.to_dict() for d in dishes]}

    def set_unavailable(p: Planner, c: ServiceCall) -> None:
        p.add_unavailable(
            _cook_id(p, c.data[ATTR_COOK]),
            c.data[ATTR_START],
            c.data[ATTR_END],
            today(),
        )

    def clear_unavailable(p: Planner, c: ServiceCall) -> None:
        p.clear_unavailable(
            _cook_id(p, c.data[ATTR_COOK]),
            c.data.get(ATTR_START),
            c.data.get(ATTR_END),
            today(),
        )

    def skip(p: Planner, c: ServiceCall) -> None:
        p.skip(c.data[ATTR_DATE])

    def complete(p: Planner, c: ServiceCall) -> None:
        p.complete(c.data[ATTR_DATE])

    def regenerate(p: Planner, c: ServiceCall) -> None:
        p.regenerate(today())

    async def add_ingredients_to_list(call: ServiceCall) -> ServiceResponse:
        planner = _planner(hass, call)
        day = call.data.get(ATTR_DATE, today())
        return await async_add_ingredients(
            hass,
            planner.dinner_on(day),
            planner.dishes,
            call.data.get("todo_entity") or planner.settings.todo_entity,
            day,
        )

    hass.services.async_register(
        DOMAIN,
        "add_ingredients_to_list",
        add_ingredients_to_list,
        schema=INGREDIENTS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def import_from_entity(call: ServiceCall) -> ServiceResponse:
        planner = _planner(hass, call)
        days = call.data["days"]
        titles: dict[date, str] = {}
        cooks: dict[date, str] = {}
        unknown: list[dict[str, Any]] = []
        for key in ("entity_id", "cook_entity"):
            entity_id = call.data.get(key)
            if entity_id and hass.states.get(entity_id) is None:
                raise HomeAssistantError(f"{entity_id} was not found")
        if entity_id := call.data.get("entity_id"):
            titles = titles_by_day(
                await async_read_states(hass, entity_id, days), today(), days
            )
        if entity_id := call.data.get("cook_entity"):
            names = titles_by_day(
                await async_read_states(hass, entity_id, days), today(), days
            )
            for day, name in names.items():
                cook = planner.cook(name) or planner.cook_by_name(name)
                if cook is None:
                    unknown.append(
                        {
                            "date": day.isoformat(),
                            "title": None,
                            "cook": name,
                            "reason": "unknown_cook",
                        }
                    )
                else:
                    cooks[day] = cook.id
        imported, skipped = planner.import_menus(
            titles, today(), call.data["overwrite"], cooks
        )
        return {"imported": imported, "skipped": skipped + unknown}

    hass.services.async_register(
        DOMAIN,
        "import_from_entity",
        import_from_entity,
        schema=IMPORT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def get_stats(call: ServiceCall) -> ServiceResponse:
        tips = _entry(hass, call).runtime_data.tips
        return tips.stats(call.data.get("period"))

    async def generate_tips(call: ServiceCall) -> ServiceResponse:
        tips = _entry(hass, call).runtime_data.tips
        generated = await tips.async_refresh()
        return {"tips": [t["text"] for t in generated], "source": tips.source}

    hass.services.async_register(
        DOMAIN,
        "get_stats",
        get_stats,
        schema=STATS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "generate_tips",
        generate_tips,
        schema=ENTRY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    none, only = SupportsResponse.NONE, SupportsResponse.ONLY
    optional = SupportsResponse.OPTIONAL
    for name, schema, action, response in (
        ("swap", SWAP_SCHEMA, swap, none),
        ("reassign", SWAP_SCHEMA, reassign, none),
        ("skip", DAY_SCHEMA, skip, none),
        ("complete", DAY_SCHEMA, complete, none),
        ("regenerate", ENTRY_SCHEMA, regenerate, none),
        ("get_plan", PLAN_SCHEMA, get_plan, only),
        ("set_unavailable", UNAVAILABLE_SCHEMA, set_unavailable, none),
        ("clear_unavailable", CLEAR_UNAVAILABLE_SCHEMA, clear_unavailable, none),
        ("get_history", HISTORY_SCHEMA, get_history, only),
        ("set_menu", MENU_SCHEMA, set_menu, none),
        ("create_dish", CREATE_DISH_SCHEMA, create_dish, optional),
        ("update_dish", UPDATE_DISH_SCHEMA, update_dish, none),
        ("delete_dish", DELETE_DISH_SCHEMA, delete_dish, none),
        ("set_ingredient", SET_INGREDIENT_SCHEMA, set_ingredient, optional),
        ("remove_ingredient", REMOVE_INGREDIENT_SCHEMA, remove_ingredient, none),
        ("list_dishes", ENTRY_SCHEMA, list_dishes, only),
    ):
        hass.services.async_register(
            DOMAIN, name, guarded(action), schema=schema, supports_response=response
        )
