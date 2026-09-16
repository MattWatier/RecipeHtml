"""Reconstruct a full Kaper recipe from shorthand card OCR.

OCR may be plain text or markdown. Headings (# / ## / **Serves:**) are
metadata, not ingredients. Amounts that live inside directions are pulled
into the ingredient list only when that food is not already listed; the
direction stays a step. A verb line followed by an ingredient list is one
step plus those (new) ingredients.

Timings: every stated bake/simmer/sauté/chill is collected and summed
into `duration` ({n}m). Clock times do not stay in the step note.
Steps with no stated time get a typical kitchen duration. Overnight is
never inferred unless the card says chill/refrigerate overnight.
Kaper YAML cannot contain markdown; titles are short actions and notes
are instruction sentences.
"""

from __future__ import annotations

import re

from parse import (
    AMT_TOKEN,
    Ingredient,
    ParsedRecipe,
    Step,
    UNIT_ALIASES,
    UNIT_RE,
    _norm_ws,
    add_new_ingredients,
    allergens_of,
    classify_course,
    cleanup_ocr,
    clock_minutes_list,
    cuisine_of,
    dedupe_ingredients,
    duration_to_minutes,
    extract_internal_temp_tip,
    extract_oven,
    extract_servings,
    extract_yield,
    format_duration,
    iter_quant_mentions,
    parse_amount,
    pick_title,
    split_amount_unit_name,
    strip_clock_language,
    strip_kaper_markdown,
    strip_whole_ingredient_amounts,
    drop_ingredient_name,
)

JUNK_NAME = re.compile(
    r"^(bowl|pan|pot|skillet|oven|paste|mixture|little|non|dutch|dutch oven|"
    r"large pot.*|few spoon.*|top up|water to cover|cutlets?|"
    r"plate|good|gentle|boil|separate|large|small|medium)$",
    re.I,
)
STEPISH_NAME = re.compile(
    r"^(heat|saute|sauté|saut[eé]|simmer|add|cook|bake|brown|mix|stir|"
    r"combine|when|peel|place|reduce|season|remove|transfer|arrange|"
    r"serve|bring|pour|cover|let)\b",
    re.I,
)
NUMBERED_STEP = re.compile(r"^\d+[\.)]\s")
NEW_STATION = re.compile(
    r"^(bowl of|dip\b|arrange\b|make roux|in a large pot|simmer\b|"
    r"when (?:the )?(?:veg|rabbit|meat))",
    re.I,
)
SKIP_NAME = re.compile(
    r"^(min|mins?|minutes?|hr|hrs?|hours?|sec|seconds?|°|f|c|until|then)$",
    re.I,
)
META_LINE = re.compile(
    r"^\*{0,2}\s*(serves?|servings?|yield|makes?|oven|temp|pan|bake|prep|cook|time)\s*[:*]",
    re.I,
)
MD_H1 = re.compile(r"^#\s+(?P<title>.+?)\s*$")
MD_H2 = re.compile(r"^#{2,6}\s+(?P<head>.+?)\s*$")
BOLD_META = re.compile(
    r"^\*{0,2}(?P<key>Serves?|Servings?|Oven|Pan|Bake|Prep|Cook|Yield|Makes?)\*{0,2}\s*:\s*(?P<val>.+?)\s*$",
    re.I,
)
SECTION_ING = re.compile(r"^(ingredients?|you(?:.ll)? need)$", re.I)
SECTION_STEP = re.compile(
    r"^(instructions?|directions?|method|steps?|preparation|to (?:make|cook))$",
    re.I,
)
INTRO_VERB = re.compile(
    r"^(heat|saute|sauté|saut[eé]|beat|stir|mix|add|fold|pour|bake|cook|"
    r"simmer|boil|blend|whisk|cream|preheat|put|place|layer|cover|serve|"
    r"combine|tear|soak|brown|drain|spread|roll|knead|chill|refrigerate|"
    r"freeze|top|remove|reduce|season|dot|arrange|brush|grease|line|"
    r"marinate|roast|grill|broil|steam|puree|pur[eé]e|process|pulse|"
    r"scald|melt|dissolve|sprinkle|garnish|cool|let|bring|transfer|"
    r"turn|flip|cut|slice|dice|chop|peel|skim|dip|dredge|coat|shape|"
    r"shell|crush|make|soften)\b",
    re.I,
)
BARE_INTRO = re.compile(
    r"^(saute|sauté|saut[eé]|combine(?: as a mixture)?|mix|add(?: to .*)?|"
    r"place in a bowl|when .+ add(?: wine)?|add to cooking pot and soften|"
    r"soften)\s*:?\s*$",
    re.I,
)
PREHEAT = re.compile(r"\bpreheat\b", re.I)
STATED_TIME = re.compile(
    r"(\d+(?:\s*/\s*\d+)?|½|¼|¾)\s*(hours?|hrs?|hr|minutes?|mins?|min)\b",
    re.I,
)
OVERNIGHT = re.compile(r"\bovernight\b", re.I)

