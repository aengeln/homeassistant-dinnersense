"""Tips: rule-based fallback, plus LLM phrasing through HA's AI Task.

The LLM only ever sees a small facts payload computed from the stats and may
only reuse numbers that occur in it; anything else is rejected and the
rule-based tips are used instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .planner import EVENT_CHANGED, EVENT_ROLLED, EVENT_SWAPPED, Planner
from .stats import compute_stats

_LOGGER = logging.getLogger(__name__)

MAX_TIPS = 3
MAX_TIP_LENGTH = 160
LAPSE_MIN_DAYS = 14
LAPSE_MIN_COUNT_90 = 3
REPEAT_MIN = 3
STREAK_MIN = 3
FACT_ITEMS = 5
AI_TIMEOUT = 90
DEBOUNCE_SECONDS = 30

TEMPLATES = {
    "en": {
        "lapse": "No {name} for {n} days",
        "repeat": "{name} {n}× this week",
        "streak": "{name} {n} days in a row",
        "today": "Tonight has no menu yet",
        "tomorrow": "Tomorrow has no menu yet",
        "weekday": "{name} has no menu yet",
        "weekdays": [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ],
    },
    "sv": {
        "lapse": "Ingen {name} på {n} dagar",
        "repeat": "{name} {n}× den här veckan",
        "streak": "{name} {n} dagar i rad",
        "today": "Ikväll saknas meny",
        "tomorrow": "Imorgon saknas meny",
        "weekday": "{name} saknar meny",
        "weekdays": [
            "Måndag",
            "Tisdag",
            "Onsdag",
            "Torsdag",
            "Fredag",
            "Lördag",
            "Söndag",
        ],
    },
}
LANGUAGE_NAMES = {"en": "English", "sv": "Swedish"}


def pick_language(language: str | None) -> str:
    code = (language or "en").split("-")[0].casefold()
    return code if code in TEMPLATES else "en"


# -- rule-based tips ------------------------------------------------------------


def _items(stats: dict[str, Any]) -> list[dict[str, Any]]:
    """Dishes and tags as one list of {name, days_since, count_*}, deduped by name."""
    seen: set[str] = set()
    out = []
    for row in stats["dishes"]:
        if row["days_since"] is None:
            continue
        seen.add(row["name"].casefold())
        out.append(row)
    for row in stats["tags"]:
        # A tag on a single dish just repeats that dish's own row.
        if row["tag"].casefold() in seen or row["dishes"] < 2:
            continue
        out.append({**row, "name": row["tag"]})
    return out


def rule_tips(stats: dict[str, Any], language: str = "en") -> list[dict[str, str]]:
    """Up to three short tips, most actionable first. Deterministic."""
    t = TEMPLATES[pick_language(language)]
    tips: list[dict[str, str]] = []

    if stats["unplanned"]:
        nxt = stats["unplanned"][0]
        if nxt["in_days"] == 0:
            text = t["today"]
        elif nxt["in_days"] == 1:
            text = t["tomorrow"]
        else:
            text = t["weekday"].format(name=t["weekdays"][nxt["weekday"]])
        tips.append({"kind": "unplanned", "text": text})

    items = _items(stats)
    repeats = sorted(
        (i for i in items if i["count_7"] >= REPEAT_MIN),
        key=lambda i: (-i["count_7"], i["name"]),
    )
    used: set[str] = set()
    for item in repeats[:1]:
        used.add(item["name"].casefold())
        tips.append(
            {
                "kind": "repeat",
                "text": t["repeat"].format(name=item["name"], n=item["count_7"]),
            }
        )

    for streak in stats["streaks"]:
        if streak["length"] >= STREAK_MIN and streak["tag"].casefold() not in used:
            tips.append(
                {
                    "kind": "streak",
                    "text": t["streak"].format(name=streak["tag"], n=streak["length"]),
                }
            )
            break

    lapses = sorted(
        (
            i
            for i in items
            if i["days_since"] >= LAPSE_MIN_DAYS and i["count_90"] >= LAPSE_MIN_COUNT_90
        ),
        key=lambda i: (-i["days_since"], i["name"]),
    )
    for item in lapses[:2]:
        tips.append(
            {
                "kind": "lapse",
                "text": t["lapse"].format(name=item["name"], n=item["days_since"]),
            }
        )
    return tips[:MAX_TIPS]


# -- LLM phrasing -----------------------------------------------------------------


def facts_for_llm(stats: dict[str, Any], language: str = "en") -> dict[str, Any]:
    """A small payload of facts: the only numbers the LLM may use."""
    t = TEMPLATES[pick_language(language)]
    items = _items(stats)
    return {
        "lapses": [
            {
                "name": i["name"],
                "days_since_last": i["days_since"],
                "times_last_90_days": i["count_90"],
            }
            for i in sorted(
                (
                    i
                    for i in items
                    if i["days_since"] >= LAPSE_MIN_DAYS and i["count_90"] >= 2
                ),
                key=lambda i: (-i["days_since"], i["name"]),
            )[:FACT_ITEMS]
        ],
        "repeats": [
            {"name": i["name"], "times_last_7_days": i["count_7"]}
            for i in sorted(
                (i for i in items if i["count_7"] >= 2),
                key=lambda i: (-i["count_7"], i["name"]),
            )[:FACT_ITEMS]
        ],
        "streaks": [
            {"name": s["tag"], "days_in_a_row": s["length"]}
            for s in stats["streaks"][:FACT_ITEMS]
        ],
        "days_without_menu": [
            {"weekday": t["weekdays"][u["weekday"]], "days_from_today": u["in_days"]}
            for u in stats["unplanned"][:FACT_ITEMS]
        ],
    }


def numbers_in(value: Any) -> set[str]:
    """Every number appearing as a value in a nested payload, as strings."""
    found: set[str] = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, int | float):
        found.add(str(int(value)))
    elif isinstance(value, dict):
        for v in value.values():
            found |= numbers_in(v)
    elif isinstance(value, list):
        for v in value:
            found |= numbers_in(v)
    return found


def has_facts(facts: dict[str, Any]) -> bool:
    return any(facts[k] for k in facts)


def build_instructions(facts: dict[str, Any], language: str) -> str:
    return (
        "You write short, friendly tips for a household's dinner planning. "
        f"Write in {LANGUAGE_NAMES[pick_language(language)]}. "
        "Use ONLY the facts below. Never invent dishes, dates or numbers; any number "
        "you write must appear in the facts. Give 1 to 3 tips, each under 100 "
        "characters, no emojis, no markdown.\n\nFacts (JSON):\n"
        + json.dumps(facts, ensure_ascii=False)
    )


def clean_llm_tips(data: Any, facts: dict[str, Any]) -> list[str]:
    """Pick usable tips out of an AI Task answer; drop any with unknown numbers."""
    if isinstance(data, dict):
        data = data.get("tips")
    if isinstance(data, str):
        data = [ln for ln in data.splitlines()]
    if not isinstance(data, list):
        return []
    allowed = numbers_in(facts)
    good: list[str] = []
    for raw in data:
        if not isinstance(raw, str):
            continue
        text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", raw).strip()
        if not text or len(text) > MAX_TIP_LENGTH:
            continue
        if any(n not in allowed for n in re.findall(r"\d+", text)):
            _LOGGER.debug("Rejected tip with unknown number: %s", text)
            continue
        good.append(text)
    return good[:MAX_TIPS]


# -- manager ------------------------------------------------------------------------


class TipsManager:
    """Holds the current tips and refreshes them (debounced) when plans change."""

    def __init__(self, hass: HomeAssistant, entry_id: str, planner: Planner) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.planner = planner
        self.tips: list[dict[str, str]] = []
        self.generated_at: str | None = None
        self.source: str = "rules"
        self._listeners: list[CALLBACK_TYPE] = []
        self._cancel_debounce: CALLBACK_TYPE | None = None
        self._lock = asyncio.Lock()

    def add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_start(self) -> list[CALLBACK_TYPE]:
        @callback
        def _on_event(event: Event) -> None:
            if event.data.get("config_entry_id") == self.entry_id:
                self.schedule_refresh()

        unsubs = [
            self.hass.bus.async_listen(name, _on_event)
            for name in (EVENT_CHANGED, EVENT_SWAPPED, EVENT_ROLLED)
        ]
        unsubs.append(self._cancel)
        return unsubs

    @callback
    def _cancel(self) -> None:
        if self._cancel_debounce:
            self._cancel_debounce()
            self._cancel_debounce = None

    @callback
    def schedule_refresh(self) -> None:
        """Debounced: many quick menu edits cause one refresh."""
        self._cancel()

        @callback
        def _fire(_: Any) -> None:
            self._cancel_debounce = None
            self.hass.async_create_background_task(
                self.async_refresh(), f"{DOMAIN} tips refresh"
            )

        self._cancel_debounce = async_call_later(self.hass, DEBOUNCE_SECONDS, _fire)

    def stats(self, period: int | None = None) -> dict[str, Any]:
        p = self.planner
        return compute_stats(
            p.dinners,
            p.dishes,
            p.cooks,
            dt_util.now().date(),
            p.settings.horizon_days,
            period,
            p.settings.dinner_days,
        )

    async def async_refresh(self) -> list[dict[str, str]]:
        async with self._lock:
            language = self.hass.config.language
            stats = self.stats()
            tips: list[dict[str, str]] | None = None
            source = "rules"
            entity_id = self.planner.settings.ai_task_entity
            if entity_id:
                tips = await self._llm_tips(entity_id, stats, language)
                if tips:
                    source = "llm"
            if not tips:
                tips = rule_tips(stats, language)
            self.tips, self.source = tips, source
            self.generated_at = dt_util.utcnow().isoformat()
            for listener in list(self._listeners):
                listener()
            return tips

    async def _llm_tips(
        self, entity_id: str, stats: dict[str, Any], language: str
    ) -> list[dict[str, str]] | None:
        facts = facts_for_llm(stats, language)
        if not has_facts(facts):
            return None
        try:
            async with asyncio.timeout(AI_TIMEOUT):
                result = await self.hass.services.async_call(
                    "ai_task",
                    "generate_data",
                    {
                        "task_name": "Dinner Sense tips",
                        "entity_id": entity_id,
                        "instructions": build_instructions(facts, language),
                        "structure": {
                            "tips": {
                                "selector": {"text": {"multiple": True}},
                                "description": "One to three short dinner tips",
                                "required": True,
                            }
                        },
                    },
                    blocking=True,
                    return_response=True,
                )
        except (HomeAssistantError, TimeoutError, ValueError, TypeError) as err:
            _LOGGER.warning("AI Task failed, using rule-based tips: %s", err)
            return None
        texts = clean_llm_tips((result or {}).get("data"), facts)
        return [{"kind": "llm", "text": t} for t in texts] or None
