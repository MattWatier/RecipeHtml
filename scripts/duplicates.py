"""Flag possible duplicate recipes without merging."""

from __future__ import annotations

import re
from difflib import SequenceMatcher


def normalize_title(title: str) -> str:
    t = title.lower()
    t = t.replace("&", " and ")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\b(a|an|the|recipe|moms?|mother|grandma|nana)s?\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def ingredient_set(names: list[str]) -> set[str]:
    out = set()
    for n in names:
        n = re.sub(r"[^a-z0-9\s]", " ", n.lower())
        n = re.sub(r"\b(chopped|diced|minced|sliced|fresh|dried|large|small|medium)\b", " ", n)
        n = re.sub(r"\s+", " ", n).strip()
        if n:
            out.add(n)
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    uni = len(a | b)
    return inter / uni if uni else 0.0


FOOD_HINT = re.compile(
    r"\b(soup|stew|cake|pie|pudding|chicken|salad|casserole|bread|cookie|"
    r"roast|eggplant|pork|beef|tuna|cheese|broccoli|steak|sauce|muffin|"
    r"pancake|chili|lasagna|fudge|brownie|ice cream|piperade|ratatouille|"
    r"zucchini|salmon|meatball|meatloaf|chowder|risotto|frango|pucara)\b",
    re.I,
)


def find_duplicates(records: list[dict]) -> dict[str, list[str]]:
    """records: card_id, title, ingredients(list[str]), note_title"""
    flags: dict[str, list[str]] = {r["card_id"]: [] for r in records}
    norms = [(r, normalize_title(r["title"]), ingredient_set(r.get("ingredients") or [])) for r in records]
    n = len(norms)
    for i in range(n):
        ri, ti, ii = norms[i]
        for j in range(i + 1, n):
            rj, tj, ij = norms[j]
            if ri["card_id"] == rj["card_id"]:
                continue
            tsim = SequenceMatcher(None, ti, tj).ratio() if ti and tj else 0.0
            isim = jaccard(ii, ij)
            foodish = bool(FOOD_HINT.search(ti) and FOOD_HINT.search(tj))
            longish = min(len(ti), len(tj)) >= 12 and min(len(ti.split()), len(tj.split())) >= 2
            same_dish = (
                (tsim >= 0.92 and longish)
                or (tsim >= 0.80 and foodish)
                or (tsim >= 0.55 and isim >= 0.55)
                or (isim >= 0.8 and tsim >= 0.4)
            )
            if not same_dish:
                continue
            link_i = f"[[{rj['note_title']}]]"
            link_j = f"[[{ri['note_title']}]]"
            if link_i not in flags[ri["card_id"]]:
                flags[ri["card_id"]].append(link_i)
            if link_j not in flags[rj["card_id"]]:
                flags[rj["card_id"]].append(link_j)
    return flags