# Typical minutes + bucket when the card does not give a time.
# Do not infer overnight.
INFER_RULES: list[tuple[re.Pattern, int, str]] = [
    (re.compile(r"\broux\b", re.I), 5, "cook"),
    (re.compile(r"\bbrown\b", re.I), 10, "cook"),
    (re.compile(r"\b(saute|sauté|soften)\b", re.I), 8, "prep"),
    (re.compile(r"\bsimmer a little\b", re.I), 10, "cook"),
    (re.compile(r"\bsimmer\b", re.I), 90, "cook"),
    (re.compile(r"\b(stew|stock|broth).{0,40}\b(clear|skim)", re.I), 90, "cook"),
    (re.compile(r"\bbake\b.{0,40}\b(chicken|tender|strip|cutlet)", re.I), 20, "cook"),
    (re.compile(r"\boven fried\b", re.I), 20, "cook"),
    (re.compile(r"\b(meatball|bake or cook in sauce)\b", re.I), 25, "cook"),
    (re.compile(r"\bbake\b", re.I), 30, "cook"),
    (re.compile(r"\b(roast|grill|broil)\b", re.I), 40, "cook"),
    (re.compile(r"\b(boil|poach)\b", re.I), 15, "cook"),
    (re.compile(r"\bsteam\b", re.I), 8, "cook"),
    (re.compile(r"\b(chill|refrigerat|until set)\b", re.I), 120, "rest"),
    (re.compile(r"\b(dip|coat|dredge|bread)\b", re.I), 5, "prep"),
    (re.compile(r"\b(mix|combine|shape|stir|whisk|beat)\b", re.I), 3, "prep"),
    (re.compile(r"\b(blend|strain|puree|purée)\b", re.I), 5, "prep"),
    (re.compile(r"\b(arrange|spray|heat skillet)\b", re.I), 2, "prep"),
]

COOK_HINT = re.compile(
    r"\b(bake|simmer|roast|grill|broil|boil|brown|fry|roux|stew|poach|reduce|steam)\b",
    re.I,
)
PREP_HINT = re.compile(
    r"\b(saute|sauté|soften|mix|combine|whisk|stir|dip|coat|shape|mince|"
    r"chop|slice|heat skillet|place in a bowl)\b",
    re.I,
)
REST_HINT = re.compile(r"\b(chill|refrigerat|rest|cool until|until set)\b", re.I)

ING_SCAN = re.compile(
    rf"(?:(?<=^)|(?<=[\s,;+/])|(?<=\bwith\s)|(?<=\badd\s)|(?<=\band\s))"
    rf"(?P<amt>{AMT_TOKEN})\s*"
    rf"(?P<unit>{UNIT_RE})?\b\.?\s*"
    rf"(?:of\s+)?"
    rf"(?P<rest>[A-Za-z][\w'\-]*(?:\s+(?:of\s+)?[A-Za-z][\w'\-]*){0,3})",
    re.I,
)
A_ITEM = re.compile(
    rf"\ba(?:n)?\s+(?:(?P<amt2>{AMT_TOKEN})\s*)?(?:(?P<unit2>{UNIT_RE})\b\.?\s*)?"
    rf"(?:of\s+)?(?P<name>(?:sliced|diced|chopped|minced|crushed)\s+[A-Za-z]+|"
    rf"(?!little|pan|bowl|pot|skillet|dutch)[A-Za-z]+)",
    re.I,
)
COUNT_CLOVES = re.compile(
    rf"(?P<amt>{AMT_TOKEN})\s+(?:cloves?\s+(?:of\s+)?garlic|garlic\s+cloves?)",
    re.I,
)
COUNT_FOOD = re.compile(
    rf"(?P<amt>{AMT_TOKEN})\s+(?P<name>(?:whole\s+)?(?:rabbits?|chickens?|apples?|"
    r"onions?|eggs?|walnuts?|potatoes?|tomatoes?|carrots?|peppers?))\b",
    re.I,
)


