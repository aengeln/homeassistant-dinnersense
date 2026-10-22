# Changelog

## 0.1.0

First release.

- Rotating cook with a rolling plan (3–14 days), nightly roll, swap and reassign
- Sensors for tonight and tomorrow, a select per day (choosing a cook swaps), and a read-only calendar
- Menus for any day, past or future, with a dish library (aliases, tags, recipes)
- Eating out for everyone or some cooks, as a free night; `cooking_needed` for reminders
- Availability from calendars, date helpers, a binary sensor or manual ranges, with fairness debt
- `dinnersense_conflict` event when a locked dinner's cook becomes unavailable
- Per-cook score sensors, deterministic stats, and tips (rule-based, with optional AI Task phrasing)
- Ingredients to a shopping list (`Name x quantity`, skipping what is already open)
- `import_from_entity` to seed past menus from an old helper's recorder history
- English and Swedish translations
