"""Emit Obsidian notes with frontmatter + Kaper YAML + footer scans."""

from __future__ import annotations

import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KAPER_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
PROTECTED_STATUS = {"reviewed", "human-reviewed", "done", "final"}


def new_kaper_id() -> str:
    return "r_" + "".join(secrets.choice(KAPER_ALPHABET) for _ in range(10))


def yaml_quote(value) -> str:
    if value is None:
        return '""'
    s = str(value)
    if s == "":
        return '""'
    if (
        re.match(r"^\d+\.", s)
        or re.search(r'[:#{}[\],&*!|>%@`"\']', s)
        or s.strip() != s
        or s.lower() in {"yes", "no", "true", "false", "on", "off", "null"}
    ):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def fmt_num(n: float) -> str:
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    return f"{n:.3f}".rstrip("0").rstrip(".")


def emit_kaper(parsed, source: str) -> str:
    from parse import strip_kaper_markdown
    from reconstruct import shape_steps

    def q(v) -> str:
        return yaml_quote(strip_kaper_markdown(v) if v is not None and not isinstance(v, (int, float)) else v)

    steps = shape_steps(list(parsed.steps or []), infer=True)
    lines = [
        "version: 1",
        f"title: {q(parsed.title)}",
        f"servings: {int(parsed.servings)}",
        f"difficulty: {strip_kaper_markdown(parsed.difficulty)}",
    ]
    tags = [strip_kaper_markdown(t) for t in (parsed.tags or []) if t]
    if tags:
        lines.append("tags: [" + ", ".join(yaml_quote(t) for t in tags) + "]")
    else:
        lines.append("tags: []")
    lines.append("time:")
    lines.append(f"  prep: {yaml_quote(parsed.prep_time) if parsed.prep_time else '\"\"'}")
    lines.append(f"  cook: {yaml_quote(parsed.cook_time) if parsed.cook_time else '\"\"'}")
    lines.append(f"  total: {yaml_quote(parsed.total_time) if parsed.total_time else '\"\"'}")
    lines.append("ingredients:")
    groups = parsed.ingredients or {"main": []}
    if not groups:
        groups = {"main": []}
    for gname, items in groups.items():
        key = re.sub(r"[^A-Za-z0-9_]+", "_", gname).strip("_") or "main"
        lines.append(f"  {key}:")
        if not items:
            lines.append("    []")
            continue
        for it in items:
            lines.append(f"    - amount: {fmt_num(it.amount)}")
            lines.append(f"      unit: {q(it.unit)}")
            lines.append(f"      name: {q(it.name)}")
            if it.note:
                lines.append(f"      note: {q(it.note)}")
    lines.append("steps:")
    if not steps:
        lines.append("  []")
    else:
        for st in steps:
            lines.append(f"  - title: {q(st.title)}")
            if st.duration:
                lines.append(f"    duration: {yaml_quote(st.duration)}")
            if st.note:
                lines.append(f"    note: {q(st.note)}")
            if st.tip:
                lines.append(f"    tip: {q(st.tip)}")
    lines.append(f"source: {yaml_quote(source)}")
    lines.append(f"yield: {q(parsed.yield_text)}")
    return "\n".join(lines) + "\n"


def fm_scalar(v) -> str:
    if v is None or v == "":
        return '""'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return fmt_num(v)
    return yaml_quote(v)


def fm_list(items: list) -> str:
    if not items:
        return "[]"
    return "\n" + "\n".join(f"  - {yaml_quote(x)}" for x in items)


