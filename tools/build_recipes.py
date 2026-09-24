#!/usr/bin/env python3
"""Generate the Jekyll `_recipes` collection from the Obsidian recipe vault.

Reads every note under the vault's `Recipes/` tree, pulls the structured
`kaper` block out of each one, and writes a flat Jekyll collection plus
`_data/facets.yml`, which drives the filter controls on the browse page.

The generated files are committed, so GitHub Actions never needs the vault.

    python3 tools/build_recipes.py [--vault PATH] [--check]
"""

from __future__ import annotations

import argparse
import collections
import re
import shutil
import sys
import unicodedata
from pathlib import Path

import yaml

SITE = Path(__file__).resolve().parent.parent
DEFAULT_VAULT = SITE.parent / "Recipe"

COURSE_LABELS = {
    "main": "Mains",
    "side": "Sides",
    "dessert": "Desserts",
    "breakfast": "Breakfast",
    "condiment": "Condiments",
    "drink": "Drinks",
}

# Units that read better pluralised once the amount is above one. Abbreviated
# measures (tsp, tbsp, oz, lb) are conventionally left alone.
PLURALISE = {
    "cup": "cups",
    "clove": "cloves",
    "slice": "slices",
    "can": "cans",
    "jar": "jars",
    "bottle": "bottles",
    "sheet": "sheets",
    "stick": "sticks",
    "bunch": "bunches",
    "head": "heads",
    "pinch": "pinches",
    "dash": "dashes",
    "pt": "pts",
    "qt": "qts",
}

VULGAR = {
    0.125: "\u215b",
    0.25: "\u00bc",
    0.333: "\u2153",
    0.33: "\u2153",
    0.375: "\u215c",
    0.5: "\u00bd",
    0.625: "\u215d",
    0.666: "\u2154",
    0.67: "\u2154",
    0.75: "\u00be",
    0.875: "\u215e",
}

# The vault's `era` values are placeholders rather than real dates, so they are
# dropped here — both the field itself and the decade tags that mirror it.
ERA_TAG = re.compile(r"^\d{4}s$")

# Kaper step titles carry their own number ("1. Slice"); the template renders
# them in an ordered list, so the number would otherwise appear twice.
STEP_NUMBER = re.compile(r"^\s*\d+\s*[.)]\s*")


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", " ", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value) or "recipe"


def parse_duration(text) -> int:
    """`2h22m` -> 142 minutes. Returns 0 for blank or unparseable values."""
    if text is None:
        return 0
    text = str(text).strip()
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", text)
    if not match or not any(match.groups()):
        return 0
    hours, minutes = match.groups()
    return int(hours or 0) * 60 + int(minutes or 0)


def human_duration(minutes: int) -> str:
    if not minutes:
        return ""
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} hr {mins} min"
    if hours:
        return f"{hours} hr"
    return f"{mins} min"


def iso_duration(minutes: int) -> str:
    if not minutes:
        return ""
    hours, mins = divmod(minutes, 60)
    return "PT" + (f"{hours}H" if hours else "") + (f"{mins}M" if mins else "")


def format_amount(amount) -> str:
    """Render `1.5` as `1½` so the ingredient list reads like a recipe card."""
    if amount is None or amount == "":
        return ""
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    if value == int(value):
        return str(int(value))
    whole = int(value)
    fraction = round(value - whole, 3)
    glyph = VULGAR.get(fraction)
    if glyph is None:
        return f"{value:g}"
    return f"{whole}{glyph}" if whole else glyph


def ingredient_text(amount: str, unit: str, name: str) -> str:
    unit = (unit or "").strip()
    if unit and amount:
        try:
            if float(amount.replace("\u00bd", ".5").replace("\u00bc", ".25") or 0) > 1:
                unit = PLURALISE.get(unit, unit)
        except ValueError:
            if not amount.isdigit() or int(amount) > 1:
                unit = PLURALISE.get(unit, unit)
    return " ".join(part for part in (amount, unit, name) if part).strip()


def clean(value) -> str:
    """Frontmatter mixes empty strings, None and numbers; normalise to str."""
    if value is None:
        return ""
    return str(value).strip()


def to_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number == int(number) else number


def read_note(path: Path) -> tuple[dict, dict] | None:
    text = path.read_text(encoding="utf-8")
    front = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    block = re.search(r"^```kaper\n(.*?)\n```", text, re.S | re.M)
    if not front or not block:
        return None
    try:
        meta = yaml.safe_load(front.group(1)) or {}
        kaper = yaml.safe_load(block.group(1)) or {}
    except yaml.YAMLError as exc:
        print(f"  ! {path.name}: {str(exc).splitlines()[0]}", file=sys.stderr)
        return None
    return meta, kaper


