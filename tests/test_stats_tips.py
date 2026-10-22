"""Stats and tips (pure functions)."""

from __future__ import annotations

from datetime import date, timedelta

from custom_components.dinnersense.models import Cook, Dinner, Dish, MenuEntry
from custom_components.dinnersense.stats import compute_stats
from custom_components.dinnersense.tips import (
    build_instructions,
    clean_llm_tips,
    facts_for_llm,
    numbers_in,
    rule_tips,
)

TODAY = date(2026, 3, 2)
COOKS = [Cook("ann", "Ann", 0), Cook("bob", "Bob", 1)]
DISHES = {
    "burgers": Dish("burgers", "Burgers", tags=["Burger", "beef"]),
    "tacos": Dish("tacos", "Tacos", tags=["mexican"]),
    "enchiladas": Dish("enchiladas", "Enchiladas", tags=["mexican"]),
    "fish": Dish("fish", "Fish", tags=["fish"]),
    "soup": Dish("soup", "Soup"),  # never eaten
}


def d(offset: int, cook="ann", dish=None, title=None, status="done", kind="home"):
    day = TODAY + timedelta(days=offset)
    menu = MenuEntry(dish_id=dish, title=title, kind=kind) if (dish or title) else None
    return day, Dinner(f"id{offset}", day, cook, status, menu=menu)


def build(*items):
    return dict(items)


def history():
    return build(
        d(-40, dish="burgers"),
        d(-30, dish="burgers"),
        d(-21, dish="burgers", cook="bob"),
        d(-10, dish="fish"),
        d(-3, dish="tacos"),
        d(-2, dish="enchiladas", cook="bob"),
        d(-1, dish="tacos"),
        d(0, dish="tacos", status="planned"),
    )


def stats(dinners=None, period=None):
    return compute_stats(dinners or history(), DISHES, COOKS, TODAY, 10, period)


def row(s, key):
    return next(r for r in s["dishes"] if r["key"] == key)


def test_days_since_and_counts_per_dish():
    r = row(stats(), "burgers")
    assert (r["last_eaten"], r["days_since"]) == ("2026-02-09", 21)
    assert (r["count_30"], r["count_90"], r["count_365"], r["count_7"]) == (1, 3, 3, 0)
    assert row(stats(), "tacos")["count_7"] == 3


def test_tags_aggregate_across_dishes_and_count_a_dinner_once():
    tags = {t["tag"]: t for t in stats()["tags"]}
    assert tags["mexican"]["count_7"] == 4  # tacos x3 + enchiladas
    assert tags["burger"]["days_since"] == 21  # tag is case-folded


def test_library_dish_never_eaten_has_no_last_date():
    r = row(stats(), "soup")
    assert r["last_eaten"] is None and r["days_since"] is None and r["count_365"] == 0


def test_free_text_titles_are_keyed_by_lowercase_title():
    s = stats(build(d(-5, title="Pizza from Bella"), d(-12, title="pizza from bella")))
    r = row(s, "title:pizza from bella")
    assert r["count_30"] == 2 and r["days_since"] == 5


def test_skip_missed_future_and_menuless_days_are_not_eaten():
    s = stats(
        build(
            d(-1, dish="tacos", status="missed"),
            d(-2, dish="tacos", status="skipped"),
            d(-3, dish="tacos", kind="skip"),
            d(-4),  # no menu
            d(2, dish="tacos", status="planned"),  # future
        )
    )
    assert row(s, "tacos")["count_365"] == 0


def test_period_limits_history():
    s = stats(period=7)
    assert all(
        r["key"] != "burgers" or r["last_eaten"] is None for r in s["dishes"]
    ) or "burgers" not in {r["key"] for r in s["dishes"]}
    assert row(s, "tacos")["count_7"] == 3


def test_streaks_are_trailing_runs_of_a_tag():
    s = stats()
    assert [(x["tag"], x["length"]) for x in s["streaks"]] == [("mexican", 4)]


def test_cook_favourites_and_last():
    s = stats()
    assert s["cooks"]["ann"]["top_dishes"][:2] == [
        {"name": "Tacos", "count": 3},
        {"name": "Burgers", "count": 2},
    ]
    assert s["cooks"]["bob"]["last"] == {"date": "2026-02-28", "title": "Enchiladas"}


def test_unplanned_lists_future_planned_days_without_menu():
    s = stats(
        build(
            d(0, dish="tacos", status="planned"),
            d(1, status="planned"),
            d(2, dish="tacos", status="planned"),
            d(3, status="planned"),
            d(30, status="planned"),  # beyond horizon
        )
    )
    assert [u["in_days"] for u in s["unplanned"]] == [1, 3]


