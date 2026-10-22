"""Dinner Sense: household dinner planning with a rotating cook."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .availability import AvailabilityTracker
from .const import CONF_COOKS, CONF_SETTINGS, DOMAIN
from .models import Cook, Settings
from .planner import Planner
from .runtime import DinnerSenseConfigEntry, DinnerSenseData
from .services import async_setup_services
from .store import HouseholdStore
from .tips import TipsManager

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.TEXT,
]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DinnerSenseConfigEntry) -> bool:
    store = HouseholdStore(hass, entry.entry_id)
    dinners, debt, dishes, windows = await store.async_load()
    cooks = [Cook.from_dict(c) for c in entry.options.get(CONF_COOKS, [])]
    settings = Settings.from_dict(entry.options.get(CONF_SETTINGS, {}))

    def fire(event: str, data: dict) -> None:
        hass.bus.async_fire(event, {"config_entry_id": entry.entry_id, **data})

    def now() -> datetime:
        return dt_util.now()

    holder: list[Planner] = []

    def save() -> None:
        store.schedule_save(holder[0])

    planner = Planner(
        cooks,
        settings,
        dinners,
        debt,
        dishes,
        windows,
        fire_event=fire,
        save=save,
        now=now,
    )
    holder.append(planner)
    tips = TipsManager(hass, entry.entry_id, planner)
    entry.runtime_data = DinnerSenseData(planner, store, tips)

    tracker = AvailabilityTracker(hass, planner)
    if cooks:
        await tracker.async_refresh()
        _roll(planner)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    hour, minute, *_ = (int(p) for p in settings.roll_time.split(":"))

    @callback
    def _nightly(_: datetime) -> None:
        if planner.cooks:
            hass.async_create_task(_nightly_roll())

    async def _nightly_roll() -> None:
        await tracker.async_refresh()  # calendars may have changed overnight
        _roll(planner, announce=True)

    @callback
    def _midnight(_: datetime) -> None:
        # "Today" moves at midnight even if the roll runs later.
        planner.notify()

    unsubs: list[CALLBACK_TYPE] = [
        async_track_time_change(hass, _nightly, hour=hour, minute=minute, second=0),
        async_track_time_change(hass, _midnight, hour=0, minute=0, second=1),
    ]
    unsubs += tracker.async_start()
    unsubs += tips.async_start()
    entry.async_create_background_task(hass, tips.async_refresh(), "dinnersense tips")
    for unsub in unsubs:
        entry.async_on_unload(unsub)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _roll(planner: Planner, *, announce: bool = False) -> None:
    today = dt_util.now().date()
    known = {c.id for c in planner.cooks}
    if any(d.cook not in known and d.date >= today for d in planner.dinners.values()):
        planner.set_cooks(planner.cooks, today)
    planner.roll(today, announce=announce)


async def _async_reload(hass: HomeAssistant, entry: DinnerSenseConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: DinnerSenseConfigEntry
) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        planner = entry.runtime_data.planner
        await entry.runtime_data.store.async_save_now(planner)
    return unloaded
