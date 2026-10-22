"""Config and options flow for Dinner Sense."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .availability import (
    DEFAULT_KEYWORD,
    SOURCE_BINARY,
    SOURCE_CALENDAR,
    SOURCE_RANGE,
    SOURCE_UNTIL,
)
from .const import (
    CONF_COOKS,
    CONF_DINNER_DAYS,
    CONF_HORIZON_DAYS,
    CONF_HOUSEHOLD_NAME,
    CONF_MENU_FOLLOWS_COOK,
    CONF_ROLL_TIME,
    CONF_SETTINGS,
    CONF_UNCONFIRMED_PAST,
    DOMAIN,
    WEEKDAYS,
)
from .models import Cook, Settings, build_cooks
from .planner import PlannerError
from .recipes import (
    RecipeTextError,
    build_recipe,
    format_ingredients,
    format_steps,
    format_timers,
    split_words,
)

CONF_COOK_NAMES = "cook_names"
CONF_AI_TASK = "ai_task_entity"
CONF_TODO = "todo_entity"
CONF_TONIGHT_FIXED = "tonight_fixed_from"
CONF_COOK = "cook"
CONF_DISH = "dish"
CONF_DISH_NAME = "name"
CONF_ALIASES = "aliases"
CONF_TAGS = "tags"
CONF_SERVINGS = "servings"
CONF_INGREDIENTS = "ingredients"
CONF_STEPS = "steps"
CONF_TIMERS = "timers"
CONF_URL = "url"
CONF_NOTES = "notes"
CONF_DELETE = "delete"
NEW_DISH = "__new__"
NEW_DISH_LABEL = {"en": "➕ New dish", "sv": "➕ Ny rätt"}
CONF_PERSON = "person_entity"
CONF_COLOR = "color"
CONF_AWAY_CALENDAR = "away_calendar"
CONF_AWAY_KEYWORD = "away_keyword"
CONF_AWAY_UNTIL = "away_until"
CONF_AWAY_FROM = "away_from"
CONF_AWAY_TO = "away_to"
CONF_AWAY_SENSOR = "away_sensor"


def _sources_from_input(data: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if data.get(CONF_AWAY_CALENDAR):
        sources.append(
            {
                "type": SOURCE_CALENDAR,
                "entity_id": data[CONF_AWAY_CALENDAR],
                "keyword": (data.get(CONF_AWAY_KEYWORD) or DEFAULT_KEYWORD).strip(),
            }
        )
    if data.get(CONF_AWAY_UNTIL):
        sources.append({"type": SOURCE_UNTIL, "entity_id": data[CONF_AWAY_UNTIL]})
    if data.get(CONF_AWAY_FROM) and data.get(CONF_AWAY_TO):
        sources.append(
            {
                "type": SOURCE_RANGE,
                "from_entity": data[CONF_AWAY_FROM],
                "to_entity": data[CONF_AWAY_TO],
            }
        )
    if data.get(CONF_AWAY_SENSOR):
        sources.append({"type": SOURCE_BINARY, "entity_id": data[CONF_AWAY_SENSOR]})
    return sources


def _split(names: str) -> list[str]:
    return [n.strip() for n in names.split(",") if n.strip()]


class DinnerSenseConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DinnerSenseOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOUSEHOLD_NAME].lower())
            self._abort_if_unique_id_configured()
            cooks = build_cooks(_split(user_input.get(CONF_COOK_NAMES, "")))
            return self.async_create_entry(
                title=user_input[CONF_HOUSEHOLD_NAME],
                data={CONF_HOUSEHOLD_NAME: user_input[CONF_HOUSEHOLD_NAME]},
                options={
                    CONF_COOKS: [c.to_dict() for c in cooks],
                    CONF_SETTINGS: Settings().to_dict(),
                },
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOUSEHOLD_NAME): str,
                    vol.Optional(CONF_COOK_NAMES, default=""): str,
                }
            ),
            errors=errors,
        )


class DinnerSenseOptionsFlow(OptionsFlow):
    def __init__(self) -> None:
        self._cook_id: str | None = None
        self._dish_id: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "cooks", "dishes"]
        )

    # -- dishes ------------------------------------------------------------------

    def _planner(self):  # noqa: ANN202
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return None
        return self.config_entry.runtime_data.planner

    async def async_step_dishes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a dish to edit, or add a new one."""
        planner = self._planner()
        if planner is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            choice = user_input[CONF_DISH]
            self._dish_id = None if choice == NEW_DISH else choice
            return await self.async_step_dish()
        language = self.hass.config.language.split("-")[0].casefold()
        dishes = sorted(planner.dishes.values(), key=lambda d: d.name.casefold())
        options = [
            selector.SelectOptionDict(
                value=NEW_DISH, label=NEW_DISH_LABEL.get(language, NEW_DISH_LABEL["en"])
            ),
            *(selector.SelectOptionDict(value=d.id, label=d.name) for d in dishes),
        ]
        return self.async_show_form(
            step_id="dishes",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DISH, default=NEW_DISH): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_dish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add or edit one dish. Ingredients, steps and timers are plain text lines."""
        planner = self._planner()
        if planner is None:
            return self.async_abort(reason="not_loaded")
        dish = planner.dishes.get(self._dish_id) if self._dish_id else None
        errors: dict[str, str] = {}
        placeholders = {"error": "", "dish": dish.name if dish else ""}

        if user_input is not None:
            if dish and user_input.get(CONF_DELETE):
                planner.delete_dish(dish.id)
                return self.async_create_entry(data=dict(self.config_entry.options))
            try:
                recipe = build_recipe(
                    servings=user_input.get(CONF_SERVINGS),
                    ingredients=user_input.get(CONF_INGREDIENTS),
                    steps=user_input.get(CONF_STEPS),
                    timers=user_input.get(CONF_TIMERS),
                    url=user_input.get(CONF_URL),
                    notes=user_input.get(CONF_NOTES),
                )
            except RecipeTextError as err:
                errors["base"] = "invalid_recipe"
                placeholders["error"] = str(err)
            else:
                aliases = split_words(user_input.get(CONF_ALIASES))
                tags = split_words(user_input.get(CONF_TAGS))
                name = user_input[CONF_DISH_NAME].strip()
                try:
                    if dish:
                        planner.update_dish(
                            dish.id,
                            name=name,
                            aliases=aliases,
                            tags=tags,
                            recipe=recipe,
                        )
                    else:
                        planner.create_dish(name, aliases, tags, recipe or None)
                except PlannerError as err:
                    errors["base"] = "dish_error"
                    placeholders["error"] = str(err)
                else:
                    return self.async_create_entry(data=dict(self.config_entry.options))

        # What to show: what the user just typed (after an error), else the dish.
        recipe_now = (dish.recipe or {}) if dish else {}
        shown: dict[str, Any] = user_input or {
            CONF_DISH_NAME: dish.name if dish else "",
            CONF_ALIASES: ", ".join(dish.aliases) if dish else "",
            CONF_TAGS: ", ".join(dish.tags) if dish else "",
            CONF_SERVINGS: recipe_now.get("servings"),
            CONF_INGREDIENTS: format_ingredients(recipe_now.get("ingredients")),
            CONF_STEPS: format_steps(recipe_now.get("steps")),
            CONF_TIMERS: format_timers(recipe_now.get("timers")),
            CONF_URL: recipe_now.get("url", ""),
            CONF_NOTES: recipe_now.get("notes", ""),
        }

        def field(key: str, *, required: bool = False) -> vol.Marker:
            marker = vol.Required if required else vol.Optional
            value = shown.get(key)
            return marker(
                key,
                description={"suggested_value": value}
                if value not in (None, "")
                else None,
            )

        multiline = selector.TextSelector(selector.TextSelectorConfig(multiline=True))
        fields: dict[Any, Any] = {
            field(CONF_DISH_NAME, required=True): str,
            field(CONF_ALIASES): str,
            field(CONF_TAGS): str,
            field(CONF_SERVINGS): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=100, mode=selector.NumberSelectorMode.BOX
                )
            ),
            field(CONF_INGREDIENTS): multiline,
            field(CONF_STEPS): multiline,
            field(CONF_TIMERS): multiline,
            field(CONF_URL): str,
            field(CONF_NOTES): multiline,
        }
        if dish:
            fields[vol.Optional(CONF_DELETE, default=False)] = bool
        return self.async_show_form(
            step_id="dish",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_cooks(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a cook to edit person, colour and availability sources."""
        cooks = [
            Cook.from_dict(c) for c in self.config_entry.options.get(CONF_COOKS, [])
        ]
        if not cooks:
            return self.async_abort(reason="no_cooks")
        if user_input is not None:
            self._cook_id = user_input[CONF_COOK]
            return await self.async_step_cook()
        return self.async_show_form(
            step_id="cooks",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COOK): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=c.id, label=c.name)
                                for c in cooks
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_cook(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self.config_entry.options
        cooks = [Cook.from_dict(c) for c in options.get(CONF_COOKS, [])]
        cook = next(c for c in cooks if c.id == self._cook_id)
        sources = {s["type"]: s for s in cook.availability}

        if user_input is not None:
            cook.person_entity = user_input.get(CONF_PERSON) or None
            cook.color = user_input.get(CONF_COLOR) or None
            cook.availability = _sources_from_input(user_input)
            return self.async_create_entry(
                data={
                    CONF_COOKS: [c.to_dict() for c in cooks],
                    CONF_SETTINGS: options.get(CONF_SETTINGS, {}),
                }
            )

        def opt(key: str, value: Any) -> vol.Optional:
            return vol.Optional(
                key, description={"suggested_value": value} if value else None
            )

        def entity(domain: str) -> selector.EntitySelector:
            return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))

        cal = sources.get(SOURCE_CALENDAR, {})
        rng = sources.get(SOURCE_RANGE, {})
        schema = vol.Schema(
            {
                opt(CONF_PERSON, cook.person_entity): entity("person"),
                opt(CONF_COLOR, cook.color): selector.TextSelector(),
                opt(CONF_AWAY_CALENDAR, cal.get("entity_id")): entity("calendar"),
                opt(CONF_AWAY_KEYWORD, cal.get("keyword")): selector.TextSelector(),
                opt(CONF_AWAY_UNTIL, sources.get(SOURCE_UNTIL, {}).get("entity_id")): (
                    entity("input_datetime")
                ),
                opt(CONF_AWAY_FROM, rng.get("from_entity")): entity("input_datetime"),
                opt(CONF_AWAY_TO, rng.get("to_entity")): entity("input_datetime"),
                opt(
                    CONF_AWAY_SENSOR, sources.get(SOURCE_BINARY, {}).get("entity_id")
                ): entity("binary_sensor"),
            }
        )
        return self.async_show_form(
            step_id="cook",
            data_schema=schema,
            description_placeholders={"cook": cook.name},
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self.config_entry.options
        existing = [Cook.from_dict(c) for c in options.get(CONF_COOKS, [])]
        settings = Settings.from_dict(options.get(CONF_SETTINGS, {}))

        if user_input is not None:
            cooks = build_cooks(_split(user_input[CONF_COOK_NAMES]), existing)
            new_settings = Settings.from_dict(
                {
                    **settings.to_dict(),
                    CONF_HORIZON_DAYS: int(user_input[CONF_HORIZON_DAYS]),
                    CONF_ROLL_TIME: user_input[CONF_ROLL_TIME],
                    CONF_DINNER_DAYS: [
                        WEEKDAYS.index(d) for d in user_input[CONF_DINNER_DAYS]
                    ],
                    CONF_MENU_FOLLOWS_COOK: user_input[CONF_MENU_FOLLOWS_COOK],
                    CONF_UNCONFIRMED_PAST: user_input[CONF_UNCONFIRMED_PAST],
                    CONF_AI_TASK: user_input.get(CONF_AI_TASK) or None,
                    CONF_TODO: user_input.get(CONF_TODO) or None,
                    CONF_TONIGHT_FIXED: user_input.get(CONF_TONIGHT_FIXED) or None,
                }
            )
            return self.async_create_entry(
                data={
                    CONF_COOKS: [c.to_dict() for c in cooks],
                    CONF_SETTINGS: new_settings.to_dict(),
                }
            )

        fixed_default = (
            {"suggested_value": settings.tonight_fixed_from}
            if settings.tonight_fixed_from
            else None
        )
        todo_default = (
            {"suggested_value": settings.todo_entity} if settings.todo_entity else None
        )
        ai_default = (
            {"suggested_value": settings.ai_task_entity}
            if settings.ai_task_entity
            else None
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_COOK_NAMES, default=", ".join(c.name for c in existing)
                ): str,
                vol.Required(CONF_HORIZON_DAYS, default=settings.horizon_days): vol.All(
                    selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=3, max=14, mode=selector.NumberSelectorMode.BOX
                        )
                    ),
                    vol.Coerce(int),
                ),
                vol.Required(CONF_ROLL_TIME, default=settings.roll_time): (
                    selector.TimeSelector()
                ),
                vol.Required(
                    CONF_DINNER_DAYS,
                    default=[WEEKDAYS[d] for d in settings.dinner_days],
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=WEEKDAYS,
                        multiple=True,
                        translation_key="weekday",
                    )
                ),
                vol.Required(
                    CONF_MENU_FOLLOWS_COOK, default=settings.menu_follows_cook
                ): bool,
                vol.Required(
                    CONF_UNCONFIRMED_PAST, default=settings.unconfirmed_past
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["done", "missed"],
                        translation_key="unconfirmed_past",
                    )
                ),
                vol.Optional(CONF_TONIGHT_FIXED, description=fixed_default): (
                    selector.TimeSelector()
                ),
                vol.Optional(CONF_TODO, description=todo_default): (
                    selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="todo")
                    )
                ),
                vol.Optional(CONF_AI_TASK, description=ai_default): (
                    selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="ai_task")
                    )
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)