def test_stats_are_json_safe_and_deterministic():
    import json

    assert json.dumps(stats()) == json.dumps(stats())


# -- tips -------------------------------------------------------------------


def test_rule_tips_order_and_text():
    s = stats(history() | build(d(1, status="planned")))
    texts = [t["text"] for t in rule_tips(s)]
    assert texts == [
        "Tomorrow has no menu yet",
        "mexican 4× this week",
        "No Burgers for 21 days",
    ]


def test_rule_tip_lapse():
    only = build(d(-40, dish="burgers"), d(-30, dish="burgers"), d(-21, dish="burgers"))
    assert [t["text"] for t in rule_tips(stats(only))] == ["No Burgers for 21 days"]


def test_rule_tip_lapse_needs_a_habit():
    once = build(d(-40, dish="burgers"))
    assert rule_tips(stats(once)) == []


def test_weekday_tip_for_later_day_and_swedish():
    s = stats(build(d(3, status="planned")))  # Thursday 5 March
    assert rule_tips(s)[0]["text"] == "Thursday has no menu yet"
    assert rule_tips(s, "sv-SE")[0]["text"] == "Torsdag saknar meny"
    assert (
        rule_tips(stats(build(d(0, status="planned"))), "sv")[0]["text"]
        == "Ikväll saknas meny"
    )


def test_no_dish_name_and_tag_duplicates():
    # "fish" is both a dish name and a tag: appears once.
    only = build(*(d(-n, dish="fish") for n in (40, 30, 20)))
    assert [t["text"] for t in rule_tips(stats(only))] == ["No Fish for 20 days"]


def test_facts_only_contain_expected_numbers():
    facts = facts_for_llm(stats(history() | build(d(1, status="planned"))))
    assert facts["lapses"][0] == {
        "name": "Burgers",
        "days_since_last": 21,
        "times_last_90_days": 3,
    }
    assert {"21", "3", "1"} <= numbers_in(facts)
    assert "Burgers" in build_instructions(facts, "sv")
    assert "Swedish" in build_instructions(facts, "sv")


def test_clean_llm_tips_rejects_invented_numbers_and_tidies_bullets():
    facts = {"lapses": [{"name": "Burgers", "days_since_last": 21}]}
    got = clean_llm_tips(
        {
            "tips": [
                "- No burgers for 21 days",
                "1. Burgers 99 days ago!",
                "",
                "x" * 400,
                5,
            ]
        },
        facts,
    )
    assert got == ["No burgers for 21 days"]


def test_clean_llm_tips_accepts_plain_text_and_ignores_garbage():
    facts = {"a": 3}
    assert clean_llm_tips("Tip one 3\nTip two", facts) == ["Tip one 3", "Tip two"]
    assert clean_llm_tips(None, facts) == []
    assert clean_llm_tips({"nope": 1}, facts) == []


def test_non_consecutive_days_are_not_a_streak():
    s = stats(
        build(d(-40, dish="burgers"), d(-30, dish="burgers"), d(-21, dish="burgers"))
    )
    assert s["streaks"] == []


def test_stale_streak_is_not_reported():
    s = stats(build(d(-7, dish="tacos"), d(-6, dish="tacos"), d(-5, dish="tacos")))
    assert s["streaks"] == []


def test_streak_respects_dinner_days():
    # Dinners only Mon/Wed/Fri (weekday 0,2,4): Mon 23 Feb, Wed 25, Fri 27 are consecutive.
    days = {date(2026, 2, 23), date(2026, 2, 25), date(2026, 2, 27)}
    dinners = {
        x: Dinner(f"i{x}", x, "ann", "done", menu=MenuEntry(dish_id="tacos"))
        for x in days
    }
    s = compute_stats(
        dinners, DISHES, COOKS, date(2026, 2, 28), 10, dinner_days=[0, 2, 4]
    )
    assert [(x["tag"], x["length"]) for x in s["streaks"]] == [("mexican", 3)]


def test_restaurants_are_not_lapse_tips():
    only = build(
        d(-40, title="Pizza from Bella", kind="eating_out"),
        d(-30, title="Pizza from Bella", kind="eating_out"),
        d(-21, title="Pizza from Bella", kind="eating_out"),
    )
    assert rule_tips(stats(only)) == []
    assert row(stats(only), "title:pizza from bella")["kind"] == "eating_out"
