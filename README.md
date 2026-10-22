# Dinner Sense

A Home Assistant integration for household dinner planning:

- a **rotating cook** with a rolling plan 3–14 days ahead, regenerated nightly
- **easy swaps**: pick another cook for a day and it swaps with their next day
- **menus** for any day, past, today or future, from a one-line title to a full recipe
- **eating out**, for everyone or only some of the cooks, as a free night
- a **dish library** with aliases, tags and optional recipes
- a **meal log** kept forever, so you can look back
- **tips** such as "No Burgers for 21 days" or "Friday has no menu yet", optionally phrased by an LLM
- **availability**: cooks who are away are skipped, and take extra turns when they are back
- a **shopping list** button that adds today's ingredients

Everything can be done with Home Assistant's own more-info dialogs (select and text entities), so it
works on a Nest Hub with no custom card.

It needs no cloud service and no external database. Data lives in Home Assistant's `.storage`.

## Install

### HACS (custom repository)

1. HACS → ⋮ → **Custom repositories** → add this repository, category **Integration**.
2. Install **Dinner Sense** and restart Home Assistant.
3. Settings → Devices & services → **Add integration** → **Dinner Sense**.

### Manual

Copy this folder to `config/custom_components/dinnersense/` and restart.

## Set up

1. Enter a **household name** and the **cooks in rotation order**, separated by commas
   (for example `Ann, Bob, Cy`). Each household gets one device.