def _strip_md(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^\*{1,2}|\*{1,2}$", "", s).strip()
    s = re.sub(r"^#+\s*", "", s).strip()
    return s


def preprocess(ocr_text: str) -> str:
    raw = ocr_text or ""
    raw = re.sub(r"^'''\s*\n?", "", raw)
    raw = re.sub(r"\n?'''\s*$", "", raw)
    raw = re.sub(r"^```[^\n]*\n", "", raw)
    raw = re.sub(r"\n```\s*$", "", raw)
    raw = cleanup_ocr(raw)
    raw = re.sub(r"\b1\s*T\.?\s+colander\b", "1 T coriander", raw, flags=re.I)
    raw = raw.replace("--- BACK ---", "\n")
    lines = []
    for ln in raw.splitlines():
        t = _norm_ws(ln)
        if t == "---":
            lines.append("")
            continue
        lines.append(t)
    return "\n".join(lines)


def extract_markdown_meta(text: str) -> dict:
    meta: dict = {
        "title": "",
        "servings": None,
        "oven": None,
        "bake": None,
        "yield": "",
        "ing_section": [],
        "step_section": [],
        "body_lines": [],
    }
    section = "body"
    for raw_line in text.splitlines():
        line = _norm_ws(raw_line)
        if not line:
            if section == "body":
                meta["body_lines"].append("")
            continue
        h1 = MD_H1.match(line)
        if h1:
            # First H1 is the card title. Later H1s are extra recipes on the
            # same card (e.g. Onion Sauce under Vegetable-Nut Loaf).
            if not meta["title"]:
                meta["title"] = _strip_md(h1.group("title"))
            continue
        h2 = MD_H2.match(line)
        if h2:
            head = _strip_md(h2.group("head"))
            if SECTION_ING.match(head):
                section = "ing"
            elif SECTION_STEP.match(head):
                section = "step"
            else:
                section = "body"
            continue
        bm = BOLD_META.match(line) or META_LINE.match(line) and BOLD_META.match(
            re.sub(r"^\*+", "", line)
        )
        if not bm:
            maybe = re.match(
                r"^\*{0,2}(Serves?|Servings?|Oven|Pan|Bake|Prep|Cook|Yield|Makes?)\*{0,2}\s*:\s*(.+)$",
                line,
                re.I,
            )
            bm = maybe
        if bm:
            key = (bm.group("key") if "key" in bm.re.groupindex else bm.group(1)).lower()
            val = (bm.group("val") if "val" in bm.re.groupindex else bm.group(2)).strip()
            val = _strip_md(val)
            if key.startswith("serve"):
                meta["servings"] = extract_servings(f"serves {val}") or meta["servings"]
            elif key == "oven":
                meta["oven"] = extract_oven(val) or extract_oven(line)
            elif key == "bake":
                mins = sum_stated_minutes(val)
                if mins:
                    meta["bake"] = mins
            elif key in {"yield", "makes"}:
                meta["yield"] = val
            continue
        if section == "ing":
            meta["ing_section"].append(line)
        elif section == "step":
            meta["step_section"].append(line)
        else:
            meta["body_lines"].append(line)
    return meta


def minutes_from_match(num: str, unit: str) -> int:
    unit = unit.lower()
    if num in {"½", "1/2"}:
        n = 0.5
    elif num in {"¼", "1/4"}:
        n = 0.25
    elif num in {"¾", "3/4"}:
        n = 0.75
    else:
        n = parse_amount(num) or 0
    if unit.startswith("h"):
        return int(round(n * 60))
    return int(round(n))


def stated_minutes_list(text: str) -> list[int]:
    return clock_minutes_list(text)


def sum_stated_minutes(text: str) -> int:
    return sum(stated_minutes_list(text))


def _clean_ing_name(name: str) -> str:
    name = _norm_ws(name)
    name = re.sub(r"^[/⁄]\d+\s*", "", name)
    name = re.sub(r"^[½¼¾⅓⅔⅛⅜⅝⅞]\s*", "", name)
    name = re.sub(r"^(of|a|an|the)\s+", "", name, flags=re.I)
    name = re.sub(
        r"\b(cubed and browned|to pieces.*|cutlets?|and crush.*|cook till.*)\b",
        "",
        name,
        flags=re.I,
    )
    name = re.sub(r"\s*[-–]\s*$", "", name)
    name = re.sub(r"\s+", " ", name).strip(" .:-")
    if name.lower() in {"london broil"}:
        return "London broil"
    return name[:80]


def _split_note(rest: str) -> tuple[str, str]:
    rest = rest.strip(" .")
    rest = re.split(r"\b(?:cook till|until|till flour)\b", rest, maxsplit=1, flags=re.I)[0]
    if "," in rest:
        name, note = rest.split(",", 1)
        return _clean_ing_name(name), note.strip(" .")
    m = re.search(
        r"\b(chopped|diced|minced|sliced|crushed|grated|peeled|softened|"
        r"melted|packed|cubed|thinly sliced|optional)\b",
        rest,
        re.I,
    )
    if m and m.start() > 2:
        return _clean_ing_name(rest[: m.start()]), rest[m.start() :].strip(" .,")
    return _clean_ing_name(rest), ""


def make_ing(amt: float, unit: str, rest: str) -> Ingredient | None:
    name, note = _split_note(rest)
    if not name or len(name) < 2 or SKIP_NAME.match(name) or JUNK_NAME.match(name):
        return None
    if drop_ingredient_name(name):
        return None
    if "**" in name or len(name.split()) > 8 or STEPISH_NAME.match(name):
        return None
    if re.match(r"^(hr|hours?|min|minutes?)\b", name, re.I):
        return None
    unit = (unit or "").strip()
    unit = UNIT_ALIASES.get(unit, UNIT_ALIASES.get(unit.lower(), unit.lower().rstrip(".")))
    if unit in {"min", "minute", "minutes", "hr", "hour", "hours"}:
        return None
    return Ingredient(amount=amt, unit=unit, name=name, note=note)


def parse_embedded_ingredients(line: str) -> list[Ingredient]:
    """Pull every quantified ingredient out of a direction or list line."""
    line = _norm_ws(line)
    if not line or META_LINE.match(line) or PREHEAT.search(line) and "°" in line:
        # still allow "2 cups cornflakes" on a non-preheat line
        if PREHEAT.search(line) and not re.search(rf"{AMT_TOKEN}\s*(?:c|cup|T|t|lb)", line, re.I):
            return []
    found: list[Ingredient] = []

    for m in COUNT_CLOVES.finditer(line):
        amt = parse_amount(m.group("amt"))
        if amt:
            found.append(Ingredient(amount=amt, unit="clove", name="garlic"))

    for m in COUNT_FOOD.finditer(line):
        amt = parse_amount(m.group("amt"))
        name = _clean_ing_name(m.group("name"))
        if amt and name and not SKIP_NAME.match(name):
            # walnuts: unit empty, count
            unit = ""
            found.append(Ingredient(amount=amt, unit=unit, name=name))

    for m in ING_SCAN.finditer(line):
        amt = parse_amount(m.group("amt"))
        if amt is None:
            continue
        rest = (m.group("rest") or "").strip()
        # "10min" leftover
        if re.match(r"^(min|minutes?|hr|hours?)\b", rest, re.I):
            continue
        ing = make_ing(amt, m.group("unit") or "", rest)
        if ing:
            found.append(ing)

    for m in A_ITEM.finditer(line):
        raw_amt = m.groupdict().get("amt2")
        amt = parse_amount(raw_amt) if raw_amt else 1.0
        if amt is None:
            amt = 1.0
        ing = make_ing(amt, m.groupdict().get("unit2") or "", m.group("name") or "")
        if ing:
            found.append(ing)

    # Whole-line parser as fallback — not for direction sentences.
    if not INTRO_VERB.search(line) and not NUMBERED_STEP.match(line):
        one = split_amount_unit_name(line)
        if one:
            amt, unit, name, note = one
            if name and not SKIP_NAME.match(name) and not STEPISH_NAME.match(name):
                found.append(Ingredient(amount=amt, unit=unit, name=name, note=note))

    return dedupe_ingredients(found)


def looks_like_ingredient_line(line: str) -> bool:
    if not line or META_LINE.match(line):
        return False
    if BARE_INTRO.match(line):
        return False
    if PREHEAT.search(line):
        return False
    ings = parse_embedded_ingredients(line)
    if not ings:
        return False
    # A long narrative with one amount is still a step, but also an ingredient line
    if INTRO_VERB.search(line) and len(line) > 48:
        return False
    return True


def expand_shorthand_step(intro: str, ing_lines: list[str]) -> str:
    names = []
    for ln in ing_lines:
        for it in parse_embedded_ingredients(ln):
            names.append(it.name)
    intro_c = _norm_ws(intro).rstrip(":")
    if not names:
        return intro_c
    joined = ", ".join(names)
    if BARE_INTRO.match(intro_c) or len(intro_c.split()) <= 8:
        low = intro_c.lower()
        if "when" in low and "add" in low:
            return f"{intro_c} {joined}"
        if re.match(r"^saute", low) or re.match(r"^sauté", low):
            return f"Sauté {joined} until soft"
        if "soften" in low and "when" not in low:
            return f"Add to the pot and soften {joined}"
        if "combine" in low:
            return f"Combine {joined}"
        if "place in a bowl" in low:
            return f"Place {joined} in a bowl"
        return f"{intro_c} {joined}".strip()
    return f"{intro_c}: {joined}"


def infer_timing(title: str, note: str | None = None) -> tuple[int, str, bool]:
    """Return (minutes, bucket, inferred)."""
    blob = f"{title} {note or ''}".strip()
    stated = stated_minutes_list(blob)
    if stated:
        return sum(stated), _bucket_for(title, note), False
    if PREHEAT.search(blob):
        return 0, "prep", False
    for pat, mins, bucket in INFER_RULES:
        if pat.search(blob):
            return mins, bucket, True
    if COOK_HINT.search(blob):
        return 10, "cook", True
    return 0, "prep", False


def _bucket_for(title: str, note: str | None = None) -> str:
    blob = f"{title} {note or ''}"
    if REST_HINT.search(blob):
        return "rest"
    if COOK_HINT.search(blob):
        return "cook"
    if PREP_HINT.search(blob):
        return "prep"
    if PREHEAT.search(blob):
        return "prep"
    return "prep"


def finish_step(title: str) -> Step:
    title = _norm_ws(title)
    mins, _bucket, inferred = infer_timing(title)
    duration = format_duration(mins) if mins else None
    note = "typical" if inferred and mins else None
    return Step(title=title[:240], duration=duration, note=note)


STEP_NUM = re.compile(r"^(?:step\s+)?(\d+)[\.)]\s+", re.I)
LOC_PREFIX = re.compile(
    r"^(?:(?:then|next|now)\s+)?"
    r"(?:(?:in|into|on|onto|using|with)\s+(?:a|an|the)\s+[^,.]{2,48},\s*)",
    re.I,
)
TITLE_ACTION = re.compile(
    r"\b(heat|saute|sauté|saut[eé]|beat|stir|mix|add|fold|pour|bake|cook|"
    r"simmer|boil|blend|whisk|cream|preheat|put|place|layer|cover|serve|"
    r"combine|tear|soak|brown|drain|spread|roll|knead|chill|refrigerate|"
    r"freeze|top|remove|reduce|season|dot|arrange|brush|grease|line|"
    r"marinate|roast|grill|broil|steam|puree|pur[eé]e|process|pulse|"
    r"scald|melt|dissolve|sprinkle|garnish|cool|let|bring|transfer|"
    r"turn|flip|cut|slice|dice|chop|peel|skim|dip|dredge|coat|shape|"
    r"shell|crush|make|soften|wash|trim|toss|reheat|divide|pat|drop|"
    r"fill|proof|rise|sear|braise|deglaze|uncover|butter|oil|poach)\b",
    re.I,
)
PLACEHOLDER_NOTE = re.compile(r"^(typical|inferred|n/?a)?$", re.I)


def _pretty_verb(raw: str) -> str:
    low = (raw or "").lower()
    special = {
        "saute": "Sauté",
        "sauté": "Sauté",
        "saut": "Sauté",
        "puree": "Puree",
        "purée": "Purée",
    }
    if low in special:
        return special[low]
    if not raw:
        return "Prepare"
    return raw[0].upper() + raw[1:].lower()


def _title_is_short(title: str) -> bool:
    body = STEP_NUM.sub("", title or "").strip()
    if re.search(r"\b(min|minutes?|hours?|hrs?|typical)\b", body, re.I):
        return False
    if any(ch in body for ch in "*`#_"):
        return False
    if re.search(r"\d+\s*[-–]\s*\d+", body):
        return False
    return 0 < len(body.split()) <= 5


def _cap_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip(" ."))
    if not text:
        return ""
    if text[0].islower():
        text = text[0].upper() + text[1:]
    if not re.search(r"[.!?]$", text):
        text = text + "."
    return text