def build_ingredients(kaper: dict) -> list[dict]:
    groups = kaper.get("ingredients") or {}
    if isinstance(groups, list):
        groups = {"main": groups}
    items = []
    for entries in groups.values():
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            name = clean(entry.get("name"))
            if not name:
                continue
            amount = format_amount(entry.get("amount"))
            unit = clean(entry.get("unit"))
            note = clean(entry.get("note"))
            items.append(
                {
                    "amount": amount,
                    "unit": unit,
                    "name": name,
                    "note": note,
                    "text": ingredient_text(amount, unit, name)
                    + (f", {note}" if note else ""),
                }
            )
    return items


def build_steps(kaper: dict) -> tuple[list[dict], int]:
    steps = []
    total = 0
    for entry in kaper.get("steps") or []:
        if not isinstance(entry, dict):
            continue
        minutes = parse_duration(entry.get("duration"))
        total += minutes
        steps.append(
            {
                "title": STEP_NUMBER.sub("", clean(entry.get("title"))),
                "note": clean(entry.get("note")),
                "tip": clean(entry.get("tip")),
                "minutes": minutes,
                "duration": human_duration(minutes),
                "duration_iso": iso_duration(minutes),
            }
        )
    return steps, total


def collect(vault: Path) -> list[dict]:
    recipes = []
    used_slugs: dict[str, int] = {}
    for path in sorted((vault / "Recipes").rglob("*.md")):
        if any(part.startswith("_") for part in path.relative_to(vault).parts):
            continue  # _Inbox / _Outbox / _Duplicates are staging areas
        parsed = read_note(path)
        if parsed is None:
            continue
        meta, kaper = parsed
        if clean(meta.get("type")) != "recipe":
            continue

        title = clean(meta.get("title")) or path.stem
        slug = slugify(title)
        used_slugs[slug] = used_slugs.get(slug, 0) + 1
        if used_slugs[slug] > 1:
            slug = f"{slug}-{used_slugs[slug]}"

        steps, step_minutes = build_steps(kaper)
        prep = parse_duration(meta.get("prep_time"))
        cook = parse_duration(meta.get("cook_time"))
        total = parse_duration(meta.get("total_time")) or (prep + cook) or step_minutes

        course = clean(meta.get("course")) or "main"
        tags = [
            clean(tag)
            for tag in (meta.get("tags") or [])
            if clean(tag) and not ERA_TAG.match(clean(tag))
        ]

        recipes.append(
            {
                "note": path,
                "stem": path.stem,
                "slug": slug,
                "title": title,
                "course": course,
                "course_label": COURSE_LABELS.get(course, course.title()),
                "cuisine": clean(meta.get("cuisine")),
                "servings": to_number(meta.get("servings")),
                "yield": clean(meta.get("yield")),
                "difficulty": clean(kaper.get("difficulty")),
                "oven_temp_f": to_number(meta.get("oven_temp_f")),
                "card_id": clean(meta.get("card_id")),
                "prep_minutes": prep,
                "cook_minutes": cook,
                "total_minutes": total,
                # A note can repeat an allergen; dedupe so the recipe page does
                # not show it twice and the facet count stays honest.
                "allergens": list(
                    dict.fromkeys(
                        clean(a) for a in (meta.get("allergens") or []) if clean(a)
                    )
                ),
                "tags": sorted(set(tags)),
                "nutrition": {
                    key: to_number(meta.get(field))
                    for key, field in (
                        ("calories", "calories"),
                        ("protein_g", "protein_g"),
                        ("fat_g", "fat_g"),
                        ("carbs_g", "carbs_g"),
                    )
                },
                "nutrition_confidence": clean(meta.get("nutrition_confidence")),
                "ingredients": build_ingredients(kaper),
                "steps": steps,
                "duplicates": [
                    match
                    for raw in (meta.get("possible_duplicates") or [])
                    for match in [re.sub(r"^\[\[|\]\]$", "", clean(raw)).split("|")[0]]
                    if match
                ],
            }
        )
    return recipes


