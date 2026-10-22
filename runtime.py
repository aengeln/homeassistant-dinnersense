"""Runtime data attached to a config entry."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .planner import Planner
from .store import HouseholdStore
from .tips import TipsManager


@dataclass
class DinnerSenseData:
    planner: Planner
    store: HouseholdStore
    tips: TipsManager


type DinnerSenseConfigEntry = ConfigEntry[DinnerSenseData]
