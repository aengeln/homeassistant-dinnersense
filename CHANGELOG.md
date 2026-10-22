# Changelog

## 0.2.0

- **Swaps never pull tonight in.** A swap's partner day is always after today.
- **Tonight is fixed from** (optional setting): after that time tonight can't be swapped, and sensors
  or availability changes no longer change tonight's cook; a `dinnersense_conflict` is fired instead.
- **Eating out, leftovers and no dinner stay on their date** when cooks swap. Only dishes follow the
  cook, and only if both days allow it. (Before, any menu moved, including eating out.)
- A restart that changes nothing no longer fires `dinnersense_changed` or `dinnersense_rolled` or
  writes storage. The scheduled nightly roll still always announces.
- `import_from_entity` can fill in **who cooked** from a `cook_entity` (for example the old
  `input_select.dinner_duty`), including days with no menu.
- **Dish editor** in Configure → Dishes and recipes (ingredients, steps, timers as plain text).
- `set_ingredient` / `remove_ingredient` change one ingredient without resending the recipe, and an
  empty recipe in `update_dish` clears it. Renaming a dish to an existing name is refused.
- **Last cooked** per person: `cook_last_*` on day entities and the today/tomorrow sensors, and
  `last_cooked_*` on the score sensors.

## 0.1.1

- **Setting a menu now locks the day's cook.** Before, a rebuild (availability appearing at boot, a
  presence sensor, a cook change) could give a planned dish to someone else, which looked like
  values not surviving a restart. Menus saved by 0.1.0 are locked on the next start. Clearing a
  menu unlocks the day unless it was also swapped or reassigned.
- A broken or not-yet-ready availability source can no longer stop the integration from loading.

## 0.1.0

First release.

- Rotating cook with a rolling plan (3–14 days), nightly roll, swap and reassign
- Sensors for tonight and tomorrow, a select per day (choosing a cook swaps), and a read-only calendar
- Menus for any day, past or future, with a dish library (aliases, tags, recipes with timers)
- Eating out for everyone or some cooks, as a free night; `cooking_needed` for reminders
- Availability from calendars, date helpers, a binary sensor or manual ranges, with fairness debt
- `dinnersense_conflict` event when a locked dinner's cook becomes unavailable
- Per-cook score sensors, deterministic stats, and tips (rule-based, with optional AI Task phrasing)
- Ingredients to a shopping list (`Name x quantity`, skipping what is already open)
- `import_from_entity` to seed past menus from an old helper's recorder history
- English and Swedish translations