def _action_phrase(instruction: str, number: str | None) -> str:
    body = instruction or ""
    if number:
        body = STEP_NUM.sub("", body, count=1)
    body = LOC_PREFIX.sub("", body).strip()
    if re.match(r"season to taste\b", body, re.I):
        action = "Season to taste"
    elif re.match(r"variation\b", body, re.I):
        action = "Variation"
    elif re.match(r"filling\b", body, re.I):
        action = "Filling"
    elif re.search(r"\bcombine\b", instruction, re.I) and re.search(r"\bgarnish\b", instruction, re.I):
        action = "Combine & Garnish"
    else:
        m = TITLE_ACTION.search(body) or TITLE_ACTION.search(instruction or "")
        action = _pretty_verb(m.group(1)) if m else "Prepare"
        if action == "Prepare":
            words = [w for w in re.findall(r"[A-Za-z]+", body)[:3]]
            if words:
                action = " ".join(w.capitalize() if i == 0 else w.lower() for i, w in enumerate(words))
    if number:
        return f"{number}. {action}"
    return action


def _keep_amount_keys(steps: list[Step]) -> set[str]:
    counts: dict[str, int] = {}
    for s in steps:
        blob = f"{s.title or ''} {s.note or ''}"
        seen = set()
        for ment in iter_quant_mentions(strip_kaper_markdown(blob)):
            key = ment.get("key") or ""
            if not key or key in seen:
                continue
            seen.add(key)
            counts[key] = counts.get(key, 0) + 1
    return {k for k, n in counts.items() if n >= 2}