2. Open the integration's **Configure** to change things later. It has two parts:

   **Plan settings**

   | Setting | Default | |
   |---|---|---|
   | Cooks | | Rotation order. A cook keeps their id, colour and availability while their name stays the same; a changed name is treated as a new cook. Removing a cook rebuilds the unlocked future |
   | Days planned ahead | 10 | 3–14 |
   | Nightly roll time | 02:00 | When yesterday is closed out and the horizon is topped up |
   | Days with a planned dinner | all | Untick days with no dinner |
   | Menu follows the cook when swapping | on | Dishes follow the cook; eating out, leftovers and no dinner always stay on their date. Off keeps every menu on its date |
   | Tonight is fixed from | none | A time of day. After it tonight can't be swapped, and sensors or availability changes won't change tonight's cook (see [Tonight](#tonight)) |
   | Unconfirmed past dinners count as | Done | Or Missed |
   | Shopping list | none | A `todo` entity, for the ingredients button |
   | AI Task for tips | none | Optional, see [Tips](#tips) |

   **Cooks and availability**: for each cook, an optional person entity, a colour (hex, handy for
   dashboards) and availability sources (see [Availability](#availability)).

## How the rotation works

- There is always a planned dinner for every dinner day from today to `today + horizon - 1`.
- New days continue from the **last planned cook**, not a fixed calendar index, so swaps don't break
  the cycle.
- At the roll time (and on startup) yesterday's still-planned dinner becomes **done** or **missed**
  (your setting), and the horizon is filled. History is never deleted.
- A human change **locks** a dinner: a swap, a reassign, or **setting a menu** (a title or dish, eating
  out, leftovers or no dinner). Generation never overwrites locked dinners or the past. Clearing a
  menu unlocks the day again, unless it was also swapped or reassigned.
- **Swap**: give day A to cook P. P's next planned day **after today** goes to A's old cook, so
  tonight is never pulled into someone else's swap. Both days are locked. If P has no later day, the
  plan is extended (up to 60 days) until there is one. Past days can't be swapped; use `reassign`
  to correct the log.
- **Menus and swaps**: dishes follow the cook to the other date. Eating out, leftovers and no dinner
  belong to their date, so if either day has one, no menu moves (only the cooks swap).
- Same inputs always give the same plan.

### Tonight

By default a rebuild (a presence sensor, a calendar, a cook change) can still change tonight's cook if
tonight has no menu. Set **Tonight is fixed from** (for example a time before your 14:00 reminder) and
from then on:

- tonight can't be swapped (`reassign` still works, to correct it);
- availability changes never change tonight's cook. You get a `dinnersense_conflict` event instead.

Before that time tonight behaves like any other day. With a menu set, a day's cook is already fixed.

## Entities

For a household called **Amarula** (entity IDs follow your household name):

| Entity | What it is |
|---|---|
| `sensor.amarula_dinner_today`, `sensor.amarula_dinner_tomorrow` | State is the cook's name. Attributes: `date`, `weekday`, `status`, `locked`, `cook_id`, `color`, `menu_title`, `dish_id`, `kind`, `note`, `going_out`, `at_home`, `cooking_needed`, `recipe` (the linked dish's recipe, or none; kept out of the recorder history) |
| `select.amarula_dinner_day_0` … `_N-1` | The cook for each day (`_0` is today). **Choosing a cook performs a swap.** Same attributes as above |
| `text.amarula_dinner_menu_day_0` … | The menu for each day. Type a dish name or alias to link it, or any text. Empty clears it |
| `select.amarula_dinner_kind_day_0` … | What kind of dinner: Home, Eating out (everyone or any combination of cooks), Leftovers or No dinner. See [Eating out](#eating-out) |
| `calendar.amarula_dinner` | Past and planned dinners as all-day events ("Ann — Tacos"). Read-only |
| `sensor.amarula_dinner_score_<cook>` | Dinners cooked in the last 30 days. Attributes: `cooked_90`, `swaps_given`, `swaps_taken`, `debt` |
| `sensor.amarula_dinner_tips` | The first tip as state. Attributes: `tips`, `source` (`rules` or `llm`), `generated_at` |
| `button.amarula_add_ingredients_today` | Adds today's ingredients to the shopping list |

Day slots shift after the nightly roll: slot 0 is always today.

## Menus and dishes

`dinnersense.set_menu` works for **past, today and future** days. A title that matches a dish name
or alias (case-insensitive) links that dish. Anything else is kept as free text. Auto-creating dishes
from new titles is off by default (it is not in the settings UI yet); create dishes with `create_dish`.

A menu has a `kind` (`home`, `eating_out`, `leftovers`, `skip`), an optional `note` and a 1–5
`rating`. Calling `set_menu` with only a date clears the menu.

A dish is just a name, or a full recipe:

```yaml
action: dinnersense.create_dish
data:
  name: Tacos
  aliases: [Taco night]
  tags: [mexican]
  recipe:
    servings: 4
    ingredients:
      - { name: Tortillas, quantity: "8" }
      - { name: Minced beef, quantity: 500 g }
      - { name: Lime }
    steps:
      - Brown the beef
      - Warm the tortillas
    timers:
      - { name: Beef, minutes: 8, step: 1 }
      - { name: Tortillas, minutes: 1.5, step: 2 }
    url: https://example.com/tacos
```

`timers` is an optional list. Each has a `name`, `minutes` (any number above 0) and an optional
`step`: the **1-based** number of the step it belongs to, which must exist. The timers are stored
and returned with the recipe (`list_dishes`); nothing starts them yet.

Setting a menu **fixes that day's cook**, so a rebuild (a cook going away, a cook being removed, a
sensor changing, a restart) never hands a planned dish to someone else. If the cook turns out to be
unavailable you get a `dinnersense_conflict` event instead.

Menus for days before you installed the integration create log-only records with no cook.

## Eating out

Mark a day with the kind select (or `set_menu` with `kind: eating_out`). The select lists every
combination of cooks, for example *Eating out: everyone* or *Eating out: Aengeln, Libertad*. With
more than four cooks only "everyone" is offered; use the service's `going_out` for the rest.

```yaml
action: dinnersense.set_menu
data:
  date: "2026-03-06"
  title: Pizza from Bella
  going_out: [Aengeln, Libertad]   # implies kind: eating_out; leave out for everyone
```

- **Eating out is a free night.** It uses the planned cook's turn, so the rotation never shifts and no
  debt is created. It's fair over time.
- If the planned cook **stays home** while others go out, it is still their normal turn.
- The day exposes `going_out` and `at_home` (names) and **`cooking_needed`**: true only when the
  planned cook is home on a normal dinner. It is false for leftovers, no dinner, skipped days, and
  when the planned cook is out. Use it to skip reminders:

```yaml
conditions:
  - condition: state
    entity_id: sensor.amarula_dinner_today
    attribute: cooking_needed
    state: true
```

- Cooks who aren't going and aren't the planned cook fend for themselves; nothing is tracked for
  them, and household members who don't cook aren't modelled.
- Restaurants never produce "No X for N days" tips.

### Editing dishes

**Configure → Dishes and recipes** lists your dishes (and *New dish*). One form edits the name,
aliases, tags, servings, ingredients, steps, timers, link and notes. Ingredients, steps and timers
are one per line:

```text
Ingredients          Steps                      Timers (name, minutes, step)
Tortillas x 8        Brown the beef             Beef, 8, 1
Minced beef x 500 g  Warm the tortillas         Tortillas, 1.5, 2
Lime
```

Empty fields clear that part of the recipe, and a *Delete this dish* box removes it (menus keep their
title). To change a single ingredient from an automation or Developer tools, use `set_ingredient` and
`remove_ingredient` instead of resending the whole recipe.

### Last cooked

The planned cook's last home-cooked dish is on every day entity and today/tomorrow sensor as
`cook_last_dish`, `cook_last_date` and `cook_last_days_ago`, and on each score sensor as
`last_cooked_dish`, `last_cooked_date` and `last_cooked_days_ago`. Eating out, leftovers and skipped
days don't count, and tonight counts once it is done.

## Availability

Per cook (Configure → Cooks and availability), any of:

- a **calendar** with a keyword (default `away`): events containing it block those dates
- an **`input_datetime`** "away until": away from today up to and including that date
- an **`input_datetime` pair** from/to
- a **`binary_sensor`**: unavailable today while `on` (live, today only)

Or use the services `dinnersense.set_unavailable` and `clear_unavailable` for a fixed range.

An away cook is skipped, the dinner goes to the next available cook, and the skipped cook is *owed* a
turn that is repaid at the next opportunity, so counts even out. Repayments never give one cook two
dinners in a row.

Availability only affects **generation**. It never changes a locked dinner. Instead a
`dinnersense_conflict` event fires so you can notify yourself. Calendars are re-read every 30 minutes
and before each nightly roll; the other sources apply immediately.

## Shopping list

Set a `todo` entity in Plan settings. The button, or `dinnersense.add_ingredients_to_list`, adds the
day's ingredients written as `Tortillas x 8` (Home Assistant's todo lists have no quantity field).
Ingredients already open on the list are skipped, matching by name and ignoring case and the `x 8`.
Completed items don't count. The service takes an optional `date` and `todo_entity`.

If you've done a big shop, or the tortillas are already in the freezer, just tick them off the list.

## Tips

`sensor.<household>_dinner_tips` shows 1–3 short tips computed from your history: dishes or tags
that haven't been eaten for a while, repeats this week, streaks, and days without a menu. They are
English or Swedish following your Home Assistant language.

**An LLM is optional.** With no AI Task set, rule-based tips are used and nothing else changes. If you
pick an `ai_task` entity, the model only receives a small facts payload and may only reuse numbers
from it; a tip containing any other number is dropped. If the AI Task is missing, unavailable, slow
(90 s limit), fails or answers with nothing usable, the rule-based tips are used. It can never break
the integration.

Tips refresh at startup, after the nightly roll, 30 seconds after plan or menu changes, and on
`dinnersense.generate_tips`.

## Services

All services take an optional `config_entry_id` (only needed with several households). Cooks can be
given by name or id.

| Service | Purpose |
|---|---|
| `swap` (`date`, `cook`) | Give a day to another cook, swapping with their next day |
| `reassign` (`date`, `cook`) | Change one day only. Works in the past |
| `skip`, `complete` (`date`) | Mark a day skipped or done |
| `regenerate` | Rebuild unlocked future dinners (menus are kept) |
| `get_plan` (`start?`, `end?`) | *Response*: planned dinners |
| `get_history` (`start`, `end`) | *Response*: dinners up to today |
| `set_menu` (`date`, `title?`, `dish_id?`, `kind?`, `going_out?`, `note?`, `rating?`) | Set or clear a menu, any date |
| `create_dish`, `update_dish`, `delete_dish`, `list_dishes` | The dish library. `update_dish` replaces the whole recipe; an empty recipe clears it |
| `set_ingredient` (`dish_id`, `name`, `quantity?`), `remove_ingredient` (`dish_id`, `name`) | Change one ingredient without resending the recipe. Matches by name; omit the quantity to keep it, send an empty one to remove it |
| `set_unavailable` (`cook`, `start`, `end`), `clear_unavailable` (`cook`, `start?`, `end?`) | Manual away ranges |
| `get_stats` (`period?`) | *Response*: days since each dish and tag, frequencies, streaks, unplanned days |
| `generate_tips` | Refresh the tips now |
| `add_ingredients_to_list` (`date?`, `todo_entity?`) | Ingredients to a todo list |
| `import_from_entity` (`entity_id?`, `cook_entity?`, `days?`, `overwrite?`) | Seed past menus and who cooked from old entities' history |

### Importing an old "today's dinner" helper

```yaml
action: dinnersense.import_from_entity
data:
  entity_id: input_text.today_s_dinner   # the menu
  cook_entity: input_select.dinner_duty  # who cooked (optional)
  days: 10
```

The last real value set on each day becomes that day's menu, and the cook entity's value (matched to
your cooks by name) becomes who cooked. Empty, `unknown` and `unavailable` values are ignored. Days
that already have a menu are skipped, and an existing cook is kept, unless `overwrite: true`. Names
that aren't one of your cooks are reported as `unknown_cook`. A day with a cook but no menu gets a
record too, and imported cooks count in the scores and in "last cooked". The recorder usually keeps
about 10 days, so this is a one-off rescue.

## Events

All carry `config_entry_id`.

| Event | When | Data |
|---|---|---|
| `dinnersense_changed` | A change to dinners that actually changed something | `before` and `after` (lists of dinners) for edits, swaps and imports. Bulk rebuilds send `reason` (`regenerate` or `availability`) and `rebuilt` (a count) instead; imports also send `reason: import` |
| `dinnersense_swapped` | A swap | `before`, `after` |
| `dinnersense_rolled` | The nightly roll, and a start-up that had something to close or add. A restart with nothing to do is silent | `date`, `closed`, `added` |
| `dinnersense_conflict` | A locked dinner's cook became unavailable | `date`, `cook`, `cook_name` |

Every change is applied, saved and announced as one step, so automations never see half-applied
state. Entity updates made by the integration itself are never treated as user actions, so a swap
doesn't trigger another swap.

## Automation examples

Remind tonight's cook (map names to your own notify targets):

```yaml
automation:
  - alias: Dinner reminder
    triggers:
      - trigger: time
        at: "14:00:00"
    conditions:
      - condition: state
        entity_id: sensor.amarula_dinner_today
        attribute: cooking_needed
        state: true
    actions:
      - action: notify.notify
        data:
          title: "Tonight: {{ states('sensor.amarula_dinner_today') }}"
          message: >
            {{ state_attr('sensor.amarula_dinner_today', 'menu_title') or 'No menu yet' }}
            {% if states('sensor.amarula_dinner_tips') not in ['unknown', 'unavailable'] %}
            – {{ states('sensor.amarula_dinner_tips') }}{% endif %}
```

Reset a helper after the nightly roll:

```yaml
automation:
  - alias: Reset dinner reminder flag
    triggers:
      - trigger: event
        event_type: dinnersense_rolled
    actions:
      - action: input_boolean.turn_off
        target:
          entity_id: input_boolean.dinner_reminder_sent
```

Tell someone when an away cook has a locked dinner:

```yaml
automation:
  - alias: Dinner conflict
    triggers:
      - trigger: event
        event_type: dinnersense_conflict
    actions:
      - action: persistent_notification.create
        data:
          title: Dinner conflict
          message: "{{ trigger.event.data.cook_name }} is away on {{ trigger.event.data.date }} but has dinner."
```

## Dashboards

### Native cards (work everywhere, including a Nest Hub)

Tapping a row opens the native picker, and picking a cook swaps. This needs nothing but built-in
cards:

```yaml
type: entities
title: Dinner
entities:
  - entity: select.amarula_dinner_day_0
    name: Today
  - entity: text.amarula_dinner_menu_day_0
    name: Today's menu
  - type: divider
  - entity: select.amarula_dinner_day_1
    name: Tomorrow
  - entity: text.amarula_dinner_menu_day_1
    name: Tomorrow's menu
  - type: divider
  - entity: select.amarula_dinner_day_2
  - entity: text.amarula_dinner_menu_day_2
  - type: divider
  - entity: button.amarula_add_ingredients_today
  - entity: sensor.amarula_dinner_tips
```

### Mushroom

A compact rota row per day. The icon takes the cook's colour, and tapping opens the day's select
(the swap picker):

```yaml
type: custom:mushroom-template-card
entity: select.amarula_dinner_day_3
primary: >
  {{ state_attr('select.amarula_dinner_day_3', 'weekday') }} – {{ states('select.amarula_dinner_day_3') }}
secondary: >
  {{ state_attr('select.amarula_dinner_day_3', 'menu_title') or 'No menu yet' }}
  {% if state_attr('select.amarula_dinner_day_3', 'cook_last_dish') %}
  · last: {{ state_attr('select.amarula_dinner_day_3', 'cook_last_dish') }}
  ({{ state_attr('select.amarula_dinner_day_3', 'cook_last_days_ago') }} d ago)
  {% endif %}
icon: mdi:chef-hat
icon_color: "{{ state_attr('select.amarula_dinner_day_3', 'color') or 'grey' }}"
badge_icon: "{{ 'mdi:lock' if state_attr('select.amarula_dinner_day_3', 'locked') else '' }}"
tap_action:
  action: more-info
```

Add `custom:mushroom-title-card` and one template card per day in a vertical stack for a full week.
These Mushroom examples have not been rendered in a browser while writing this README; adjust them to
your Mushroom version if needed.

### Cooking mode

`sensor.<household>_dinner_today` (and `_tomorrow`) carry the linked dish's recipe in the `recipe`
attribute: `servings`, `ingredients`, `steps`, `timers` and `notes`. A Markdown card that lists
tonight's steps and timers:

```yaml
type: markdown
content: >
  {% set r = state_attr('sensor.amarula_dinner_today', 'recipe') %}
  {% if r %}
  ## {{ state_attr('sensor.amarula_dinner_today', 'menu_title') }}
  {% for step in r.steps %}
  {{ loop.index }}. {{ step }}
  {% endfor %}
  {% for t in r.timers %}
  - **{{ t.name }}**: {{ t.minutes }} min (step {{ t.step }})
  {% endfor %}
  {% else %}No recipe for tonight.{% endif %}
```

To start a timer you need a Home Assistant `timer` helper. `timer.start` takes the duration in
seconds, which a template gets from `(t.minutes * 60) | int`. The Markdown template above was checked
against a running Home Assistant; wiring the timers to buttons depends on a templating card such as
`custom:button-card` and hasn't been tried here.

### Cast to a Nest Hub

Use native and Mushroom cards. **Bubble Card doesn't render on Google cast**, so avoid it on cast
dashboards.

## Limitations

- Cooks are edited as a list of names in the options flow; reordering is by editing the list, and
  renaming a cook makes a new cook.
- Auto-creating dishes from new menu titles exists as a setting but isn't in the options UI yet.
- Dishes are edited in Configure → Dishes and recipes (or with the services). The editor shows
  ingredients, steps and timers as plain text lines.
- Select and text entities are created for the configured horizon, so changing it reloads the
  integration.
- Day-slot entities show the weekday and date in their attributes, not in their names.
- Every `done` dinner counts as a turn in the scores, including eating out and leftovers.
- Dinners become `done` at the nightly roll, so a score doesn't count tonight's dinner until then.

## Development

The rotation engine in `rotation/` has no Home Assistant imports so it can be shared later. Tests use
`pytest-homeassistant-custom-component`:

```bash
pip install pytest-homeassistant-custom-component ruff mypy
pytest tests          # run from this folder; pytest.ini lives in tests/
ruff check . && mypy
```

The tests import the integration as `custom_components.dinnersense`, so keep this folder inside a
`custom_components/` directory (or adapt `pythonpath` in `tests/pytest.ini`).
