"""Heuristics to turn OCR text into a best-effort Kaper recipe."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

COURSES = ("breakfast", "main", "side", "drink", "dessert", "condiment")

COURSE_FOLDER = {
    "breakfast": "Breakfast",
    "main": "Mains",
    "side": "Sides",
    "drink": "Drinks",
    "dessert": "Desserts",
    "condiment": "Condiments",
    # older notes
    "lunch": "Mains",
    "dinner": "Mains",
    "snack": "Sides",
}

UNICODE_FRAC = {
    "½": 0.5,
    "¼": 0.25,
    "¾": 0.75,
    "⅓": 1 / 3,
    "⅔": 2 / 3,
    "⅛": 0.125,
    "⅜": 0.375,
    "⅝": 0.625,
    "⅞": 0.875,
}

# ASCII before NFKC so ½ / 1½ do not become 1⁄2 / 11⁄2 (U+2044).
# Kaper amounts: .cursor/rules/recipe-vault.mdc
UNICODE_FRAC_ASCII = {
    "½": "1/2",
    "¼": "1/4",
    "¾": "3/4",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}

UNIT_ALIASES = {
    "c": "cup",
    "c.": "cup",
    "cup": "cup",
    "cups": "cup",
    "t": "tsp",
    "t.": "tsp",
    "tsp": "tsp",
    "tsp.": "tsp",
    "tsps": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "T": "tbsp",
    "T.": "tbsp",
    "Tb": "tbsp",
    "Tb.": "tbsp",
    "tb": "tbsp",
    "tbsp": "tbsp",
    "tbsp.": "tbsp",
    "tbs": "tbsp",
    "tbs.": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "oz": "oz",
    "oz.": "oz",
    "ounce": "oz",
    "ounces": "oz",
    "lb": "lb",
    "lb.": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "#": "lb",  # recipe-card pound sign after an amount, not a heading
    "g": "g",
    "g.": "g",
    "gm": "g",
    "gm.": "g",
    "gms": "g",
    "gram": "g",
    "grams": "g",
    "pkg": "pkg",
    "pkg.": "pkg",
    "package": "pkg",
    "can": "can",
    "cans": "can",
    "clove": "clove",
    "cloves": "clove",
    "slice": "slice",
    "slices": "slice",
    "qt": "qt",
    "qt.": "qt",
    "quart": "qt",
    "quarts": "qt",
    "pt": "pt",
    "pint": "pt",
    "pints": "pt",
    "stick": "stick",
    "sticks": "stick",
    "bunch": "bunch",
    "head": "head",
    "pinch": "pinch",
    "dash": "dash",
    "ml": "ml",
    "l": "l",
    "gal": "gal",
    "box": "box",
    "bag": "bag",
    "jar": "jar",
    "bottle": "bottle",
    "envelope": "envelope",
    "env": "envelope",
    "sheet": "sheet",
    "leaf": "leaf",
    "leaves": "leaf",
    # Size words after an amount are Kaper units, not part of name.
    # See reconstruction contract: .cursor/rules/recipe-vault.mdc
    "small": "small",
    "medium": "medium",
    "large": "large",
    "whole": "whole",
}

UNIT_RE = "|".join(
    sorted((re.escape(k) for k in UNIT_ALIASES), key=len, reverse=True)
)

STEP_START = re.compile(
    r"^(heat|saute|sauté|saut[eé]|beat|stir|mix|add|fold|pour|bake|cook|"
    r"simmer|boil|blend|whisk|cream|preheat|put|place|layer|cover|serve|"
    r"combine|tear|soak|brown|drain|spread|roll|knead|chill|refrigerate|"
    r"freeze|top|remove|reduce|season|dot|arrange|brush|grease|line|"
    r"marinate|roast|grill|broil|steam|puree|pur[eé]e|process|pulse|"
    r"scald|melt|dissolve|sprinkle|garnish|cool|let|bring|transfer|"
    r"turn|flip|cut|slice|dice|chop|peel|skim|cover.?cook)\b",
    re.I,
)

TITLE_SKIP = re.compile(
    r"^(serves?|servings?|oven|preheat|yield|makes?|temp)\b",
    re.I,
)

GROUP_HEADERS = re.compile(
    r"^(top(?:ping| w\.?| with)?|sauce|filling|crust|frosting|glaze|"
    r"dressing|marinade|rub|dough|batter|garnish|syrup)\b[:\s]*$",
    re.I,
)

BREAKFAST = re.compile(
    r"\b(pancakes?|waffles?|french toast|oatmeal|granola|muffins?|scones?|"
    r"biscuits?|omelets?|omelettes?|breakfast|brunch|coffee cakes?|"
    r"hash browns?|cereals?|bagels?|scrambled|quiche|frittatas?|"
    r"crepes?|crêpes?|sticky buns?|cinnamon rolls?|"
    r"danishes\b|danish pastry|cheese danish|grits|porridge|"
    r"(?:fried|poached|scrambled|boiled|baked)\s+eggs?)\b",
    re.I,
)

# True dessert forms only. Chocolate, nuts, fruit, caramel ≠ dessert.
DESSERT_FORM = re.compile(
    r"\b(cakes?|cookies?|pies?|puddings?|brownies?|fudge|frosting|icing|"
    r"cobblers?|crisps?|custards?|cheesecakes?|tortes?|tarts?|trifles?|"
    r"mousses?|sorbets?|ice creams?|cand(?:y|ies)|shortbread|bar cookies?|"
    r"desserts?|gingerbread|snickerdoodles?|macaroons?|meringues?|"
    r"eclairs?|éclairs?|flans?|baklava|strudels?|turnovers?|doughnuts?|"
    r"donuts?|cupcakes?|chiffons?|blondies?|whoopee pies?|drops)\b",
    re.I,
)
SAVORY_PIE = re.compile(
    r"\b((?:pot|shepherd(?:'s)?|cottage|vegetable|meat|chicken|turkey)\s*pies?"
    r"|pizza|empanadas?|samosas?)\b",
    re.I,
)
SAVORY_CAKE = re.compile(
    r"\b(crab|fish|potato|cod|salmon|tuna|corn|codfish)\s+cakes?\b",
    re.I,
)
SAVORY_PUDDING = re.compile(
    r"\b(?:fish|meat|corn|yorkshire)\s+puddings?\b",
    re.I,
)
DRINK = re.compile(
    r"\b(punch|cocktails?|smoothies?|nogs?|eggnog|lemonade|sangria|"
    r"spritzer|beverages?|drinks?|grog|cider(?! vinegar)|shakes?|"
    r"martinis?|daiquiris?|mocktails?)\b",
    re.I,
)
DRINK_TITLE = re.compile(
    r"\b(tea|coffee|cocoa|hot chocolate)\b",
    re.I,
)
CONDIMENT = re.compile(
    r"\b(vinaigrettes?|jams?|jell(?:y|ies)|preserves?|relish(?:es)?|"
    r"chutneys?|pesto|marinades?|\brubs?\b|salsas?|ketchup|"
    r"mayonnaise|aioli|pickles?|brine|mustards?|dressings?)\b",
    re.I,
)
SAUCE_TITLE = re.compile(
    r"(?:^|[\s:\-])((?:[A-Za-z]+\s+)?(?:sauce|gravy|dressing)s?)$",
    re.I,
)
# Snacks and small bites file as sides.
SMALL_BITE = re.compile(
    r"\b(dips?|snacks?|popcorn|trail mix|appetizers?|nibbles?|"
    r"chex mix|party mix|crackers?|chips?|stuffed mushrooms?|"
    r"deviled eggs?|crostini|bruschetta|canapés?|canapes?|\bbites?\b)\b",
    re.I,
)
SALAD = re.compile(
    r"\b(salads?|slaws?|coleslaws?|tabbouleh|tabouleh|tabouli)\b",
    re.I,
)
PROTEIN_FOOD = re.compile(
    r"\b("
    r"chicken|pollo|poulet|fowl|hen|capon|beef|pork|lamb|turkey|tacchino|"
    r"veal|duck|goose|rabbit|rabit|meats?\b|"
    r"ham\b|bacon|pancetta|prosciutto|salami|pepperoni|sausage|"
    r"hamburger|ground round|ground chuck|ground meat|meatballs?|"
    r"meat\s*loafs?|brisket|tenderloin|cutlets?|steaks?|chops?\b|"
    r"spareribs?|spare ribs?|short ribs?|london broil|stew meat|"
    r"gulyas|goulash|"
    r"salmon|tuna|cod\b|halibut|trout|tilapia|swordfish|"
    r"shrimp|prawns?|scallops?|lobster|crab|clams?|mussels?|"
    r"oysters?|anchov(?:y|ies)|sole\b|flounder|haddock|\bfish\b|"
    r"frango|tofu|tempeh"
    r")\b",
    re.I,
)
FLAVOR_PROTEIN = re.compile(r"\b(bacon|pancetta|anchov(?:y|ies)|prosciutto)\b", re.I)
BROTH_FLAVOR = re.compile(r"\b(?:chicken|beef|fish|bone|vegetable)\s+(?:broth|stock|base)\b", re.I)
PASTA_AS_MAIN = re.compile(
    r"\b(carbonara|bolognese|ragu|ragù|lasagna|lasagne|manicotti|"
    r"stroganoff|paella|jambalaya|gumbo|enchiladas?|tamales?|"
    r"feijoada|chil(?:i|e)\b)\b",
    re.I,
)
SMALL_UNITS = {"tsp", "tbsp", "t", "t.", "T", "T.", "pinch", "dash"}

CUISINE_HINTS = [
    (r"\b(portuguese|frango|pucara|pucára)\b", "Portuguese"),
    (r"\b(italian|lasagna|lasagne|marinara|parmesan|risotto|pesto|minestrone)\b", "Italian"),
    (r"\b(mexican|taco|enchilada|tamale|salsa|burrito|quesadilla)\b", "Mexican"),
    (r"\b(french|coq au vin|bechamel|béchamel|crepe|crêpe|quiche)\b", "French"),
    (r"\b(chinese|stir.?fry|lo mein|szechuan|szechwan|soy sauce)\b", "Chinese"),
    (r"\b(indian|curry|masala|tandoori|dal\b)\b", "Indian"),
    (r"\b(greek|feta|tzatziki|spanakopita|gyro)\b", "Greek"),
    (r"\b(german|sauerkraut|schnitzel|strudel)\b", "German"),
    (r"\b(japanese|teriyaki|sushi|miso|tempura)\b", "Japanese"),
    (r"\b(thai|pad thai|coconut milk)\b", "Thai"),
    (r"\b(cajun|creole|gumbo|jambalaya)\b", "Cajun"),
]

ALLERGEN_MAP = [
    ("dairy", re.compile(r"\b(milk|butter|cream|cheese|cheddar|parmesan|mozzarella|yogurt|sour cream|half.?and.?half|whipping cream|margarine|whey)\b", re.I)),
    ("egg", re.compile(r"\b(eggs?|egg yolk|egg white)\b", re.I)),
    ("wheat", re.compile(r"\b(flour|wheat|bread|crumbs?|pasta|noodle|spaghetti|cracker|tortilla|biscuit|pie crust|dough)\b", re.I)),
    ("soy", re.compile(r"\b(soy|soya|tofu|edamame|soy sauce)\b", re.I)),
    ("peanut", re.compile(r"\b(peanut)\b", re.I)),
    ("tree-nut", re.compile(r"\b(almond|walnut|pecan|cashew|hazelnut|pistachio|macadamia|nut\b)\b", re.I)),
    ("fish", re.compile(r"\b(tuna|salmon|cod|anchov|fish|sardine|haddock|tilapia)\b", re.I)),
    ("shellfish", re.compile(r"\b(shrimp|crab|lobster|scallop|clam|oyster|mussel|crawfish|crayfish)\b", re.I)),
    ("sesame", re.compile(r"\b(sesame|tahini)\b", re.I)),
    ("mustard", re.compile(r"\b(mustard)\b", re.I)),
]


@dataclass
class Ingredient:
    amount: float
    unit: str
    name: str
    note: str = ""


# Food-name identity when merging Ingredients-section vs direction pulls.
# Ignore amount/unit/prep note; tolerate minor OCR spelling.
_FOOD_FILLER = {
    "fresh", "dried", "dry", "ground", "kosher", "sea", "table", "coarse",
    "fine", "the", "a", "an", "of", "and", "or", "optional", "unsalted",
    "salted", "extra", "virgin", "to", "for", "with", "into", "some",
}
_FOOD_PREP = {
    "chopped", "diced", "minced", "sliced", "crushed", "grated", "peeled",
    "softened", "melted", "packed", "cubed", "halved", "quartered",
    "wedged", "smashed", "julienned", "thin", "thinly", "rough", "roughly",
    "finely", "whole",
}
_FOOD_IRREGULAR = {
    "tomatoes": "tomato",
    "potatoes": "potato",
    "leaves": "leaf",
    "loaves": "loaf",
    "berries": "berry",
    "cloves": "clove",
    "halves": "half",
}


def _singular_food(word: str) -> str:
    w = word.lower()
    if w in _FOOD_IRREGULAR:
        return _FOOD_IRREGULAR[w]
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("oes") and len(w) > 4:
        return w[:-2]
    if w.endswith("sses") or w.endswith("ss"):
        return w
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def food_name_key(name: str) -> str:
    raw = (name or "").lower()
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    toks = []
    for t in raw.split():
        if t in _FOOD_FILLER or t in _FOOD_PREP:
            continue
        toks.append(_singular_food(t))
    return " ".join(toks)


def _is_subseq(short: list[str], long: list[str]) -> bool:
    if not short:
        return False
    it = iter(long)
    return all(tok in it for tok in short)


def food_names_match(a: str, b: str) -> bool:
    ka, kb = food_name_key(a), food_name_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    ta, tb = ka.split(), kb.split()
    last_a, last_b = ta[-1], tb[-1]
    last_ok = last_a == last_b
    if not last_ok and min(len(last_a), len(last_b)) >= 4:
        last_ok = SequenceMatcher(None, last_a, last_b).ratio() >= 0.8
    if not last_ok:
        return False
    if _is_subseq(ta, tb) or _is_subseq(tb, ta):
        return True
    if len(ta) == 1 and len(tb) == 1:
        return SequenceMatcher(None, ka, kb).ratio() >= 0.8
    return False


def _has_amount(it: Ingredient) -> bool:
    try:
        return float(it.amount or 0) > 0
    except (TypeError, ValueError):
        return False


def merge_ingredient(keep: Ingredient, extra: Ingredient) -> Ingredient:
    """Prefer keep (Ingredients-section). If only extra is quantified, take that amount."""
    amt, unit = keep.amount, keep.unit
    if not _has_amount(keep) and _has_amount(extra):
        amt = extra.amount
        unit = extra.unit or keep.unit
    elif _has_amount(keep) and _has_amount(extra) and not (keep.unit or "").strip() and extra.unit:
        unit = extra.unit
    return Ingredient(
        amount=amt,
        unit=unit,
        name=keep.name or extra.name,
        note=keep.note or extra.note,
    )


def find_matching_ingredient(items: list[Ingredient], cand: Ingredient) -> int:
    for i, it in enumerate(items):
        if food_names_match(it.name, cand.name):
            return i
    return -1


def dedupe_ingredients(items: list[Ingredient]) -> list[Ingredient]:
    """First mention wins (Ingredients section). Keep the richer quantified amount."""
    out: list[Ingredient] = []
    for it in items:
        if not (it.name or "").strip():
            continue
        idx = find_matching_ingredient(out, it)
        if idx < 0:
            out.append(it)
        else:
            out[idx] = merge_ingredient(out[idx], it)
    return out


def add_new_ingredients(existing: list[Ingredient], incoming: list[Ingredient]) -> list[Ingredient]:
    """Pull from directions only when the food is not already listed."""
    return dedupe_ingredients(list(existing) + list(incoming))


@dataclass
class Step:
    title: str
    duration: str | None = None
    note: str | None = None
    tip: str | None = None


@dataclass
class ParsedRecipe:
    title: str
    servings: int
    yield_text: str
    prep_time: str
    cook_time: str
    total_time: str
    oven_temp_f: int | None
    difficulty: str
    course: str
    cuisine: str
    tags: list[str]
    ingredients: dict[str, list[Ingredient]] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    allergens: list[str] = field(default_factory=list)
    ocr_ok: bool = True


def _vulgar_to_ascii(s: str) -> str:
    glyphs = "".join(UNICODE_FRAC_ASCII)
    s = re.sub(
        rf"(\d+)([{glyphs}])",
        lambda m: f"{m.group(1)} {UNICODE_FRAC_ASCII[m.group(2)]}",
        s,
    )
    for glyph, ascii_frac in UNICODE_FRAC_ASCII.items():
        s = s.replace(glyph, ascii_frac)
    return s.replace("\u2044", "/")


def _norm_ws(s: str) -> str:
    # Convert vulgar fractions before NFKC (½ → 1/2, 1½ → 1 1/2).
    s = _vulgar_to_ascii(s)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u2044", "/")
    # Recipe-card pound sign after a number (2# / 2 #) → lb. Headings are full-line #.
    # Contract: .cursor/rules/recipe-vault.mdc
    s = re.sub(r"(?<=\d)\s*#\s*", " lb ", s)
    s = s.replace("\u2014", "-").replace("\u2013", "-")
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"[ \t]+", " ", s).strip()


def parse_amount(token: str) -> float | None:
    # Kaper YAML amount: whole numbers and decimals only (no fractions).
    # Contract: .cursor/rules/recipe-vault.mdc
    token = token.strip().replace("\u2044", "/")
    if not token:
        return None
    if token in UNICODE_FRAC:
        return UNICODE_FRAC[token]
    m = re.match(r"^(\d+)\s*([½¼¾⅓⅔⅛⅜⅝⅞])$", token)
    if m:
        return float(m.group(1)) + UNICODE_FRAC[m.group(2)]
    compact = re.sub(r"\s+", " ", token)
    m = re.match(r"^(\d+)\s+(\d+)/(\d+)$", compact)
    if m:
        den = float(m.group(3))
        return float(m.group(1)) + float(m.group(2)) / den if den else None
    nospace = compact.replace(" ", "")
    m = re.match(r"^(\d+)[-–](\d+)$", nospace)
    if m:
        return (float(m.group(1)) + float(m.group(2))) / 2.0
    m = re.match(r"^(\d+)/(\d+)$", nospace)
    if m:
        den = float(m.group(2))
        return float(m.group(1)) / den if den else None
    m = re.match(r"^(\d+)[-–](\d+)/(\d+)$", nospace)
    if m:
        den = float(m.group(3))
        return float(m.group(1)) + float(m.group(2)) / den if den else None
    # "11/2" often means 1 1/2 in handwriting OCR
    m = re.match(r"^(\d+)(\d)/(\d+)$", nospace)
    if m and int(m.group(3)) in (2, 3, 4, 8) and int(m.group(2)) < int(m.group(3)):
        return float(m.group(1)) + float(m.group(2)) / float(m.group(3))
    # Leftover ⁄2 after a leading 1 was parsed separately
    m = re.match(r"^/(\d+)$", nospace)
    if m:
        den = float(m.group(1))
        return (1.0 / den) if den else None
    try:
        return float(nospace)
    except ValueError:
        return None


AMT_TOKEN = (
    r"(?:(?:\d+\s+\d/\d)|(?:\d+\s*-\s*\d+/\d+)|(?:\d+/\d+)|(?:\d+[½¼¾⅓⅔⅛⅜⅝⅞])|"
    r"(?:\d+\.\d+)|(?:\.\d+)|(?:\d+)|[½¼¾⅓⅔⅛⅜⅝⅞])"
)


def split_amount_unit_name(line: str) -> tuple[float, str, str, str] | None:
    line = _norm_ws(line)
    line = re.sub(r"^[•\-–—*]\s*", "", line)
    # Strip leading method verb + optional colon: "Saute: 1 medium onion"
    stripped = STEP_START.sub("", line).lstrip(" :,-")
    use = stripped if stripped != line else line
    m = re.match(
        rf"^(?P<amt>{AMT_TOKEN}(?:\s*[-–]\s*{AMT_TOKEN})?)\s*"
        rf"(?P<unit>{UNIT_RE})?\b\.?\s*[:.]?\s*(?P<rest>.+)$",
        use,
        re.I,
    )
    if not m:
        m = re.match(
            rf"^(?P<amt>{AMT_TOKEN})\s+(?P<rest>.+)$",
            use,
        )
        if not m:
            return None
        amt = parse_amount(m.group("amt"))
        if amt is None:
            return None
        rest = m.group("rest").strip(" .,:")
        name, note = _split_note(rest)
        return amt, "", name, note
    amt_raw = m.group("amt")
    if "-" in amt_raw and "/" not in amt_raw.split("-")[-1]:
        parts = re.split(r"[-–]", amt_raw)
        vals = [parse_amount(p) for p in parts if p.strip()]
        vals = [v for v in vals if v is not None]
        amt = sum(vals) / len(vals) if vals else None
    else:
        amt = parse_amount(amt_raw)
    if amt is None:
        return None
    unit_raw = (m.groupdict().get("unit") or "").strip()
    unit = UNIT_ALIASES.get(unit_raw, UNIT_ALIASES.get(unit_raw.lower(), unit_raw.lower().rstrip(".")))
    rest = m.group("rest").strip(" .,:")
    name, note = _split_note(rest)
    return amt, unit, name, note


# Prep/knife directions → Kaper `note`, not `name`. Case-insensitive; OCR near-misses included.
# Contract: .cursor/rules/recipe-vault.mdc
PREP_NOTE_RE = re.compile(
    r"\b("
    r"mince(?:d)?\s+to\s+a\s+paste|"
    r"rough(?:ly)?\s+chop(?:ped)?|"
    r"fine(?:ly)?\s+chop(?:ped)?|"
    r"(?:large|medium|small)\s+dice(?:d)?|"
    r"thin(?:ly)?\s+slice(?:d)?|"
    r"halve[ds]?|quarter(?:ed|s)?|wedge[ds]?|chunk(?:ed|s)?|"
    r"chop(?:ped)?|dice(?:d)?|mince(?:d)?|slice(?:d)?|"
    r"rings?|strips?|julienne(?:d)?|chiffonade|"
    r"crush(?:ed)?|smash(?:ed)?|"
    r"grated|peeled|softened|melted|packed|sifted|beaten|optional|"
    r"chappel|chaoped|chapel|chasped"
    r")\b",
    re.I,
)


def _split_note(rest: str) -> tuple[str, str]:
    rest = rest.strip(" .")
    if "," in rest:
        name, note = rest.split(",", 1)
        return _clean_name(name), note.strip(" .")
    m = PREP_NOTE_RE.search(rest)
    if m and m.start() > 3:
        return _clean_name(rest[: m.start()]), rest[m.start() :].strip(" .,")
    return _clean_name(rest), ""


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" .:-")
    # Do not leave a fraction glyph in name (e.g. leading ⁄2 after amount split).
    name = re.sub(r"^[/⁄]\d+\s*", "", name)
    name = re.sub(r"^[½¼¾⅓⅔⅛⅜⅝⅞]\s*", "", name)
    name = re.sub(r"^(of|a|an)\s+", "", name, flags=re.I)
    return name[:80]


def parse_minutes(text: str) -> str | None:
    t = text.lower()
    total = 0
    found = False
    # Normalize common handwritten fractions before the bare \d+ hour matcher
    # (otherwise "1/2 hr" is read as 2 hours).
    t = re.sub(r"(½|1\s*/\s*2)\s*(hours?|hrs?|hr)\b", "30 minutes", t)
    t = re.sub(r"(¼|1\s*/\s*4)\s*(hours?|hrs?|hr)\b", "15 minutes", t)
    t = re.sub(r"(¾|3\s*/\s*4)\s*(hours?|hrs?|hr)\b", "45 minutes", t)
    t = re.sub(r"(⅓|1\s*/\s*3)\s*(hours?|hrs?|hr)\b", "20 minutes", t)
    for m in re.finditer(r"(\d+)\s*(?:-\s*\d+\s*)?(hours?|hrs?|hr)\b", t):
        total += int(m.group(1)) * 60
        found = True
    for m in re.finditer(r"(\d+)\s*(?:-\s*\d+\s*)?(minutes?|mins?|min)\b", t):
        total += int(m.group(1))
        found = True
    if not found:
        return None
    return format_duration(total)


def format_duration(minutes: int) -> str:
    if minutes <= 0:
        return ""
    if minutes % 60 == 0:
        h = minutes // 60
        return f"{h}h"
    if minutes > 90:
        h, m = divmod(minutes, 60)
        return f"{h}h{m}m" if m else f"{h}h"
    return f"{minutes}m"


def duration_to_minutes(s: str) -> int:
    if not s:
        return 0
    m = re.match(r"^(?:(\d+)h)?(?:(\d+)m)?$", s.strip())
    if not m:
        return 0
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def strip_kaper_markdown(s: str) -> str:
    """Remove markdown from a Kaper YAML string. RAW OCR is not passed here."""
    if s is None:
        return ""
    s = str(s)
    s = s.replace("\u2014", "-").replace("\u2013", "-")
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", s)
    s = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?!\w)", r"\1", s)
    s = s.replace("`", "")
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.M)
    s = re.sub(r"(?<!\d)#\s*", "", s)
    s = re.sub(r"^[\-\*+]\s+", "", s, flags=re.M)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip(" \t")


_CLOCK_UNIT = r"(?:hours?|hrs?|hr|minutes?|mins?|min|m)\b"
_CLOCK_AMT = (
    r"(?:(?:\d+\s+\d/\d)|(?:\d+/\d+)|(?:\d+\.\d+)|(?:\.\d+)|(?:\d+)|[½¼¾⅓⅔⅛⅜⅝⅞]|"
    r"(?:\d+[½¼¾⅓⅔⅛⅜⅝⅞]))"
)
CLOCK_SPAN = re.compile(
    r"(?:(?:,|;)\s*)?(?:(?:for|about|around|another|at\s+least|approx(?:imately)?)\s+)?"
    rf"(?P<a>{_CLOCK_AMT})\s*(?:(?:[-–—]|to)\s*(?P<b>{_CLOCK_AMT})\s*)?(?P<unit>{_CLOCK_UNIT})"
    r"(?:\s*\(\s*typical\s*\))?",
    re.I,
)
BARE_DURATION = re.compile(r"\b(?P<n>\d+)m\b")
PAREN_TIME = re.compile(
    rf"\(\s*(?P<a>{_CLOCK_AMT})\s*(?:(?:[-–—]|to)\s*(?P<b>{_CLOCK_AMT})\s*)?(?P<unit>{_CLOCK_UNIT})\s*\)",
    re.I,
)
TYPICAL_PAREN = re.compile(r"\s*\(\s*typical\s*\)", re.I)
OVERNIGHT_RE = re.compile(r"\bovernight\b", re.I)


def _clock_amt_minutes(token: str, unit: str) -> int | None:
    amt = parse_amount((token or "").replace("–", "-").replace("—", "-").strip())
    if amt is None:
        return None
    u = (unit or "min").lower()
    if u.startswith("h"):
        mins = int(round(amt * 60))
    elif u == "m" or u.startswith("min"):
        mins = int(round(amt))
    else:
        mins = int(round(amt))
    if mins >= 200 and (u == "m" or u.startswith("min")):
        return None
    if mins <= 0:
        return None
    return mins


def clock_spans(text: str) -> list[tuple[int, int, int]]:
    """Return (start, end, minutes) for clock times. Range uses the upper bound."""
    if not text:
        return []
    found: list[tuple[int, int, int]] = []
    occupied: list[tuple[int, int]] = []

    def _taken(a: int, b: int) -> bool:
        return any(a < e and b > s for s, e in occupied)

    def _add(a: int, b: int, mins: int) -> None:
        if mins and not _taken(a, b):
            found.append((a, b, mins))
            occupied.append((a, b))

    for m in PAREN_TIME.finditer(text):
        unit_s = m.group("unit") or "min"
        vals = [
            _clock_amt_minutes(m.group("a"), unit_s),
            _clock_amt_minutes(m.group("b"), unit_s) if m.group("b") else None,
        ]
        vals = [v for v in vals if v]
        if vals:
            _add(m.start(), m.end(), max(vals))
    for m in CLOCK_SPAN.finditer(text):
        unit_s = m.group("unit") or "min"
        if re.match(r"(?i)m\b", unit_s) and not re.search(r"(?i)min|hour|hr", unit_s):
            nxt = text[m.end() : m.end() + 1]
            if nxt.isalpha():
                continue
        vals = [
            _clock_amt_minutes(m.group("a"), unit_s),
            _clock_amt_minutes(m.group("b"), unit_s) if m.group("b") else None,
        ]
        vals = [v for v in vals if v]
        if vals:
            _add(m.start(), m.end(), max(vals))
    for m in BARE_DURATION.finditer(text):
        mins = int(m.group("n"))
        if mins >= 200:
            continue
        _add(m.start(), m.end(), mins)
    return found


def clock_minutes_list(text: str) -> list[int]:
    mins = [m for _a, _b, m in clock_spans(text)]
    if OVERNIGHT_RE.search(text or ""):
        mins.append(8 * 60)
    return mins


def strip_clock_language(text: str) -> str:
    """Remove clock times from instruction text. Keep overnight and qualitative ends."""
    if not text:
        return ""
    spans = clock_spans(text)
    if not spans:
        out = TYPICAL_PAREN.sub("", text)
        return re.sub(r"\s+", " ", out).strip(" ,;.")
    keep = []
    last = 0
    for a, b, _mins in sorted(spans):
        keep.append(text[last:a])
        last = b
    keep.append(text[last:])
    out = "".join(keep)
    out = TYPICAL_PAREN.sub("", out)
    out = re.sub(r"\b(for|about|around|another|approximately)\s*([,.;]|$)", r"\2", out, flags=re.I)
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"\s+([,.;])", r"\1", out)
    out = re.sub(r"\s+", " ", out)
    return out.strip(" ,;.")


_SPELLED_UNITS = (
    r"tablespoons?|teaspoons?|cups?|pounds?|ounces?|grams?|kilograms?|"
    r"heads?|stalks?|cloves?|bunches?|slices?|sticks?|cans?|packages?|"
    r"pinches?|dashes?|quarts?|pints?|gallons?|liters?|litres?|"
    r"envelopes?|bottles?|jars?|bags?|boxes?|sheets?"
)
_QUANT_UNIT = rf"(?:{UNIT_RE}|{_SPELLED_UNITS})"
QUANT_MENTION = re.compile(
    rf"(?P<prefix>\b(?:the|a|an)\s+)?"
    rf"(?P<amt>{AMT_TOKEN}(?:\s*(?:or)\s*{AMT_TOKEN})?(?:\s*[-–—]\s*{AMT_TOKEN})?)\s*"
    rf"(?:(?P<unit>{_QUANT_UNIT})\b\.?\s*)?"
    rf"(?:of\s+)?"
    rf"(?P<name>[A-Za-z][\w'\-]*(?:\s+(?:of\s+)?[A-Za-z][\w'\-]*){{0,4}})",
    re.I,
)
SUBDIVIDE_CUE = re.compile(
    r"\b(at a time|of the|remaining|remainder|the rest|rest of|other half|"
    r"half of|half the|one half|except|set aside|reserv(?:e|ed|ing)|"
    r"divid(?:e|ed))\b",
    re.I,
)
EQUIPMENT_NAME = re.compile(
    r"^(skillet|pan|pot|bowl|oven|dish|plate|casserole|mixer|saucepan|"
    r"kettle|sheet|rack|blender|processor|board|knife|speed|rack|foil|"
    r"thermometer|timer|burner|flame|heat|inch|inches|degree|degrees|"
    r"minute|minutes|hour|hours|min|mins|hr|hrs|dutch|wok|griddle|"
    r"steamer|colander|strainer|ramekin)\b",
    re.I,
)
COOKWARE_PHRASE = re.compile(
    r"\b(dutch\s+ovens?|stock\s*pots?|saucepans?|skillets?|frying\s+pans?|"
    r"saute\s+pans?|sauté\s+pans?|grill\s+pans?|roasting\s+pans?|"
    r"sheet\s+pans?|loaf\s+pans?|cake\s+pans?|pie\s+pans?|"
    r"baking\s+(?:dishes?|pans?|sheets?)|cookie\s+sheets?|"
    r"muffin\s+tins?|mixing\s+bowls?|double\s+boilers?|"
    r"casserole\s+dishes?|food\s+processors?|cutting\s+boards?)\b",
    re.I,
)
COOKWARE_ONLY = re.compile(
    r"^(?:(?:a|an|the|large|small|medium|heavy|nonstick|non-stick|"
    r"buttered|oiled|greased|hot|ovenproof|oven-proof)\s+)*"
    r"(dutch\s+oven|skillet|saucepan|stockpot|stock\s*pot|wok|griddle|"
    r"bowl|pan|pot|oven|dish|plate|casserole|mixer|blender|kettle|"
    r"colander|strainer|ramekin|steamer)s?$",
    re.I,
)
FOODISH_COOKWARE = re.compile(
    r"\b(pie|roast|liquor|cheese|stick|herb|roast|piecrust|broth|stock)\b",
    re.I,
)


def is_cookware_name(name: str) -> bool:
    """True for pans, bowls, dutch ovens — not foods. Stay in instructions."""
    n = (name or "").strip()
    if not n:
        return False
    if FOODISH_COOKWARE.search(n) and not COOKWARE_PHRASE.search(n):
        return False
    if COOKWARE_ONLY.match(n) or COOKWARE_PHRASE.search(n):
        return True
    return bool(EQUIPMENT_NAME.match(n))


JUNK_INGREDIENT_NAME = re.compile(
    r"^(paste|mixture|little|non|few|good|gentle|boil|separate|"
    r"large|small|medium|cutlets?|top up|water to cover)$",
    re.I,
)


def drop_ingredient_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return True
    if JUNK_INGREDIENT_NAME.match(n):
        return True
    return is_cookware_name(n)
STEP_NUM_LEAD = re.compile(r"^\d+[\.)]\s")
TEMP_OR_SIZE = re.compile(r"^[°x×]|inch|degree", re.I)
INTERNAL_TEMP = re.compile(
    r"(?:,\s*)?(?:until\s+)?internal\s+temp(?:erature|rature)?\s*(?:of\s*)?"
    r"(?P<n>\d+)\s*°?\s*[fF]?",
    re.I,
)
ACTION_VERB_HEAD = re.compile(
    r"^(heat|saute|sauté|saut[eé]|beat|stir|mix|add|fold|pour|bake|cook|"
    r"simmer|boil|blend|whisk|cream|preheat|put|place|layer|cover|serve|"
    r"combine|tear|soak|brown|drain|spread|roll|knead|chill|refrigerate|"
    r"freeze|top|remove|reduce|season|dot|arrange|brush|grease|line|"
    r"marinate|roast|grill|broil|steam|puree|pur[eé]e|process|pulse|"
    r"scald|melt|dissolve|sprinkle|garnish|cool|let|bring|transfer|"
    r"turn|flip|cut|slice|dice|chop|peel|skim|dip|dredge|coat|shape|"
    r"shell|crush|make|soften|wash|trim|toss|reheat|divide|pat|drop|"
    r"fill|proof|rise|sear|braise|deglaze|uncover|butter|oil)\b",
    re.I,
)


def iter_quant_mentions(text: str):
    """Yield dicts for quantified foods in instruction prose."""
    if not text:
        return
    for m in QUANT_MENTION.finditer(text):
        amt_raw = m.group("amt") or ""
        unit = (m.group("unit") or "").strip()
        name = (m.group("name") or "").strip()
        start, end = m.start(), m.end()
        if STEP_NUM_LEAD.match(text[start:]):
            continue
        after_amt = text[m.start("amt") + len(amt_raw) : m.start("amt") + len(amt_raw) + 1]
        if not unit and after_amt in (".", ")"):
            continue
        prev = text[max(0, start - 16) : start].lower()
        if re.search(r"\b(speed|rack|room)\s*$", prev):
            continue
        name = re.split(
            r"\s+(?:until|then|and|or|about|for|over|into|onto|with|in|on)\b|,",
            name,
            maxsplit=1,
            flags=re.I,
        )[0].strip()
        if name:
            idx = text.find(name, m.start("name") if m.start("name") >= 0 else start)
            if idx >= 0:
                end = idx + len(name)
        tail = text[end : end + 8]
        nxt = text[end : end + 1]
        if TEMP_OR_SIZE.search(tail) or nxt == "°":
            continue
        if is_cookware_name(name):
            continue
        if not name or (not unit and ACTION_VERB_HEAD.match(name)):
            continue
        amt_head = re.split(r"\s+or\s+|[-–—]", amt_raw, maxsplit=1)[0]
        if parse_amount(amt_head) is None:
            continue
        window = text[max(0, start - 24) : min(len(text), end + 32)]
        amt_val = parse_amount(amt_raw) if " or " not in amt_raw.lower() else parse_amount(amt_head)
        half_the = (
            not unit
            and amt_val is not None
            and amt_val <= 1
            and re.match(r"(?i)the\b", name)
        )
        subdivide = bool(SUBDIVIDE_CUE.search(window) or half_the)
        yield {
            "start": start,
            "end": end,
            "prefix": m.group("prefix") or "",
            "amt": amt_raw,
            "unit": unit,
            "name": name,
            "subdivide": subdivide,
            "key": food_name_key(name),
        }


def strip_whole_ingredient_amounts(
    text: str, *, keep_keys: set[str] | None = None
) -> str:
    """Drop whole-ingredient amounts; keep amounts that subdivide a food."""
    if not text:
        return ""
    mentions = list(iter_quant_mentions(text))
    if not mentions:
        return text
    keep_keys = keep_keys or set()
    out = []
    last = 0
    for ment in mentions:
        if ment["subdivide"] or (ment["key"] and ment["key"] in keep_keys):
            continue
        out.append(text[last : ment["start"]])
        prefix = ment["prefix"]
        name = ment["name"]
        name = re.sub(r"^(chopped|sliced|minced|diced|grated|fresh|dried)\s+", "", name, flags=re.I)
        repl = f"{prefix}{name}" if prefix else name
        out.append(repl)
        last = ment["end"]
    out.append(text[last:])
    s = "".join(out)
    s = re.sub(r"\(\s*(?:about\s+)?\)", "", s)
    s = re.sub(r"\s+,", ",", s)
    s = re.sub(r",\s*,", ",", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" ,;.")


def extract_internal_temp_tip(text: str) -> tuple[str, str | None]:
    if not text:
        return "", None
    m = INTERNAL_TEMP.search(text)
    if not m:
        return text, None
    tip = f"internal temperature {m.group('n')}"
    left = (text[: m.start()] + text[m.end() :]).strip(" ,;.")
    return re.sub(r"\s+", " ", left).strip(" ,;."), tip


def extract_servings(text: str) -> int | None:
    patterns = [
        r"(\d+)\s*-\s*(\d+)\s*servings?",
        r"serv(?:es|ings?)\s*[:.]?\s*(\d+)\s*-\s*(\d+)",
        r"serv(?:es|ings?)\s*[:.]?\s*(\d+)",
        r"(\d+)\s*servings?",
        r"serves?\s+(\d+)",
        r"4-6 servings",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            nums = [int(g) for g in m.groups() if g and str(g).isdigit()]
            if len(nums) >= 2:
                return round((nums[0] + nums[1]) / 2)
            if nums:
                return nums[0]
    return None


def extract_oven(text: str) -> int | None:
    m = re.search(r"\b(3[025]0|375|400|425|450|275|300|325|350)\s*°?\s*F?\b", text)
    if m:
        return int(m.group(1))
    m = re.search(r"oven\s*[:.]?\s*(\d{3})", text, re.I)
    if m:
        val = int(m.group(1))
        if 250 <= val <= 500:
            return val
    return None


def extract_yield(text: str) -> str:
    m = re.search(r"\b(?:yield|makes?)\s*[:.]?\s*([^\n]{3,40})", text, re.I)
    if m:
        return m.group(1).strip(" .")
    return ""


def latin_ratio(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return 0.0
    return sum(ch.isascii() for ch in letters) / len(letters)


FOOD_TITLE = re.compile(
    r"\b(soup|stew|cake|pie|pudding|chicken|salad|casserole|bread|cookie|"
    r"roast|eggplant|pork|beef|tuna|cheese|broccoli|surprise|steak|sauce|"
    r"frango|pucara|pacara|muffin|pancake|chili|lasagna|cookies|fudge|brownie|"
    r"rabbit|tabbouleh|tabouli|pilaf|pilat|loaf|meatball)\b",
    re.I,
)


def looks_like_title(line: str) -> bool:
    line = _norm_ws(line)
    if not line or len(line) > 70 or len(line) < 4:
        return False
    if latin_ratio(line) < 0.85:
        return False
    if TITLE_SKIP.search(line):
        return False
    if re.fullmatch(r"[\d°./\-\sF]+", line):
        return False
    if split_amount_unit_name(line) and not re.match(r"^[A-Z][a-z].{8,}", line):
        return False
    if STEP_START.search(line) and not FOOD_TITLE.search(line):
        return False
    if STEP_START.search(line) and split_amount_unit_name(STEP_START.sub("", line).lstrip(" :,")):
        return False
    letters = sum(ch.isalpha() for ch in line)
    if letters < 4:
        return False
    return True


def pick_title(lines: list[str], ocr_lines: list[dict], card_id: str) -> str:
    scored: list[tuple[float, int, str]] = []
    # Scan the whole card: titles are often at the top, but some scans put
    # the heading after the method (or OCR order is reversed).
    for i, line in enumerate(lines):
        if not looks_like_title(line):
            continue
        score = 8 - min(i, 12) * 0.45
        words = line.split()
        if 2 <= len(words) <= 8:
            score += 5
        if FOOD_TITLE.search(line):
            score += 10
        if STEP_START.search(line):
            score -= 6
        if "&" in line or "+" in line or " and " in line.lower():
            score += 2
        scored.append((score, i, line))
    # Join two consecutive short title-like lines (e.g. Cheese + Bread / Pudding)
    by_idx = {i: (sc, ln) for sc, i, ln in scored}
    for i in sorted(by_idx):
        if i + 1 in by_idx:
            a = by_idx[i][1]
            b = by_idx[i + 1][1]
            if (
                len(a) < 28
                and len(b) < 24
                and not any(ch.isdigit() for ch in a + b)
                and not STEP_START.search(a)
                and not STEP_START.search(b)
            ):
                joined = f"{a} {b}".strip()
                extra = 6 if FOOD_TITLE.search(joined) else 0
                if extra:
                    scored.append((by_idx[i][0] + extra + 3, i, joined))
    if scored:
        scored.sort(key=lambda x: -x[0])
        title = _norm_ws(scored[0][2])
        title = re.sub(r"\s*[-–]\s*$", "", title).strip(" .:-")
        if title.isupper() or title.islower():
            title = title.title()
        return title[:80]
    pretty = card_id.replace("_reciepes_", " ").replace("_", " ")
    return f"Untitled {pretty}"


def _ings_blob(ingredients) -> str:
    if not ingredients:
        return ""
    parts: list[str] = []
    groups = ingredients.values() if isinstance(ingredients, dict) else ingredients
    for group in groups:
        if not group:
            continue
        for it in group:
            if isinstance(it, dict):
                parts.append(f"{it.get('name', '')} {it.get('note', '')}")
            else:
                parts.append(f"{getattr(it, 'name', '')} {getattr(it, 'note', '')}")
    return " ".join(parts)


def _steps_blob(steps) -> str:
    if not steps:
        return ""
    parts: list[str] = []
    for s in steps:
        if isinstance(s, dict):
            parts.append(str(s.get("title") or ""))
        else:
            parts.append(str(getattr(s, "title", s) or ""))
    return " ".join(parts)


def _dessert_hit(s: str) -> bool:
    if not s or not DESSERT_FORM.search(s):
        return False
    if SAVORY_PIE.search(s) or SAVORY_CAKE.search(s) or SAVORY_PUDDING.search(s):
        return False
    return True


def _course_tags(blob: str, title: str) -> list[str]:
    tags: list[str] = []
    if re.search(r"\bsoup\b", blob, re.I):
        tags.append("soup")
    if re.search(r"\bstew\b", blob, re.I):
        tags.append("stew")
    if re.search(r"\bcasserole\b", blob, re.I):
        tags.append("casserole")
    if re.search(r"\bsalad\b", blob, re.I):
        tags.append("salad")
    if re.search(r"\bbread\b", blob, re.I) and not _dessert_hit(title):
        tags.append("bread")
    return tags


def _ing_rows(ingredients):
    if not ingredients:
        return
    groups = ingredients.values() if isinstance(ingredients, dict) else ingredients
    for group in groups or []:
        for it in group or []:
            if isinstance(it, dict):
                yield it.get("amount"), it.get("unit") or "", it.get("name") or "", it.get("note") or ""
            else:
                yield (
                    getattr(it, "amount", None),
                    getattr(it, "unit", "") or "",
                    getattr(it, "name", "") or "",
                    getattr(it, "note", "") or "",
                )


def _unit_key(unit: str) -> str:
    return (unit or "").strip().rstrip(".").lower()


def _has_major_protein(title: str, ingredients=None, raw: str = "") -> bool:
    """True when the dish is built around a substantial protein."""
    title = title or ""
    if PASTA_AS_MAIN.search(title) or SAVORY_PIE.search(title):
        return True
    if PROTEIN_FOOD.search(title) and not BROTH_FLAVOR.search(title):
        return True
    for amount, unit, name, note in _ing_rows(ingredients):
        blob = f"{name} {note}"
        if re.search(r"\b(broth|stock|base)\b", blob, re.I):
            continue
        if not PROTEIN_FOOD.search(blob):
            continue
        ukey = _unit_key(unit)
        if ukey in SMALL_UNITS:
            continue
        if FLAVOR_PROTEIN.search(blob) or re.search(r"\bham\b", blob, re.I):
            try:
                n = float(amount or 0)
            except (TypeError, ValueError):
                n = 0
            if ukey in SMALL_UNITS or ukey in {"slice", "slices", ""}:
                continue
            if ukey in {"oz", "ounce", "ounces"} and n < 8:
                continue
            if ukey in {"cup", "cups"} and n < 0.5:
                continue
        return True
    if ingredients:
        return False
    probe = BROTH_FLAVOR.sub(" ", raw or "")
    return bool(PROTEIN_FOOD.search(probe))


def classify_course(
    title: str,
    text: str,
    ingredients=None,
    steps=None,
) -> tuple[str, list[str]]:
    """Assign breakfast / main / side / drink / dessert / condiment.

    Mains are dishes with a major protein. Sides are vegetables, starches,
    and small-bite snacks. Empty string is unused; savory leftovers are sides.
    """
    title = title or ""
    raw = text or ""
    ings = _ings_blob(ingredients)
    step_txt = _steps_blob(steps)
    blob = "\n".join(p for p in (title, ings, step_txt, raw) if p)
    tags = _course_tags(blob, title)
    protein = _has_major_protein(title, ingredients, raw)

    def t(pat) -> bool:
        return bool(pat.search(title))

    dessert_title = _dessert_hit(title)
    protein_title = bool(PROTEIN_FOOD.search(title) and not BROTH_FLAVOR.search(title))
    savory_title = protein or protein_title or t(SAVORY_PIE) or t(SAVORY_CAKE) or t(SAVORY_PUDDING)

    if t(BREAKFAST):
        return "breakfast", tags
    if (t(DRINK) or (t(DRINK_TITLE) and not dessert_title)) and not savory_title:
        return "drink", tags
    if dessert_title and not savory_title:
        return "dessert", tags
    if re.search(r"\bbars?\b", title, re.I) and not protein_title:
        return "dessert", tags
    if t(CONDIMENT) or SAUCE_TITLE.search(title.strip()):
        if re.search(r"\b(rubs?|marinades?)\s+for\b", title, re.I):
            return "condiment", tags
        if not protein_title:
            return "condiment", tags
    if t(SMALL_BITE):
        return "side", tags
    if t(SALAD):
        return ("main" if protein else "side"), tags
    if protein:
        return "main", tags
    return "side", tags


def cuisine_of(title: str, text: str) -> str:
    blob = f"{title}\n{text}"
    for pat, name in CUISINE_HINTS:
        if re.search(pat, blob, re.I):
            return name
    return ""


def allergens_of(names: list[str]) -> list[str]:
    blob = " ".join(names)
    found = []
    for label, pat in ALLERGEN_MAP:
        if pat.search(blob):
            found.append(label)
    return found


OCR_FIXES = [
    (re.compile(r"^top\s*9\s*:", re.I | re.M), "Top w:"),
    (re.compile(r"\b(sakee|bakee|batec)\b", re.I), "Baked"),
    (re.compile(r"\bbakung\b", re.I), "baking"),
    (re.compile(r"\b(leggplant|legsplent)\b", re.I), "eggplant"),
    (re.compile(r"\bonors\b", re.I), "onions"),
    (re.compile(r"\b(tometoes|tomatses)\b", re.I), "tomatoes"),
    (re.compile(r"\b(chappel|chaoped|chapel|chasped)\b", re.I), "chopped"),
    (re.compile(r"\bgratel\b", re.I), "grated"),
    (re.compile(r"\b(regar[ao]|regano)\b", re.I), "oregano"),
    (re.compile(r"\b(loosil|losil)\b", re.I), "basil"),
    (re.compile(r"\bzuchinni\b", re.I), "zucchini"),
    (re.compile(r"\bbuillon\b", re.I), "bouillon"),
    (re.compile(r"\bchix\b", re.I), "chicken"),
    (re.compile(r"\b(cluchen|chiachar)\b", re.I), "chicken"),
    (re.compile(r"\bservines\b", re.I), "servings"),
    (re.compile(r"\bfraugo\b", re.I), "Frango"),
    (re.compile(r"\bpeanul\b", re.I), "Peanut"),
    (re.compile(r"\b(veyetable|legoalse)\b", re.I), "Vegetable"),
    (re.compile(r"\bdoup\b", re.I), "Soup"),
    (re.compile(r"\bproscoli\b", re.I), "Broccoli"),
    (re.compile(r"\bstero\b", re.I), "Stew"),
    (re.compile(r"\bsteal\b", re.I), "steak"),
    (re.compile(r"\bpork\s+i\s+", re.I), "Pork & "),
    (re.compile(r"\sY2\s"), " 1/2 "),
    (re.compile(r"\bY2\b"), "1/2"),
    (re.compile(r"\bYa\s*c\.?\b", re.I), "1/4 c."),
    (re.compile(r"\bYac\.?\b", re.I), "1/4 c."),
    (re.compile(r"\bY4\s*c\.?\b", re.I), "1/4 c."),
    (re.compile(r"\bIT\.\b"), "1 T."),
    (re.compile(r"\blu\.", re.I), "hr."),
    (re.compile(r"\bwatier\b", re.I), "water"),
    (re.compile(r"\bbluebornes\b", re.I), "blueberries"),
    (re.compile(r"\bgarham\b", re.I), "graham"),
    (re.compile(r"\bsuace\s*pans?\b", re.I), "saucepan"),
    (re.compile(r"\bprehead\b", re.I), "Preheat"),
    (re.compile(r"\bboke\b", re.I), "Bake"),
    (re.compile(r"\bbuttner\b", re.I), "butter"),
    (re.compile(r"\brabit\b", re.I), "rabbit"),
    (re.compile(r"\bvegitables?\b", re.I), "vegetables"),
    (re.compile(r"\bvegitable\b", re.I), "vegetable"),
    (re.compile(r"\bavacado\b", re.I), "avocado"),
    (re.compile(r"\bcorflakes?\b", re.I), "cornflakes"),
    (re.compile(r"\bchiken\b", re.I), "chicken"),
    (re.compile(r"\bpaparika\b", re.I), "paprika"),
    (re.compile(r"\brenove\b", re.I), "remove"),
    (re.compile(r"\bunsweet\b", re.I), "unsweetened"),
]


def cleanup_ocr(text: str) -> str:
    out = text
    for pat, repl in OCR_FIXES:
        out = pat.sub(repl, out)
    return out


def parse_recipe(ocr_text: str, card_id: str, ocr_lines: list[dict] | None = None) -> ParsedRecipe:
    """Reconstruct a recipe from card OCR (plain text or markdown)."""
    from reconstruct import reconstruct_recipe

    return reconstruct_recipe(ocr_text, card_id, ocr_lines)


def parse_recipe_heuristic(ocr_text: str, card_id: str, ocr_lines: list[dict] | None = None) -> ParsedRecipe:
    raw = cleanup_ocr(ocr_text or "")
    ocr_ok = len(re.findall(r"[A-Za-z]{3,}", raw)) >= 8
    if ocr_lines:
        cleaned_ocr = []
        for ln in ocr_lines:
            t = dict(ln)
            t["text"] = cleanup_ocr(ln.get("text") or "")
            cleaned_ocr.append(t)
        ocr_lines = cleaned_ocr
    lines = [_norm_ws(ln) for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln and ln != "--- BACK ---"]

    title = pick_title(lines, ocr_lines or [], card_id)
    servings = extract_servings(raw) or 4
    oven = extract_oven(raw)
    yield_text = extract_yield(raw)

    cook = None
    prep = None
    # bake / simmer / cook durations tend to be cook time
    for m in re.finditer(
        r"\b(bake|cook|simmer|roast|grill|broil|slow cook)\b[^\n]{0,40}",
        raw,
        re.I,
    ):
        d = parse_minutes(m.group(0))
        if d:
            cook = d
            break
    if not cook:
        cook = parse_minutes(raw)
    for m in re.finditer(r"\b(prep|chill|marinate|rest|rise|soak)\b[^\n]{0,40}", raw, re.I):
        d = parse_minutes(m.group(0))
        if d:
            prep = d
            break
    if not prep:
        # "saute 10 min" style
        saute = re.search(r"\b(saute|sauté|beat|mix)\b[^\n]{0,30}", raw, re.I)
        if saute:
            prep = parse_minutes(saute.group(0))

    ingredients: dict[str, list[Ingredient]] = {"main": []}
    steps: list[Step] = []
    current_group = "main"

    for line in lines:
        if not line or line.lower() == title.lower():
            continue
        if GROUP_HEADERS.match(line.rstrip(":")):
            current_group = re.sub(r"[^A-Za-z]+", "_", line.lower()).strip("_") or "main"
            ingredients.setdefault(current_group, [])
            continue
        duration = parse_minutes(line)
        parsed_ing = split_amount_unit_name(line)
        is_step = bool(STEP_START.search(line))
        bake_line = bool(re.search(r"\b(bake|preheat|oven)\b", line, re.I)) and bool(
            re.search(r"\b(hr|hour|min|°|degree)\b", line, re.I)
        )
        if bake_line:
            parsed_ing = None
        if parsed_ing and (not is_step or len(parsed_ing[2]) >= 2):
            amt, unit, name, note = parsed_ing
            if name and len(name) >= 2 and not re.match(r"^(hr|hours?|min|minutes?)\b", name, re.I):
                ingredients.setdefault(current_group, []).append(
                    Ingredient(amount=amt, unit=unit, name=name, note=note)
                )
        if is_step or (duration and not parsed_ing):
            step_text = _norm_ws(line)
            if len(step_text) >= 8:
                steps.append(Step(title=step_text[:240], duration=duration, note=None))
        elif not parsed_ing and len(line) > 25 and re.search(r"\b(until|then|when|before serving)\b", line, re.I):
            steps.append(Step(title=line[:240], duration=duration, note=None))

    for g, lst in list(ingredients.items()):
        ingredients[g] = dedupe_ingredients(lst)

    # Deduplicate near-identical steps
    uniq_steps: list[Step] = []
    seen = set()
    for s in steps:
        key = re.sub(r"\W+", "", s.title.lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        uniq_steps.append(s)
    steps = uniq_steps

    if not any(ingredients.values()):
        ingredients = {"main": []}
    if not steps:
        # leftover instructional lines
        for line in lines[1:]:
            if line.lower() == title.lower():
                continue
            if len(line) > 20:
                steps.append(Step(title=line[:240]))
                if len(steps) >= 6:
                    break
    if not steps:
        steps = [Step(title="Follow the source card; OCR did not recover numbered steps.")]

    course, extra_tags = classify_course(
        title, raw, ingredients=ingredients, steps=steps
    )
    tags = []
    era = "1980s" if "1980s" in card_id else "1990s"
    tags.append(era)
    tags.extend(extra_tags)
    if oven:
        tags.append("baked")
    tags = list(dict.fromkeys(tags))

    n_ing = sum(len(v) for v in ingredients.values())
    if n_ing <= 6 and len(steps) <= 5:
        difficulty = "easy"
    elif n_ing >= 14 or len(steps) >= 10:
        difficulty = "hard"
    else:
        difficulty = "medium"

    from reconstruct import shape_steps, sum_buckets as recon_sum

    title = strip_kaper_markdown(title)
    yield_text = strip_kaper_markdown(yield_text or "")
    for lst in ingredients.values():
        for it in lst:
            it.name = strip_kaper_markdown(it.name)
            it.note = strip_kaper_markdown(it.note or "")
            it.unit = strip_kaper_markdown(it.unit or "")
    steps = shape_steps(steps, infer=True)

    names = [i.name for g in ingredients.values() for i in g]
    prep, cook, total = recon_sum(steps)

    return ParsedRecipe(
        title=title,
        servings=int(servings),
        yield_text=yield_text,
        prep_time=prep or "",
        cook_time=cook or "",
        total_time=total or "",
        oven_temp_f=oven,
        difficulty=difficulty,
        course=course,
        cuisine=cuisine_of(title, raw),
        tags=[strip_kaper_markdown(t) for t in tags],
        ingredients=ingredients,
        steps=steps,
        allergens=allergens_of(names + [title]),
        ocr_ok=ocr_ok,
    )


def flatten_ings(parsed: ParsedRecipe) -> list[dict]:
    items = []
    for group in parsed.ingredients.values():
        for it in group:
            items.append({"amount": it.amount, "unit": it.unit, "name": it.name, "note": it.note})
    return items


def flatten_names(parsed: ParsedRecipe) -> list[str]:
    return [it["name"] for it in flatten_ings(parsed)]


def folder_for(course: str) -> str:
    if course in COURSE_FOLDER:
        return f"Recipes/{COURSE_FOLDER[course]}"
    return "Recipes/_Inbox"


def sanitize_filename(title: str) -> str:
    name = title.strip() or "Untitled"
    name = re.sub(r"^#+\s*", "", name)
    name = name.strip("*_ ")
    name = re.sub(r'[\\/:*?"<>|]', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:90] or "Untitled"