def shape_step(step: Step, *, keep_keys: set[str] | None = None, infer: bool = True) -> Step:
    """Split short title vs instruction note; clock times live in duration only."""
    keep_keys = keep_keys or set()
    raw_title = strip_kaper_markdown(step.title or "")
    raw_note = strip_kaper_markdown(step.note or "")
    raw_tip = strip_kaper_markdown(step.tip or "") if step.tip else ""
    if PLACEHOLDER_NOTE.match(raw_note.strip()):
        raw_note = ""

    num_m = STEP_NUM.match(raw_title)
    number = num_m.group(1) if num_m else None
    body_only = STEP_NUM.sub("", raw_title).strip()
    body_no_clock = strip_clock_language(body_only)
    short_title = _title_is_short(raw_title) or _title_is_short(body_no_clock)
    if short_title:
        instruction = raw_note or body_no_clock
        title_seed = body_no_clock or raw_title
    else:
        instruction = raw_title
        if raw_note:
            instruction = f"{instruction.rstrip('.')} {raw_note}"
        title_seed = instruction

    clock_from_text = sum(stated_minutes_list(f"{raw_title} {raw_note}"))
    existing_m = duration_to_minutes(step.duration or "")
    if clock_from_text:
        mins = clock_from_text
    elif existing_m:
        mins = existing_m
    elif infer:
        mins, _b, _inf = infer_timing(title_seed, raw_note)
    else:
        mins = 0
    duration = format_duration(mins) if mins else None

    instruction = strip_clock_language(instruction)
    instruction = STEP_NUM.sub("", instruction, count=1).strip()
    instruction, tip_from_text = extract_internal_temp_tip(instruction)
    instruction = strip_whole_ingredient_amounts(instruction, keep_keys=keep_keys)
    instruction = re.sub(
        r",\s*(sliced|chopped|minced|diced|grated|peeled)\b", "", instruction, flags=re.I
    )
    instruction = re.sub(r"\s+,", ",", instruction)
    instruction = re.sub(r"\s+", " ", instruction).strip(" ,;.")
    instruction = re.sub(r"\babout\s+(?=[A-Za-z])", "", instruction, flags=re.I)
    instruction = re.sub(
        r"\.\s+(Cook|Bake|Simmer|Mix|Heat|Stir)\.?$", ".", instruction, flags=re.I
    )
    instruction = re.sub(r"[:.]\s*non\.?$", "", instruction, flags=re.I)
    instruction = re.sub(r",\s+or until", " until", instruction, flags=re.I)
    instruction = instruction.strip(" ,;.")

    title = _action_phrase(title_seed, number)

    title = strip_kaper_markdown(title)
    title = re.sub(r"\s+", " ", title).strip()[:80]
    num_keep = STEP_NUM.match(title)
    title_body = STEP_NUM.sub("", title).strip()
    title_body = strip_clock_language(title_body)
    title_body = re.sub(r"\s+", " ", title_body).strip(" .")
    if num_keep:
        title = f"{num_keep.group(1)}. {title_body}".strip()
    else:
        title = title_body
    title = title[:80]

    action_word = STEP_NUM.sub("", title).strip()
    first = action_word.split("&")[0].strip()
    if instruction and not TITLE_ACTION.match(instruction):
        if re.match(r"season to taste$", title, re.I):
            pass
        elif re.match(r"^(until|the |a |an )", instruction, re.I):
            instruction = f"{first} {instruction}".strip()
        elif instruction[:1].islower():
            instruction = f"{first} {instruction}".strip()
    if first and instruction:
        instruction = re.sub(
            rf"^{re.escape(first)}\s+{re.escape(first)}\b",
            first,
            instruction,
            flags=re.I,
        )

    note = _cap_sentence(instruction) if instruction else None
    if note and note.lower().rstrip(".") == title.lower().rstrip("."):
        note = note

    tip = raw_tip or tip_from_text
    if tip:
        tip = strip_kaper_markdown(tip)
        tip = re.sub(r"\s+", " ", tip).strip()

    return Step(title=title or "Prepare", duration=duration, note=note or None, tip=tip or None)


