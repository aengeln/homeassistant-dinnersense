"""Domain records for Dinner Sense. No Home Assistant imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

STATUS_PLANNED = "planned"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_MISSED = "missed"
STATUSES = (STATUS_PLANNED, STATUS_DONE, STATUS_SKIPPED, STATUS_MISSED)


@dataclass
class Cook:
    id: str
    name: str
    order: int = 0
    person_entity: str | None = None
    color: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Cook:
        return cls(**data)


@dataclass
class MenuEntry:
    dish_id: str | None = None
    title: str | None = None
    kind: str = "home"  # home | eating_out | leftovers | skip
    rating: int | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MenuEntry:
        return cls(**data)


MENU_KINDS = ("home", "eating_out", "leftovers", "skip")


@dataclass
class Dish:
    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    recipe: dict[str, Any] | None = None
    created: str = ""
    updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Dish:
        return cls(**data)

    def matches(self, title: str) -> bool:
        wanted = title.strip().casefold()
        return wanted in {self.name.casefold(), *(a.casefold() for a in self.aliases)}


@dataclass
class ChangeRecord:
    at: str  # ISO timestamp
    action: str  # swap | reassign | status | roll | generated
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"at": self.at, "action": self.action, "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChangeRecord:
        return cls(**data)


@dataclass
class Dinner:
    id: str
    date: date
    cook: str
    status: str = STATUS_PLANNED
    locked: bool = False
    repay: bool = False
    menu: MenuEntry | None = None
    history: list[ChangeRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "cook": self.cook,
            "status": self.status,
            "locked": self.locked,
            "repay": self.repay,
            "menu": self.menu.to_dict() if self.menu else None,
            "history": [h.to_dict() for h in self.history],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Dinner:
        return cls(
            id=data["id"],
            date=date.fromisoformat(data["date"]),
            cook=data["cook"],
            status=data.get("status", STATUS_PLANNED),
            locked=data.get("locked", False),
            repay=data.get("repay", False),
            menu=MenuEntry.from_dict(data["menu"]) if data.get("menu") else None,
            history=[ChangeRecord.from_dict(h) for h in data.get("history", [])],
        )


@dataclass
class Settings:
    horizon_days: int = 10
    roll_time: str = "02:00"
    dinner_days: list[int] = field(default_factory=lambda: list(range(7)))
    menu_follows_cook: bool = True
    unconfirmed_past: str = STATUS_DONE  # done | missed
    auto_create_dishes: bool = False
    ai_task_entity: str | None = None
    todo_entity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in known})


def slugify(name: str, fallback: str = "item") -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or fallback


def build_cooks(names: list[str], existing: list[Cook] | None = None) -> list[Cook]:
    """Cooks in rotation order. A name that already exists keeps its id/colour."""
    by_name = {c.name.casefold(): c for c in existing or []}
    cooks: list[Cook] = []
    used: set[str] = set()
    for order, raw in enumerate(n.strip() for n in names if n.strip()):
        previous = by_name.get(raw.casefold())
        if previous and previous.id not in used:
            cook = Cook(previous.id, raw, order, previous.person_entity, previous.color)
        else:
            base = slugify(raw, "cook")
            cook_id, i = base, 2
            while cook_id in used:
                cook_id, i = f"{base}_{i}", i + 1
            cook = Cook(cook_id, raw, order)
        used.add(cook.id)
        cooks.append(cook)
    return cooks