def front_matter(recipe: dict, by_stem: dict[str, dict]) -> dict:
    related = []
    for stem in recipe["duplicates"]:
        other = by_stem.get(stem)
        if other and other["slug"] != recipe["slug"]:
            related.append({"title": other["title"], "slug": other["slug"]})

    nutrition = {k: v for k, v in recipe["nutrition"].items() if v is not None}

    data = {
        "title": recipe["title"],
        "slug": recipe["slug"],
        "course": recipe["course"],
        "course_label": recipe["course_label"],
        "ingredients": recipe["ingredients"],
        "steps": recipe["steps"],
    }
    for key in ("cuisine", "yield", "difficulty", "card_id"):
        if recipe[key]:
            data[key] = recipe[key]
    if recipe["servings"] is not None:
        data["servings"] = recipe["servings"]
    if recipe["oven_temp_f"] is not None:
        data["oven_temp_f"] = recipe["oven_temp_f"]
    for key, minutes in (
        ("prep", recipe["prep_minutes"]),
        ("cook", recipe["cook_minutes"]),
        ("total", recipe["total_minutes"]),
    ):
        if minutes:
            data[f"{key}_minutes"] = minutes
            data[f"{key}_time"] = human_duration(minutes)
            data[f"{key}_iso"] = iso_duration(minutes)
    if recipe["tags"]:
        data["tags"] = recipe["tags"]
    if recipe["allergens"]:
        data["allergens"] = recipe["allergens"]
    if nutrition:
        data["nutrition"] = nutrition
        if recipe["nutrition_confidence"]:
            data["nutrition_confidence"] = recipe["nutrition_confidence"]
    if related:
        data["related"] = related

    # Everything the browse page matches free-text queries against, lowercased
    # once here so the filter script never has to normalise at keystroke time.
    haystack = [recipe["title"], recipe["course_label"], recipe["cuisine"]]
    haystack += recipe["tags"]
    haystack += [item["name"] for item in recipe["ingredients"]]
    data["search"] = " ".join(part for part in haystack if part).lower()
    return data


def write_collection(recipes: list[dict], out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    by_stem = {r["stem"]: r for r in recipes}
    for recipe in recipes:
        data = front_matter(recipe, by_stem)
        body = yaml.safe_dump(
            data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100
        )
        (out_dir / f"{recipe['slug']}.md").write_text(
            f"---\n{body}---\n", encoding="utf-8"
        )


def write_facets(recipes: list[dict], out_file: Path) -> None:
    """Filter options for the browse page, ordered so the UI stays stable."""
    courses = collections.Counter()
    cuisines = collections.Counter()
    tags = collections.Counter()
    allergens = collections.Counter()
    for recipe in recipes:
        courses[(recipe["course"], recipe["course_label"])] += 1
        if recipe["cuisine"]:
            cuisines[recipe["cuisine"]] += 1
        tags.update(recipe["tags"])
        allergens.update(recipe["allergens"])

    def listed(counter, order="count"):
        items = counter.most_common()
        if order == "name":
            items = sorted(items, key=lambda kv: kv[0].lower())
        return [{"key": key, "count": count} for key, count in items]

    data = {
        "total": len(recipes),
        # Courses keep the order declared in _config.yml, not their size.
        "courses": [
            {"key": key, "label": label, "count": count}
            for key, label in [
                (k, COURSE_LABELS[k]) for k in COURSE_LABELS if any(c[0] == k for c in courses)
            ]
            for count in [courses[(key, label)]]
        ],
        "cuisines": listed(cuisines),
        "tags": listed(tags),
        "allergens": listed(allergens),
    }
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what would be generated without writing",
    )
    args = parser.parse_args()

    if not (args.vault / "Recipes").is_dir():
        print(f"No Recipes/ directory under {args.vault}", file=sys.stderr)
        return 1

    recipes = collect(args.vault)
    if not recipes:
        print("No recipes found", file=sys.stderr)
        return 1

    courses: dict[str, int] = {}
    for recipe in recipes:
        courses[recipe["course_label"]] = courses.get(recipe["course_label"], 0) + 1
    summary = ", ".join(f"{label} {count}" for label, count in sorted(courses.items()))
    print(f"{len(recipes)} recipes ({summary})")

    missing = [r["title"] for r in recipes if not r["ingredients"] or not r["steps"]]
    if missing:
        print(f"  ! {len(missing)} with no ingredients or no steps: {missing[:5]}")

    if args.check:
        return 0

    write_collection(recipes, SITE / "_recipes")
    write_facets(recipes, SITE / "_data" / "facets.yml")
    print("Wrote _recipes/ and _data/facets.yml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