UNNUMBERED_TITLE = re.compile(
    r"^(season to taste|serve|serves|serving|variation|tip)$",
    re.I,
)


def _number_step_titles(steps: list[Step]) -> list[Step]:
    n = 1
    out: list[Step] = []
    for s in steps:
        body = re.sub(r"^\d+\.\s*", "", (s.title or "").strip())
        if UNNUMBERED_TITLE.match(body):
            s.title = body[0].upper() + body[1:] if body else s.title
        else:
            s.title = f"{n}. {body}".strip()
            n += 1
        out.append(s)
    return out


def shape_steps(steps: list[Step], *, infer: bool = True) -> list[Step]:
    keep = _keep_amount_keys(steps)
    shaped = [shape_step(s, keep_keys=keep, infer=infer) for s in steps]
    return _number_step_titles(shaped)


def walk_body(lines: list[str]) -> tuple[list[Ingredient], list[Step]]:
    ingredients: list[Ingredient] = []
    steps: list[Step] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line:
            i += 1
            continue
        if MD_H1.match(line) or MD_H2.match(line) or BOLD_META.match(line):
            i += 1
            continue
        if PREHEAT.search(line) and not parse_embedded_ingredients(line):
            steps.append(finish_step(line))
            i += 1
            continue

        if BARE_INTRO.match(line) or (
            INTRO_VERB.search(line)
            and len(line) < 60
            and not stated_minutes_list(line)
            and i + 1 < n
            and looks_like_ingredient_line(lines[i + 1])
        ):
            intro = line
            collected: list[str] = []
            j = i + 1
            while j < n and lines[j] and looks_like_ingredient_line(lines[j]) and not NEW_STATION.match(lines[j]):
                collected.append(lines[j])
                j += 1
            if collected:
                for ln in collected:
                    ingredients.extend(parse_embedded_ingredients(ln))
                ingredients.extend(parse_embedded_ingredients(intro))
                steps.append(finish_step(expand_shorthand_step(intro, collected)))
                i = j
                continue

        if looks_like_ingredient_line(line) and re.search(r"\bbrowned\b", line, re.I):
            ingredients.extend(parse_embedded_ingredients(line))
            names = [it.name for it in parse_embedded_ingredients(line)]
            label = ", ".join(names) or line
            steps.append(finish_step(f"Brown {label}"))
            i += 1
            continue

        ings = parse_embedded_ingredients(line)
        ingredients.extend(ings)
        if INTRO_VERB.search(line) or stated_minutes_list(line) or len(line) > 40:
            # Follow-on ingredient list after a verb sentence
            collected = []
            j = i + 1
            if INTRO_VERB.search(line) and not looks_like_ingredient_line(line):
                while j < n and lines[j] and looks_like_ingredient_line(lines[j]) and not NEW_STATION.match(lines[j]):
                    collected.append(lines[j])
                    j += 1
            if collected:
                for ln in collected:
                    ingredients.extend(parse_embedded_ingredients(ln))
                steps.append(finish_step(expand_shorthand_step(line, collected)))
                i = j
                continue
            steps.append(finish_step(line))
        i += 1
    return dedupe_ingredients(ingredients), _dedupe_steps(steps)


