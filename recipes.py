"""Turn a dish's recipe into editable text and back. No Home Assistant imports.

The dish editor shows ingredients, steps and timers as plain multi-line text:

    ingredients     steps                    timers
    Tortillas x 8   Brown the beef           Beef, 8, 1
    Minced beef     Warm the tortillas       Tortillas, 1.5, 2
"""

from __future__ import annotations

import re
from typing import Any

_QUANTITY_SUFFIX = re.compile(r"\s+x\s+\S.*$", re.IGNORECASE)
_INGREDIENT = re.compile(r"^(?P<name>.+?)\s+x\s+(?P<quantity>\S.*)$", re.IGNORECASE)


def ingredient_name_key(text: str) -> str:
    """Compare list items and ingredients by name, ignoring case and 'x 8'."""
    return _QUANTITY_SUFFIX.sub("", text.strip()).casefold()


def format_item(name: str, quantity: str | None) -> str:
    name, quantity = name.strip(), (quantity or "").strip()
    return f"{name} x {quantity}" if quantity else name


class RecipeTextError(ValueError):
    """A line of recipe text could not be understood. The message names the line."""


def _lines(text: str | None) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def parse_ingredients(text: str | None) -> list[dict[str, str]]:
    """'Tortillas x 8' -> {name: Tortillas, quantity: 8}; 'Lime' -> {name: Lime}."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in _lines(text):
        match = _INGREDIENT.match(line)
        name = match["name"].strip() if match else line
        item = {"name": name}
        if match:
            item["quantity"] = match["quantity"].strip()
        key = ingredient_name_key(name)
        if key in seen:
            raise RecipeTextError(f"Ingredient listed twice: {name}")
        seen.add(key)
        out.append(item)
    return out


def format_ingredients(ingredients: list[dict[str, Any]] | None) -> str:
    return "\n".join(
        format_item(str(i.get("name", "")), i.get("quantity"))
        for i in ingredients or []
    )


def parse_steps(text: str | None) -> list[str]:
    return _lines(text)


def format_steps(steps: list[str] | None) -> str:
    return "\n".join(steps or [])


def parse_timers(text: str | None) -> list[dict[str, Any]]:
    """'Noodles, 4, 3' -> {name: Noodles, minutes: 4, step: 3}; step is optional."""
    out: list[dict[str, Any]] = []
    for line in _lines(text):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) not in (2, 3) or not parts[0]:
            raise RecipeTextError(f"Timer needs 'name, minutes[, step]': {line}")
        try:
            minutes = float(parts[1].replace(" min", ""))
        except ValueError:
            raise RecipeTextError(f"Minutes must be a number: {line}") from None
        if minutes <= 0:
            raise RecipeTextError(f"Minutes must be above 0: {line}")
        timer: dict[str, Any] = {
            "name": parts[0],
            "minutes": int(minutes) if minutes.is_integer() else minutes,
        }
        if len(parts) == 3 and parts[2]:
            try:
                step = int(parts[2])
            except ValueError:
                raise RecipeTextError(f"Step must be a whole number: {line}") from None
            if step < 1:
                raise RecipeTextError(f"Step numbers start at 1: {line}")
            timer["step"] = step
        out.append(timer)
    return out


def format_timers(timers: list[dict[str, Any]] | None) -> str:
    lines = []
    for t in timers or []:
        parts = [str(t.get("name", "")), str(t.get("minutes", ""))]
        if t.get("step") is not None:
            parts.append(str(t["step"]))
        lines.append(", ".join(parts))
    return "\n".join(lines)


def build_recipe(
    *,
    servings: int | None,
    ingredients: str | None,
    steps: str | None,
    timers: str | None,
    url: str | None,
    notes: str | None,
) -> dict[str, Any]:
    """The recipe dict from the editor's fields; {} when everything is empty.

    Raises RecipeTextError for a line it can't read or a timer whose step
    doesn't exist.
    """
    recipe: dict[str, Any] = {}
    if servings:
        recipe["servings"] = int(servings)
    if parsed := parse_ingredients(ingredients):
        recipe["ingredients"] = parsed
    step_list = parse_steps(steps)
    if step_list:
        recipe["steps"] = step_list
    timer_list = parse_timers(timers)
    for timer in timer_list:
        step = timer.get("step")
        if step is not None and step > len(step_list):
            raise RecipeTextError(
                f"Timer '{timer['name']}' is for step {step}, "
                f"but there are {len(step_list)} step(s)"
            )
    if timer_list:
        recipe["timers"] = timer_list
    if url and url.strip():
        recipe["url"] = url.strip()
    if notes and notes.strip():
        recipe["notes"] = notes.strip()
    return recipe


def split_words(text: str | None) -> list[str]:
    """'a, b,  c' -> [a, b, c] (for aliases and tags)."""
    return [p.strip() for p in (text or "").split(",") if p.strip()]