def relative_wiki(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    return f"![[{rel}]]"


def extract_source_cards(text: str) -> str | None:
    idx = text.find("## Source cards")
    if idx < 0:
        return None
    section = text[idx:].rstrip() + "\n"
    if "FastFoto/rotated/" not in section and "![[" not in section:
        return None
    return section


def extract_field(text: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*(.*)$", text, re.M)
    if not m:
        return ""
    return m.group(1).strip().strip('"')


CARD_ID_WIKI = re.compile(
    r"FastFoto/(?:rotated/)?(?:1980s_reciepes|1990s_reciepes)/"
    r"((?:1980s|1990s)_reciepes_\d+)_[ab]\.",
    re.I,
)
EMBED_WIKI = re.compile(r"!\[\[[^\]]+\]\]")
ORIGINAL_WIKI = re.compile(r"(?<!!)\[\[(FastFoto/(?!rotated/)[^\]]+)\]\]")


def card_id_from_wikilinks(text: str) -> str:
    """Prefer FastFoto/rotated wiki targets, then original scan links."""
    rotated = ""
    original = ""
    for m in CARD_ID_WIKI.finditer(text):
        cid = m.group(1)
        prefix = text[max(0, m.start() - 24) : m.start()]
        if "rotated/" in prefix:
            if not rotated:
                rotated = cid
        elif not original:
            original = cid
    return rotated or original


def card_id_from_note(text: str) -> str:
    return card_id_from_wikilinks(text) or extract_field(text, "card_id")


def collect_source_section(text: str) -> str:
    """Keep rotated image embeds (and original wiki links) as the footer."""
    existing = extract_source_cards(text)
    if existing:
        return existing.rstrip() + "\n"
    embeds = EMBED_WIKI.findall(text)
    originals = ORIGINAL_WIKI.findall(text)
    lines = ["## Source cards", ""]
    if embeds:
        lines.extend(embeds)
    if originals:
        if embeds:
            lines.append("")
        links = " · ".join(f"[[{p}]]" for p in originals)
        lines.append(f"Original (unrotated): {links}")
    lines.append("")
    return "\n".join(lines)


RAW_OCR_HEADING = re.compile(r"^## RAW OCR\s*$", re.M)
KAPER_FENCE = re.compile(r"```kaper\n.*?```", re.S)
OCR_DRAFT_BLOCK = re.compile(
    r"^## OCR draft\s*\n.*?</details>[ \t]*\n*",
    re.M | re.S,
)
DETAILS_OCR_BLOCK = re.compile(
    r"<details>\s*<summary>\s*Raw OCR\s*</summary>\s*\n*```[^\n]*\n(.*?)```\s*</details>",
    re.S | re.I,
)
PLACEHOLDER_OCR = "(no text recovered)"


def raw_ocr_heading_present(text: str) -> bool:
    return bool(RAW_OCR_HEADING.search(text))


def _usable_ocr(body: str | None) -> bool:
    if body is None:
        return False
    s = body.strip()
    return bool(s) and s != PLACEHOLDER_OCR


def extract_details_ocr(text: str) -> str | None:
    m = DETAILS_OCR_BLOCK.search(text)
    if not m:
        return None
    return m.group(1).rstrip("\n")


SOURCE_CARDS_HEADING = re.compile(r"^## Source cards\s*$", re.M)


def extract_raw_ocr(text: str) -> str | None:
    """Inner text of ## RAW OCR if present, else collapsed details. None if neither.

    Unfenced markdown OCR may contain ## Ingredients / ## Instructions; only
    stop the section at ## Source cards so those headings stay in the OCR.
    """
    m = RAW_OCR_HEADING.search(text)
    if m:
        rest = text[m.end() :]
        next_h = SOURCE_CARDS_HEADING.search(rest)
        section = rest[: next_h.start()] if next_h else rest
        fence = re.search(r"```[^\n]*\n(.*?)```", section, re.S)
        if fence:
            body = fence.group(1).rstrip("\n")
            # Fence-escape leftover from render_raw_ocr_section
            body = re.sub(r"^'''\s*\n?", "", body)
            body = re.sub(r"\n?'''\s*$", "", body)
            return body.rstrip("\n")
        return section.strip()
    return extract_details_ocr(text)


def render_raw_ocr_section(ocr_text: str) -> str:
    raw = (ocr_text or "").rstrip("\n") or PLACEHOLDER_OCR
    raw = raw.replace("```", "'''")
    return f"## RAW OCR\n\n```\n{raw}\n```\n"


def render_stripped_note(*, kaper_id: str, card_id: str, ocr_text: str, source_section: str) -> str:
    """Inbox/holding note: plugin id + card_id, RAW OCR, source images. No recipe fields."""
    lines = ["---"]
    if kaper_id:
        lines.append(f"kaper: {kaper_id}")
    if card_id:
        lines.append(f"card_id: {card_id}")
    lines.append("---")
    lines.append("")
    lines.append(render_raw_ocr_section(ocr_text).rstrip())
    lines.append("")
    src = (source_section or "").strip() or "## Source cards"
    if not src.startswith("## Source cards"):
        src = "## Source cards\n\n" + src
    lines.append(src.rstrip())
    lines.append("")
    return "\n".join(lines)


def ensure_visible_raw_ocr(text: str, fallback: str = "") -> tuple[str, bool]:
    """Insert/convert a visible ## RAW OCR section. Never overwrite an existing one."""
    if raw_ocr_heading_present(text):
        return text, False
    body = extract_details_ocr(text)
    if not _usable_ocr(body):
        body = fallback if _usable_ocr(fallback) else (body or fallback or "")
    section = render_raw_ocr_section(body)
    if OCR_DRAFT_BLOCK.search(text):
        return OCR_DRAFT_BLOCK.sub(section + "\n", text, count=1), True
    if DETAILS_OCR_BLOCK.search(text):
        return DETAILS_OCR_BLOCK.sub(section + "\n", text, count=1), True
    src = re.search(r"^## Source cards\s*$", text, re.M)
    if src:
        return text[: src.start()] + section + "\n" + text[src.start() :], True
    return text.rstrip() + "\n\n" + section, True


def replace_kaper_fence(text: str, kaper_yaml: str) -> str:
    new = "```kaper\n" + kaper_yaml.rstrip() + "\n```"
    if KAPER_FENCE.search(text):
        return KAPER_FENCE.sub(lambda _m: new, text, count=1)
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    if m:
        return text[: m.end()] + new + "\n" + text[m.end() :]
    return new + "\n" + text


def replace_fm_scalar(text: str, key: str, value: str) -> str:
    pat = re.compile(rf"^{re.escape(key)}:\s*.*$", re.M)
    if not pat.search(text):
        return text
    return pat.sub(lambda _m: f"{key}: {value}", text, count=1)


def replace_fm_list(text: str, key: str, items: list) -> str:
    block = f"{key}: {fm_list(items)}"
    pat = re.compile(
        rf"^{re.escape(key)}:[ \t]*(?:\[\])?(?:\n(?:[ \t]+-[^\n]*)+)?",
        re.M,
    )
    if not pat.search(text):
        return text
    return pat.sub(lambda _m: block, text, count=1)


def patch_kaper_and_frontmatter(
    text: str,
    *,
    parsed,
    card_id: str,
    nutrition: dict,
    possible_duplicates: list[str],
) -> str:
    """Replace ```kaper and derived frontmatter. Leaves ## RAW OCR untouched."""
    source = f"family-card:{card_id}"
    text = replace_kaper_fence(text, emit_kaper(parsed, source))
    nutr = nutrition or {}
    oven = parsed.oven_temp_f if parsed.oven_temp_f else '""'
    text = replace_fm_scalar(text, "course", fm_scalar(parsed.course or ""))
    text = replace_fm_scalar(text, "cuisine", fm_scalar(parsed.cuisine))
    text = replace_fm_scalar(text, "servings", str(int(parsed.servings)))
    text = replace_fm_scalar(text, "yield", fm_scalar(parsed.yield_text))
    text = replace_fm_scalar(text, "prep_time", fm_scalar(parsed.prep_time))
    text = replace_fm_scalar(text, "cook_time", fm_scalar(parsed.cook_time))
    text = replace_fm_scalar(text, "total_time", fm_scalar(parsed.total_time))
    text = replace_fm_scalar(text, "oven_temp_f", str(oven) if parsed.oven_temp_f else '""')
    text = replace_fm_scalar(text, "calories", fm_scalar(nutr.get("calories", "")))
    text = replace_fm_scalar(text, "protein_g", fm_scalar(nutr.get("protein_g", "")))
    text = replace_fm_scalar(text, "fat_g", fm_scalar(nutr.get("fat_g", "")))
    text = replace_fm_scalar(text, "carbs_g", fm_scalar(nutr.get("carbs_g", "")))
    text = replace_fm_scalar(
        text, "nutrition_confidence", nutr.get("nutrition_confidence", "unknown") or "unknown"
    )
    text = replace_fm_list(text, "allergens", parsed.allergens or [])
    text = replace_fm_list(text, "tags", parsed.tags or [])
    text = replace_fm_list(text, "possible_duplicates", possible_duplicates or [])
    return text


def migrate_all_raw_ocr_sections() -> dict:
    """One-pass: give every note a visible ## RAW OCR section without rewriting OCR text."""
    from sidecar import read_sidecar

    existing = find_notes_by_card_id()
    migrated = already = missing_text = 0
    for cid, path in existing.items():
        text = path.read_text(encoding="utf-8")
        fallback = ""
        got = read_sidecar(cid)
        if got:
            fallback = got[2]
        new_text, changed = ensure_visible_raw_ocr(text, fallback=fallback)
        if raw_ocr_heading_present(new_text) and not _usable_ocr(extract_raw_ocr(new_text)):
            missing_text += 1
        if changed:
            path.write_text(new_text, encoding="utf-8")
            migrated += 1
        else:
            already += 1
    return {
        "notes": len(existing),
        "migrated": migrated,
        "already_had_section": already,
        "empty_or_placeholder": missing_text,
    }


def render_source_cards(image_paths: list[Path], original_paths: list[Path] | None = None) -> str:
    lines = ["## Source cards", ""]
    for p in image_paths:
        lines.append(relative_wiki(p))
    if original_paths:
        links = " · ".join(f"[[{p.relative_to(ROOT).as_posix()}]]" for p in original_paths)
        lines.append("")
        lines.append(f"Original (unrotated): {links}")
    lines.append("")
    return "\n".join(lines)


def render_note(
    *,
    kaper_id: str,
    parsed,
    card_id: str,
    card_sides: str,
    era: str,
    image_paths: list[Path],
    original_paths: list[Path] | None = None,
    ocr_text: str,
    nutrition: dict,
    possible_duplicates: list[str],
    status: str = "ocr-draft",
    source_section: str | None = None,
) -> str:
    source = f"family-card:{card_id}"
    kaper = emit_kaper(parsed, source)
    course = parsed.course or ""
    nutr = nutrition or {}
    body = []
    body.append("---")
    body.append(f"kaper: {kaper_id}")
    body.append("type: recipe")
    body.append(f"course: {fm_scalar(course)}")
    body.append(f"cuisine: {fm_scalar(parsed.cuisine)}")
    body.append(f"era: {era}")
    body.append(f"status: {status or 'ocr-draft'}")
    body.append(f"card_id: {card_id}")
    body.append(f"card_sides: {card_sides}")
    body.append(f"servings: {int(parsed.servings)}")
    body.append(f"yield: {fm_scalar(parsed.yield_text)}")
    body.append(f"prep_time: {fm_scalar(parsed.prep_time)}")
    body.append(f"cook_time: {fm_scalar(parsed.cook_time)}")
    body.append(f"total_time: {fm_scalar(parsed.total_time)}")
    body.append(f"oven_temp_f: {parsed.oven_temp_f if parsed.oven_temp_f else '\"\"'}")
    body.append(f"calories: {fm_scalar(nutr.get('calories', ''))}")
    body.append(f"protein_g: {fm_scalar(nutr.get('protein_g', ''))}")
    body.append(f"fat_g: {fm_scalar(nutr.get('fat_g', ''))}")
    body.append(f"carbs_g: {fm_scalar(nutr.get('carbs_g', ''))}")
    body.append(f"nutrition_confidence: {nutr.get('nutrition_confidence', 'unknown')}")
    body.append("nutrition_source: usda")
    body.append(f"allergens: {fm_list(parsed.allergens)}")
    body.append(f"tags: {fm_list(parsed.tags)}")
    body.append(f"possible_duplicates: {fm_list(possible_duplicates)}")
    body.append("---")
    body.append("```kaper")
    body.append(kaper.rstrip())
    body.append("```")
    body.append("")
    body.append(render_raw_ocr_section(ocr_text).rstrip())
    body.append("")
    if source_section:
        body.append(source_section.rstrip())
        body.append("")
    else:
        body.append(render_source_cards(image_paths, original_paths).rstrip())
        body.append("")
    return "\n".join(body)


def update_or_create_note(
    *,
    card: dict,
    parsed,
    ocr_text: str,
    nutrition: dict,
    duplicates: list[str],
    index: dict,
    existing: dict[str, Path],
    reserved: set[str],
    force: bool = False,
) -> tuple[Path | None, str]:
    """Write or update a note. Never rename an existing file. Skip reviewed unless force.

    Existing notes: replace ```kaper + derived frontmatter only. Never overwrite ## RAW OCR.
    """
    from parse import folder_for, sanitize_filename

    card_id = card["card_id"]
    era = card["era"]
    rec = index.setdefault("cards", {}).setdefault(card_id, {})
    old_path = existing.get(card_id)
    status = "ocr-draft"
    source_section = None
    kaper_id = rec.get("kaper_id") or ""

    if old_path and old_path.exists():
        old_text = old_path.read_text(encoding="utf-8")
        status = extract_field(old_text, "status") or "ocr-draft"
        kaper_id = extract_field(old_text, "kaper") or kaper_id
        path = old_path
        text, _migrated = ensure_visible_raw_ocr(old_text, fallback=ocr_text)
        if status.lower() in PROTECTED_STATUS and not force:
            if text != old_text:
                path.write_text(text, encoding="utf-8")
            existing[card_id] = path
            rec["kaper_id"] = kaper_id or rec.get("kaper_id") or ""
            rec["path"] = str(path.relative_to(ROOT))
            rec["sidecar"] = f"Scripts/ocr_raw/{card_id}.txt"
            return path, "skipped-reviewed"
        if not kaper_id:
            kaper_id = new_kaper_id()
        rec["kaper_id"] = kaper_id
        text = patch_kaper_and_frontmatter(
            text,
            parsed=parsed,
            card_id=card_id,
            nutrition=nutrition,
            possible_duplicates=duplicates,
        )
        path.write_text(text, encoding="utf-8")
        existing[card_id] = path
        rec["path"] = str(path.relative_to(ROOT))
        rec["title"] = parsed.title
        rec["course"] = parsed.course
        rec["ocr_ok"] = parsed.ocr_ok
        rec["sidecar"] = f"Scripts/ocr_raw/{card_id}.txt"
        return path, "updated"

    if not kaper_id:
        kaper_id = new_kaper_id()
    rec["kaper_id"] = kaper_id
    folder = ROOT / folder_for(parsed.course)
    base = sanitize_filename(parsed.title)
    path = unique_path(folder, base, era, reserved, allow=None)
    reserved.add(str(path))

    images = list(card.get("rotated") or [])
    originals = [card["a"]]
    if card.get("b"):
        originals.append(card["b"])
    if not images:
        images = originals

    text = render_note(
        kaper_id=kaper_id,
        parsed=parsed,
        card_id=card_id,
        card_sides=card["sides"],
        era=era,
        image_paths=images,
        original_paths=originals,
        ocr_text=ocr_text,
        nutrition=nutrition,
        possible_duplicates=duplicates,
        status=status,
        source_section=source_section,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    existing[card_id] = path
    rec["path"] = str(path.relative_to(ROOT))
    rec["title"] = parsed.title
    rec["course"] = parsed.course
    rec["ocr_ok"] = parsed.ocr_ok
    rec["sidecar"] = f"Scripts/ocr_raw/{card_id}.txt"
    return path, "created"


def _free(path: Path, reserved: set[str], allow: Path | None) -> bool:
    if allow and path.resolve() == allow.resolve():
        return True
    if str(path) in reserved:
        return False
    return not path.exists()


def unique_path(folder: Path, base: str, era: str, reserved: set[str], allow: Path | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / f"{base}.md"
    if _free(candidate, reserved, allow):
        return candidate
    era_name = f"{base} {era}"
    candidate = folder / f"{era_name}.md"
    if _free(candidate, reserved, allow):
        return candidate
    n = 2
    while True:
        candidate = folder / f"{base} ({n}).md"
        if _free(candidate, reserved, allow):
            return candidate
        n += 1


def patch_possible_duplicates(path: Path, values: list[str]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    block = f"possible_duplicates: {fm_list(values)}"
    pat = re.compile(r"^possible_duplicates:[ \t]*(?:\[\])?(?:\n(?:[ \t]+-[^\n]*)+)?", re.M)
    if not pat.search(text):
        return
    path.write_text(pat.sub(block, text, count=1), encoding="utf-8")


def find_notes_by_card_id() -> dict[str, Path]:
    found = {}
    recipes = ROOT / "Recipes"
    if not recipes.exists():
        return found
    for p in recipes.rglob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        cid = card_id_from_note(text)
        if cid:
            found[cid] = p
    return found


MEAL_NOTE_FOLDERS = (
    "Breakfast",
    "Mains",
    "Sides",
    "Desserts",
    "Drinks",
    "Condiments",
    "_Inbox",
    "_Outbox",
)
_QUOTE_KEYS = ("title", "note", "name", "tip", "source", "yield")


def _quote_kaper_plain_scalars(raw: str) -> str:
    def repl(m):
        key, val = m.group(1), (m.group(2) or "").rstrip()
        if not val or val[0] in "\"'|[{":
            return m.group(0)
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        return f'{key}: "{escaped}"'

    keys = "|".join(_QUOTE_KEYS)
    return re.sub(rf"^(\s*(?:- )?(?:{keys})):(?:\s+(.*))?$", repl, raw, flags=re.M)


def _strip_md_tree(obj):
    from parse import strip_kaper_markdown

    if isinstance(obj, str):
        return strip_kaper_markdown(obj)
    if isinstance(obj, list):
        return [_strip_md_tree(x) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "amount":
                out[k] = v
            else:
                out[k] = _strip_md_tree(v)
        return out
    return obj


def emit_kaper_doc(doc: dict) -> str:
    from parse import strip_kaper_markdown

    def sc(v) -> str:
        if v is None:
            return '""'
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float):
            return fmt_num(v)
        return yaml_quote(strip_kaper_markdown(str(v)))

    lines = []
    lines.append(f"version: {doc.get('version', 1)}")
    lines.append(f"title: {sc(doc.get('title') or '')}")
    servings = doc.get("servings", 4)
    try:
        lines.append(f"servings: {int(servings)}")
    except (TypeError, ValueError):
        lines.append(f"servings: {sc(servings)}")
    if "difficulty" in doc:
        lines.append(f"difficulty: {sc(doc.get('difficulty') or 'medium')}")
    tags = doc.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    tags = [strip_kaper_markdown(str(t)) for t in tags if t is not None and str(t).strip() != ""]
    if tags:
        lines.append("tags: [" + ", ".join(yaml_quote(t) for t in tags) + "]")
    else:
        lines.append("tags: []")
    time = doc.get("time") or {}
    if not isinstance(time, dict):
        time = {}
    lines.append("time:")
    for tk in ("prep", "cook", "total"):
        val = time.get(tk) or ""
        lines.append(f"  {tk}: {yaml_quote(val) if val else '\"\"'}")
    for tk, val in time.items():
        if tk not in ("prep", "cook", "total"):
            lines.append(f"  {tk}: {sc(val)}")
    lines.append("ingredients:")
    groups = doc.get("ingredients") or {}
    if not isinstance(groups, dict) or not groups:
        groups = {"main": []}
    for gname, items in groups.items():
        key = re.sub(r"[^A-Za-z0-9_]+", "_", str(gname)).strip("_") or "main"
        lines.append(f"  {key}:")
        if not items:
            lines.append("    []")
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            amt = it.get("amount", 0)
            if isinstance(amt, (int, float)):
                amt_s = fmt_num(amt)
            else:
                amt_s = str(amt)
            lines.append(f"    - amount: {amt_s}")
            emitted = {"amount"}
            for ik in ("unit", "name", "note"):
                if ik in it and it[ik] is not None and str(it[ik]) != "":
                    lines.append(f"      {ik}: {sc(it[ik])}")
                    emitted.add(ik)
                elif ik == "name":
                    lines.append(f"      name: {sc(it.get('name') or '')}")
                    emitted.add(ik)
            for ik, iv in it.items():
                if ik not in emitted:
                    lines.append(f"      {ik}: {sc(iv)}")
    lines.append("steps:")
    steps = doc.get("steps") or []
    if not steps:
        lines.append("  []")
    else:
        for st in steps:
            if not isinstance(st, dict):
                continue
            lines.append(f"  - title: {sc(st.get('title') or 'Prepare')}")
            emitted = {"title"}
            for sk in ("duration", "note", "tip"):
                if st.get(sk):
                    lines.append(f"    {sk}: {sc(st[sk])}")
                    emitted.add(sk)
            for sk, sv in st.items():
                if sk not in emitted and sv not in (None, ""):
                    lines.append(f"    {sk}: {sc(sv)}")
    known = {
        "version", "title", "servings", "difficulty", "tags", "time",
        "ingredients", "steps", "source", "yield",
    }
    if "source" in doc:
        lines.append(f"source: {sc(doc.get('source') or '')}")
    if "yield" in doc:
        lines.append(f"yield: {sc(doc.get('yield') or '')}")
    for k, v in doc.items():
        if k not in known:
            lines.append(f"{k}: {sc(v)}")
    return "\n".join(lines) + "\n"


def rewrite_kaper_steps_in_vault() -> dict:
    """Rewrite Kaper steps + strip markdown in meal folders and _Inbox."""
    import yaml
    from parse import Step, drop_ingredient_name
    from reconstruct import shape_steps, sum_buckets

    recipes = ROOT / "Recipes"
    patched = []
    unchanged = []
    failures = []
    samples = []
    for folder in MEAL_NOTE_FOLDERS:
        d = recipes / folder
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                failures.append({"path": str(path), "error": str(e)})
                continue
            m = KAPER_FENCE.search(text)
            if not m:
                continue
            raw = m.group(0)
            yaml_text = re.sub(r"^```kaper\s*", "", raw)
            yaml_text = re.sub(r"```\s*$", "", yaml_text)
            yaml_text = yaml_text.replace("\t", "  ")
            try:
                doc = yaml.safe_load(_quote_kaper_plain_scalars(yaml_text))
            except Exception as e:
                failures.append({"path": str(path.relative_to(ROOT)), "error": f"yaml: {e}"})
                continue
            if not isinstance(doc, dict):
                failures.append({"path": str(path.relative_to(ROOT)), "error": "kaper is not a mapping"})
                continue
            doc = _strip_md_tree(doc)
            groups = doc.get("ingredients") or {}
            if isinstance(groups, dict):
                cleaned_groups = {}
                for gname, items in groups.items():
                    if not isinstance(items, list):
                        cleaned_groups[gname] = items
                        continue
                    cleaned_groups[gname] = [
                        it
                        for it in items
                        if not (
                            isinstance(it, dict)
                            and drop_ingredient_name(str(it.get("name") or ""))
                        )
                    ]
                doc["ingredients"] = cleaned_groups
            raw_steps = doc.get("steps") or []
            step_objs = []
            extras = []
            if isinstance(raw_steps, list):
                for st in raw_steps:
                    if not isinstance(st, dict):
                        continue
                    step_objs.append(
                        Step(
                            title=str(st.get("title") or ""),
                            duration=(str(st["duration"]) if st.get("duration") else None),
                            note=(str(st["note"]) if st.get("note") else None),
                            tip=(str(st["tip"]) if st.get("tip") else None),
                        )
                    )
                    extras.append(
                        {k: v for k, v in st.items() if k not in {"title", "duration", "note", "tip"}}
                    )
            shaped = shape_steps(step_objs, infer=True)
            new_steps = []
            for i, st in enumerate(shaped):
                item = {"title": st.title}
                if st.duration:
                    item["duration"] = st.duration
                if st.note:
                    item["note"] = st.note
                if st.tip:
                    item["tip"] = st.tip
                extra = extras[i] if i < len(extras) and isinstance(extras[i], dict) else {}
                item.update(extra)
                new_steps.append(item)
            doc["steps"] = new_steps
            prep, cook, total = sum_buckets(shaped)
            time = doc.get("time") if isinstance(doc.get("time"), dict) else {}
            time = dict(time or {})
            time["prep"] = prep or ""
            time["cook"] = cook or ""
            time["total"] = total or ""
            doc["time"] = time
            new_yaml = emit_kaper_doc(doc)
            new_text = replace_kaper_fence(text, new_yaml)
            new_text = replace_fm_scalar(new_text, "prep_time", fm_scalar(prep or ""))
            new_text = replace_fm_scalar(new_text, "cook_time", fm_scalar(cook or ""))
            new_text = replace_fm_scalar(new_text, "total_time", fm_scalar(total or ""))
            rel = str(path.relative_to(ROOT))
            if new_text != text:
                path.write_text(new_text, encoding="utf-8")
                patched.append(rel)
                if len(samples) < 2:
                    samples.append({"path": rel, "steps": new_steps})
            else:
                unchanged.append(rel)
    return {
        "patched": len(patched),
        "unchanged": len(unchanged),
        "failures": failures,
        "samples": samples,
    }
