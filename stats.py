"""Deterministic meal statistics. No Home Assistant imports."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

from .models import STATUS_DONE, STATUS_PLANNED, Cook, Dinner, Dish

WINDOWS = (7, 30, 90, 365)
TOP_DISHES = 3


def _title(dinner: Dinner, dishes: dict[str, Dish]) -> str | None:
    menu = dinner.menu
    if menu is None:
        return None
    if menu.title:
        return menu.title
    dish = dishes.get(menu.dish_id) if menu.dish_id else None
    return dish.name if dish else None


def eaten(
    dinners: Iterable[Dinner],
    dishes: dict[str, Dish],
    today: date,
    period: int | None = None,
) -> list[tuple[Dinner, str, str, list[str]]]:
    """Dinners that were eaten (up to today, with a menu), oldest first.

    Each item is (dinner, key, name, tags). ``key`` is the dish id when linked,
    else the lower-cased title.
    """
    first = today - timedelta(days=period - 1) if period else None
    out = []
    for dinner in sorted(dinners, key=lambda d: d.date):
        if dinner.date > today or dinner.status not in (STATUS_DONE, STATUS_PLANNED):
            continue
        if first and dinner.date < first:
            continue
        menu = dinner.menu
        name = _title(dinner, dishes)
        if menu is None or menu.kind == "skip" or not name:
            continue
        dish = dishes.get(menu.dish_id) if menu.dish_id else None
        key = dish.id if dish else f"title:{name.casefold()}"
        tags = sorted({t.casefold() for t in dish.tags}) if dish else []
        out.append((dinner, key, name, tags))
    return out


def _row(days: list[date], today: date) -> dict[str, Any]:
    last = max(days) if days else None
    row: dict[str, Any] = {
        "last_eaten": last.isoformat() if last else None,
        "days_since": (today - last).days if last else None,
    }
    for n in WINDOWS:
        cutoff = today - timedelta(days=n)
        row[f"count_{n}"] = sum(1 for d in days if d > cutoff)
    return row


def _gap_free(earlier: date, later: date, dinner_days: frozenset[int]) -> bool:
    """True when no dinner day lies strictly between the two dates."""
    day = earlier + timedelta(days=1)
    while day < later:
        if day.weekday() in dinner_days:
            return False
        day += timedelta(days=1)
    return True


def compute_stats(
    dinners: dict[date, Dinner],
    dishes: dict[str, Dish],
    cooks: list[Cook],
    today: date,
    horizon_days: int,
    period: int | None = None,
    dinner_days: Iterable[int] = range(7),
) -> dict[str, Any]:
    """All statistics. ``period`` limits history to the last N days."""
    days_with_dinner = frozenset(dinner_days)
    history = eaten(dinners.values(), dishes, today, period)

    by_dish: dict[str, list[date]] = {}
    names: dict[str, str] = {}
    tags_of: dict[str, list[str]] = {}
    by_tag: dict[str, list[date]] = {}
    tag_dishes: dict[str, set[str]] = {}
    for dinner, key, name, tags in history:
        by_dish.setdefault(key, []).append(dinner.date)
        names[key] = name
        tags_of[key] = tags
        for tag in tags:
            by_tag.setdefault(tag, []).append(dinner.date)
            tag_dishes.setdefault(tag, set()).add(key)
    if period is None:  # library dishes never eaten still get a row
        for dish in dishes.values():
            by_dish.setdefault(dish.id, [])
            names.setdefault(dish.id, dish.name)
            tags_of.setdefault(dish.id, sorted({t.casefold() for t in dish.tags}))

    dish_rows = [
        {"key": k, "name": names[k], "tags": tags_of[k], **_row(v, today)}
        for k, v in by_dish.items()
    ]
    dish_rows.sort(
        key=lambda r: (r["days_since"] is None, r["days_since"] or 0, r["name"])
    )
    tag_rows = [
        {"tag": t, "dishes": len(tag_dishes[t]), **_row(v, today)}
        for t, v in sorted(by_tag.items())
    ]
    tag_rows.sort(key=lambda r: (r["days_since"], r["tag"]))

    cook_stats: dict[str, Any] = {}
    for cook in cooks:
        mine = [h for h in history if h[0].cook == cook.id]
        counts = Counter(name for _, _, name, _ in mine)
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_DISHES]
        last = mine[-1] if mine else None
        cook_stats[cook.id] = {
            "name": cook.name,
            "last": {"date": last[0].date.isoformat(), "title": last[2]}
            if last
            else None,
            "top_dishes": [{"name": n, "count": c} for n, c in top],
        }

    streaks = []
    for tag in by_tag:
        length, newer = 0, today
        for dinner, _, _, tags in reversed(history):
            if tag not in tags or not _gap_free(dinner.date, newer, days_with_dinner):
                break
            length += 1
            newer = dinner.date
        # A streak only counts while it is current: it runs up to today/yesterday.
        if length >= 2 and _gap_free(history[-1][0].date, today, days_with_dinner):
            streaks.append(
                {"tag": tag, "length": length, "ends": history[-1][0].date.isoformat()}
            )
    streaks.sort(key=lambda s: (-s["length"], s["tag"]))

    end = today + timedelta(days=horizon_days)
    unplanned = [
        {
            "date": d.date.isoformat(),
            "weekday": d.date.weekday(),
            "in_days": (d.date - today).days,
        }
        for d in sorted(dinners.values(), key=lambda d: d.date)
        if today <= d.date < end
        and d.status == STATUS_PLANNED
        and not _title(d, dishes)
    ]

    return {
        "today": today.isoformat(),
        "period_days": period,
        "dishes": dish_rows,
        "tags": tag_rows,
        "cooks": cook_stats,
        "streaks": streaks,
        "unplanned": unplanned,
    }
