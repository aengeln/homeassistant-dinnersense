"""The options of the per-day "kind" select. No Home Assistant imports.

One select covers both the kind of dinner and, for eating out, who goes:
"Home", "Eating out: everyone", "Eating out: Ann, Bob", "Leftovers", "No dinner".
Every combination is generated for up to MAX_SUBSET_COOKS cooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from .models import Cook, MenuEntry

MAX_SUBSET_COOKS = 4

LABELS = {
    "en": {
        "home": "Home",
        "everyone": "Eating out: everyone",
        "out": "Eating out: {names}",
        "leftovers": "Leftovers",
        "skip": "No dinner",
    },
    "sv": {
        "home": "Hemma",
        "everyone": "Äta ute: alla",
        "out": "Äta ute: {names}",
        "leftovers": "Rester",
        "skip": "Ingen middag",
    },
}


@dataclass(frozen=True)
class KindOption:
    label: str
    kind: str
    going_out: tuple[str, ...] = ()  # cook ids; empty means everyone


def kind_options(cooks: list[Cook], language: str = "en") -> list[KindOption]:
    t = LABELS.get(language.split("-")[0].casefold(), LABELS["en"])
    options = [
        KindOption(t["home"], "home"),
        KindOption(t["everyone"], "eating_out"),
    ]
    if 2 <= len(cooks) <= MAX_SUBSET_COOKS:
        for size in range(1, len(cooks)):
            for group in combinations(cooks, size):
                options.append(
                    KindOption(
                        t["out"].format(names=", ".join(c.name for c in group)),
                        "eating_out",
                        tuple(c.id for c in group),
                    )
                )
    options += [KindOption(t["leftovers"], "leftovers"), KindOption(t["skip"], "skip")]
    return options


def option_for_menu(
    menu: MenuEntry | None, cooks: list[Cook], options: list[KindOption]
) -> KindOption:
    """The option matching a menu. A day with no menu is 'Home'."""
    kind = menu.kind if menu else "home"
    ids = [c.id for c in cooks]
    chosen = tuple(c for c in ids if menu and c in menu.going_out)
    everyone = not chosen or len(chosen) == len(ids)
    for option in options:
        if option.kind != kind:
            continue
        if kind != "eating_out":
            return option
        if everyone and not option.going_out:
            return option
        if not everyone and option.going_out == chosen:
            return option
    return options[0]