def _dedupe_steps(steps: list[Step]) -> list[Step]:
    out: list[Step] = []
    seen = set()
    for s in steps:
        key = re.sub(r"\W+", "", s.title.lower())[:80]
        if key in seen:
            # Keep repeated timed cook segments (bake 10 + bake 10 + bake 10).
            if s.duration and COOK_HINT.search(s.title):
                out.append(s)
            continue
        seen.add(key)
        out.append(s)
    return out


def apply_bake_meta(steps: list[Step], bake_mins: int | None) -> list[Step]:
    if not bake_mins:
        return steps
    has_bake = any(re.search(r"\bbake\b", f"{s.title} {s.note or ''}", re.I) for s in steps)
    if has_bake:
        for s in steps:
            blob = f"{s.title} {s.note or ''}"
            if re.search(r"\bbake\b", blob, re.I) and not stated_minutes_list(blob):
                s.duration = format_duration(bake_mins)
        return steps
    steps.append(Step(title="Bake", duration=format_duration(bake_mins), note="Bake until done."))
    return steps


def sum_buckets(steps: list[Step]) -> tuple[str, str, str]:
    prep = cook = rest = 0
    for s in steps:
        mins = duration_to_minutes(s.duration or "")
        if not mins:
            continue
        bucket = _bucket_for(s.title, s.note)
        if bucket == "cook":
            cook += mins
        elif bucket == "rest":
            rest += mins
        else:
            prep += mins
    total = prep + cook + rest
    return (
        format_duration(prep),
        format_duration(cook),
        format_duration(total) if total else "",
    )


