"""Dinner Sense: household dinner planning with a rotating cook."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import CONF_COOKS, CONF_SETTINGS
from .models import Cook, Settings
from .planner import Planner
from .runtime import DinnerSenseConfigEntry, DinnerSenseData
from .services import async_setup_services
from .store import HouseholdStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.SELECT]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DinnerSenseConfigEntry) -> bool:
    store = HouseholdStore(hass, entry.entry_id)
    dinners, debt = await store.async_load()
    cooks = [Cook.from_dict(c) for c in entry.options.get(CONF_COOKS, [])]
    settings = Settings.from_dict(entry.options.get(CONF_SETTINGS, {}))

    def fire(event: str, data: dict) -> None:
        hass.bus.async_fire(event, {"config_entry_id": entry.entry_id, **data})

    def now() -> datetime:
        return dt_util.now()

    holder: list[Planner] = []

    def save() -> None:
        p = holder[0]
        store.schedule_save(p.dinners, p.debt, p.cooks)

    planner = Planner(
        cooks, settings, dinners, debt, fire_event=fire, save=save, now=now
    )
    holder.append(planner)
    entry.runtime_data = DinnerSenseData(planner, store)

    if cooks:
        _roll(planner)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    hour, minute, *_ = (int(p) for p in settings.roll_time.split(":"))

    @callback
    def _nightly(_: datetime) -> None:
        if planner.cooks:
            _roll(planner)

    @callback
    def _midnight(_: datetime) -> None:
        # "Today" moves at midnight even if the roll runs later.
        planner.notify()

    unsubs: list[CALLBACK_TYPE] = [
        async_track_time_change(hass, _nightly, hour=hour, minute=minute, second=0),
        async_track_time_change(hass, _midnight, hour=0, minute=0, second=1),
    ]
    for unsub in unsubs:
        entry.async_on_unload(unsub)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _roll(planner: Planner) -> None:
    today = dt_util.now().date()
    known = {c.id for c in planner.cooks}
    if any(d.cook not in known and d.date >= today for d in planner.dinners.values()):
        planner.set_cooks(planner.cooks, today)
    planner.roll(today)


async def _async_reload(hass: HomeAssistant, entry: DinnerSenseConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: DinnerSenseConfigEntry
) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        planner = entry.runtime_data.planner
        await entry.runtime_data.store.async_save_now(
            planner.dinners, planner.debt, planner.cooks
        )
    return unloaded
