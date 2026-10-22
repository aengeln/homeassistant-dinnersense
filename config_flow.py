"""Config and options flow for Dinner Sense."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

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

CONF_COOK_NAMES = "cook_names"


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
    async def async_step_init(
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
                }
            )
            return self.async_create_entry(
                data={
                    CONF_COOKS: [c.to_dict() for c in cooks],
                    CONF_SETTINGS: new_settings.to_dict(),
                }
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
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