def reconstruct_recipe(
    ocr_text: str, card_id: str, ocr_lines: list[dict] | None = None
) -> ParsedRecipe:
    raw = preprocess(ocr_text or "")
    ocr_ok = len(re.findall(r"[A-Za-z]{3,}", raw)) >= 8
    meta = extract_markdown_meta(raw)
    body_lines = [_norm_ws(x) for x in (meta["body_lines"] or raw.splitlines())]

    section_ings: list[Ingredient] = []
    steps: list[Step] = []

    if meta["ing_section"]:
        for ln in meta["ing_section"]:
            section_ings.extend(parse_embedded_ingredients(ln.lstrip("-* ")))
        section_ings = dedupe_ingredients(section_ings)

    pulled: list[Ingredient] = []
    if meta["step_section"]:
        more_i, more_s = walk_body(meta["step_section"])
        pulled.extend(more_i)
        steps.extend(more_s)

    body_i, body_s = walk_body(body_lines)
    pulled.extend(body_i)
    steps.extend(body_s)

    if section_ings:
        ingredients = add_new_ingredients(section_ings, pulled)
    else:
        ingredients = dedupe_ingredients(pulled)
    ingredients = [it for it in ingredients if not drop_ingredient_name(it.name)]
    steps = _dedupe_steps(steps)
    steps = apply_bake_meta(steps, meta.get("bake"))
    steps = shape_steps(steps, infer=True)

    if not steps:
        steps = [Step(title="Follow the source card", note="Steps were not recovered.")]

    title = strip_kaper_markdown(meta.get("title") or "")
    if not title or title.lower().startswith("untitled"):
        title = pick_title(
            [ln for ln in body_lines if ln], ocr_lines or [], card_id
        )
    title = strip_kaper_markdown(title)

    servings = meta.get("servings") or extract_servings(raw) or 4
    oven = meta.get("oven") or extract_oven(raw)
    yield_text = strip_kaper_markdown(meta.get("yield") or extract_yield(raw) or "")

    for it in ingredients:
        it.name = strip_kaper_markdown(it.name)
        it.note = strip_kaper_markdown(it.note or "")
        it.unit = strip_kaper_markdown(it.unit or "")

    groups: dict[str, list[Ingredient]] = {"main": ingredients or []}
    course, extra_tags = classify_course(title, raw, ingredients=groups, steps=steps)
    tags = []
    era = "1980s" if "1980s" in card_id else "1990s"
    tags.append(era)
    tags.extend(extra_tags)
    if oven:
        tags.append("baked")
    tags = list(dict.fromkeys(strip_kaper_markdown(t) for t in tags if t))

    n_ing = len(ingredients)
    if n_ing <= 6 and len(steps) <= 5:
        difficulty = "easy"
    elif n_ing >= 14 or len(steps) >= 10:
        difficulty = "hard"
    else:
        difficulty = "medium"

    prep_time, cook_time, total_time = sum_buckets(steps)

    return ParsedRecipe(
        title=title,
        servings=int(servings),
        yield_text=yield_text,
        prep_time=prep_time,
        cook_time=cook_time,
        total_time=total_time,
        oven_temp_f=oven,
        difficulty=difficulty,
        course=course,
        cuisine=cuisine_of(title, raw),
        tags=tags,
        ingredients=groups,
        steps=steps,
        allergens=allergens_of([i.name for i in ingredients] + [title]),
        ocr_ok=ocr_ok,
    )
