"""USDA nutrition estimates. Never logs the API key."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPTS = Path(__file__).resolve().parent
CACHE_PATH = SCRIPTS / "data" / "nutrition_cache.json"
SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

# Approximate grams per unit when USDA serving data is missing.
UNIT_GRAMS = {
    "g": 1.0,
    "ml": 1.0,
    "cup": 240.0,
    "tbsp": 15.0,
    "tsp": 5.0,
    "oz": 28.35,
    "lb": 453.6,
    "qt": 960.0,
    "pt": 480.0,
    "stick": 113.0,  # butter
    "clove": 3.0,
    "slice": 28.0,
    "pinch": 0.3,
    "dash": 0.4,
    "can": 400.0,
    "pkg": 200.0,
    "leaf": 1.0,
    "bunch": 120.0,
    "head": 500.0,
    "box": 400.0,
    "jar": 340.0,
}

DENSITY_OVERRIDES = {
    "flour": {"cup": 120},
    "sugar": {"cup": 200},
    "brown sugar": {"cup": 220},
    "butter": {"cup": 227, "tbsp": 14, "stick": 113},
    "oil": {"cup": 218, "tbsp": 14},
    "milk": {"cup": 244},
    "water": {"cup": 240},
    "rice": {"cup": 185},
    "oats": {"cup": 80},
    "cheese": {"cup": 113},
    "onion": {"": 110, "cup": 160},
    "egg": {"": 50},
    "eggs": {"": 50},
    "garlic": {"clove": 3},
    "salt": {"tsp": 6, "tbsp": 18},
    "pepper": {"tsp": 2},
}

SKIP_NAMES = re_skip = None


def _skip(name: str) -> bool:
    n = name.lower().strip()
    if len(n) < 2:
        return True
    if n in {"salt", "pepper", "salt and pepper", "water", "ice"}:
        return False
    return False


def load_key() -> str:
    load_dotenv(SCRIPTS / ".env")
    return (
        (os.environ.get("USDA_API_KEY") or os.environ.get("FDC_API_KEY") or "")
        .strip()
    )


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def _nutrient_map(food: dict) -> dict[str, float]:
    out = {"calories": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0}
    for n in food.get("foodNutrients") or []:
        name = (n.get("nutrientName") or n.get("name") or "").lower()
        val = n.get("value")
        if val is None:
            val = n.get("amount")
        if val is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        if "energy" in name and "kcal" in name:
            out["calories"] = val
        elif name in {"energy", "energy (atwater general factors)"} and out["calories"] == 0:
            out["calories"] = val
        elif name.startswith("protein"):
            out["protein_g"] = val
        elif name.startswith("total lipid") or name == "fat" or "total fat" in name:
            out["fat_g"] = val
        elif "carbohydrate" in name and "by difference" in name:
            out["carbs_g"] = val
        elif name.startswith("carbohydrate") and out["carbs_g"] == 0:
            out["carbs_g"] = val
    return out


def search_food(session: requests.Session, key: str, query: str, cache: dict) -> dict | None:
    q = " ".join(query.lower().split())[:80]
    if not q:
        return None
    if q in cache:
        return cache[q]
    payload = {
        "query": q,
        "pageSize": 5,
        "dataType": ["Foundation", "SR Legacy", "Survey (FNDDS)"],
    }
    attempt = 0
    while attempt < 6:
        attempt += 1
        try:
            r = session.post(
                SEARCH_URL,
                params={"api_key": key},
                json=payload,
                timeout=30,
            )
        except requests.RequestException:
            time.sleep(min(2 ** attempt, 30))
            continue
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "0")) or min(2 ** attempt, 60)
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            time.sleep(min(2 ** attempt, 30))
            continue
        if r.status_code != 200:
            cache[q] = None
            save_cache(cache)
            return None
        data = r.json()
        foods = data.get("foods") or []
        if not foods:
            cache[q] = None
            save_cache(cache)
            return None
        food = foods[0]
        rec = {
            "fdcId": food.get("fdcId"),
            "description": food.get("description"),
            "per_100g": _nutrient_map(food),
        }
        cache[q] = rec
        save_cache(cache)
        time.sleep(0.15)
        return rec
    return None


def grams_for(name: str, amount: float, unit: str) -> float | None:
    unit = (unit or "").lower().rstrip(".")
    n = name.lower()
    for key, table in DENSITY_OVERRIDES.items():
        if key in n and unit in table:
            return amount * table[unit]
        if key in n and unit == "" and "" in table:
            return amount * table[""]
    if unit in UNIT_GRAMS:
        return amount * UNIT_GRAMS[unit]
    if unit == "" and amount > 0:
        # treat as whole items ~ 100g unless clearly a count of eggs/onions
        if any(w in n for w in ("egg", "onion", "apple", "tomato", "potato", "carrot")):
            return amount * 80.0
        return None
    return None


def estimate_recipe(ingredients: list[dict], servings: int, session: requests.Session, key: str, cache: dict) -> dict:
    servings = max(int(servings or 1), 1)
    totals = {"calories": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0}
    mapped = 0
    considered = 0
    for ing in ingredients:
        name = (ing.get("name") or "").strip()
        if not name:
            continue
        considered += 1
        rec = search_food(session, key, name, cache)
        grams = grams_for(name, float(ing.get("amount") or 0), ing.get("unit") or "")
        if not rec or not rec.get("per_100g") or not grams:
            continue
        factor = grams / 100.0
        for k in totals:
            totals[k] += rec["per_100g"].get(k, 0.0) * factor
        mapped += 1
    per = {k: round(v / servings, 1) if k != "calories" else round(v / servings) for k, v in totals.items()}
    if considered == 0 or mapped == 0:
        conf = "unknown"
        per = {"calories": "", "protein_g": "", "fat_g": "", "carbs_g": ""}
    elif mapped < max(1, considered * 0.6) or any(
        grams_for(i.get("name", ""), float(i.get("amount") or 0), i.get("unit") or "") is None
        for i in ingredients
        if i.get("name")
    ):
        conf = "partial"
    else:
        conf = "estimated"
    return {
        "calories": per["calories"],
        "protein_g": per["protein_g"],
        "fat_g": per["fat_g"],
        "carbs_g": per["carbs_g"],
        "nutrition_confidence": conf,
        "mapped": mapped,
        "considered": considered,
    }
