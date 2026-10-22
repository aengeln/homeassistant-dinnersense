"""Runtime data attached to a config entry."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .planner import Planner
from .store import HouseholdStore


@dataclass
class DinnerSenseData:
    planner: Planner
    store: HouseholdStore


type DinnerSenseConfigEntry = ConfigEntry[DinnerSenseData]
