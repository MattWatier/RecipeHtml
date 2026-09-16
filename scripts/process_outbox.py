#!/usr/bin/env python3
"""Process notes dropped in Recipes/_Outbox.

Reads ## RAW OCR from the note (sidecar only if OCR is missing),
reconstructs the recipe (markdown headings, ingredients inside
directions, shorthand verb+list, summed/inferred timings),
builds a Kaper block + cooking frontmatter, estimates per-serving
nutrition via the USDA API, then files the note into a meal folder.

Usage:
  python Scripts/process_outbox.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from cards import load_index, save_index  # noqa: E402
from notes import (  # noqa: E402
    KAPER_FENCE,
    card_id_from_note,
    collect_source_section,
    extract_field,
    extract_raw_ocr,
    new_kaper_id,
    patch_kaper_and_frontmatter,
    raw_ocr_heading_present,
    render_note,
    unique_path,
)
from parse import flatten_ings, folder_for, parse_recipe, sanitize_filename  # noqa: E402
from sidecar import combine_sidecar_text, parse_ocr_blob, read_sidecar  # noqa: E402

OUTBOX = ROOT / "Recipes" / "_Outbox"
INBOX = ROOT / "Recipes" / "_Inbox"
MEAL_FOLDERS = (
    "Breakfast",
    "Mains",
    "Sides",
    "Desserts",
    "Drinks",
    "Condiments",
)
PLACEHOLDER_OCR = "(no text recovered)"
EMPTY_NUTR = {
    "calories": "",
    "protein_g": "",
    "fat_g": "",
    "carbs_g": "",
    "nutrition_confidence": "unknown",
}


def _usable(body: str | None) -> bool:
    if body is None:
        return False
    s = body.strip()
    return bool(s) and s != PLACEHOLDER_OCR


def era_from_card_id(card_id: str) -> str:
    if "1980s" in (card_id or ""):
        return "1980s"
    if "1990s" in (card_id or ""):
        return "1990s"
    return ""


def card_sides_from_text(text: str) -> str:
    has_a = bool(re.search(r"_[aA]\.jpe?g", text))
    has_b = bool(re.search(r"_[bB]\.jpe?g", text))
    if has_a and has_b:
        return "ab"
    if has_b and not has_a:
        return "b"
    return "a"


def resolve_ocr(card_id: str, note_text: str) -> tuple[str, str]:
    """Notes win. Return (ocr_for_parse, source)."""
    if raw_ocr_heading_present(note_text):
        body = extract_raw_ocr(note_text) or ""
        if _usable(body):
            front, back = parse_ocr_blob(body)
            return combine_sidecar_text(front, back), "note"
        if body.strip():
            return body, "note"
    if card_id:
        got = read_sidecar(card_id)
        if got and _usable(got[2]):
            return got[2], "sidecar"
    return "", "empty"


def dest_base(parsed, src: Path) -> str:
    title = (parsed.title or "").strip()
    if title and not title.lower().startswith("untitled"):
        return sanitize_filename(title)
    return sanitize_filename(src.stem)


def estimate_nutrition(parsed, key: str, session, cache: dict) -> dict:
    if not key or session is None:
        return dict(EMPTY_NUTR)
    from nutrition import estimate_recipe

    try:
        return estimate_recipe(
            flatten_ings(parsed), parsed.servings, session, key, cache
        )
    except Exception as exc:
        print(f"  nutrition error: {type(exc).__name__}", file=sys.stderr)
        return dict(EMPTY_NUTR)


def process_one(
    path: Path,
    *,
    key: str,
    session,
    nut_cache: dict,
    index: dict,
    reserved: set[str],
) -> tuple[str, Path | None, str]:
    text = path.read_text(encoding="utf-8")
    card_id = card_id_from_note(text)
    kaper_id = extract_field(text, "kaper") or new_kaper_id()
    ocr_text, ocr_source = resolve_ocr(card_id, text)
    if not _usable(ocr_text):
        ocr_text = extract_raw_ocr(text) or ocr_text or ""
    if not card_id:
        return "failed", None, "missing card_id (no FastFoto wiki link or YAML)"
    if not _usable(ocr_text):
        return "failed", None, "missing RAW OCR"

    parsed = parse_recipe(ocr_text, card_id)  # reconstruct.py agent
    nutr = estimate_nutrition(parsed, key, session, nut_cache)
    source_section = collect_source_section(text)
    era = era_from_card_id(card_id)
    dest_folder = ROOT / folder_for(parsed.course)
    dest_folder.mkdir(parents=True, exist_ok=True)
    dest = unique_path(
        dest_folder,
        dest_base(parsed, path),
        era,
        reserved,
        allow=path if path.parent.resolve() == dest_folder.resolve() else None,
    )
    reserved.add(str(dest))

    # Keep ## RAW OCR as written. Patch kaper + frontmatter when the note
    # already has a recipe body; otherwise render a full note from this OCR.
    original_ocr = extract_raw_ocr(text) if raw_ocr_heading_present(text) else None
    if KAPER_FENCE.search(text):
        note = patch_kaper_and_frontmatter(
            text,
            parsed=parsed,
            card_id=card_id,
            nutrition=nutr,
            possible_duplicates=[],
        )
    else:
        note = render_note(
            kaper_id=kaper_id,
            parsed=parsed,
            card_id=card_id,
            card_sides=card_sides_from_text(text),
            era=era,
            image_paths=[],
            original_paths=None,
            ocr_text=original_ocr if _usable(original_ocr) else ocr_text,
            nutrition=nutr,
            possible_duplicates=[],
            status="ocr-draft",
            source_section=source_section,
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(note, encoding="utf-8")
    if dest.resolve() != path.resolve():
        path.unlink()

    rec = index.setdefault("cards", {}).setdefault(card_id, {})
    rec["kaper_id"] = kaper_id
    rec["path"] = str(dest.relative_to(ROOT))
    rec["title"] = parsed.title
    rec["course"] = parsed.course
    rec["ocr_ok"] = parsed.ocr_ok
    rec["sidecar"] = f"Scripts/ocr_raw/{card_id}.txt"
    return "processed", dest, ocr_source


def run() -> dict:
    OUTBOX.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)
    for name in MEAL_FOLDERS:
        (ROOT / "Recipes" / name).mkdir(parents=True, exist_ok=True)

    notes = sorted(p for p in OUTBOX.glob("*.md") if p.is_file())
    if not notes:
        report = {"processed": 0, "failed": 0, "skipped": "empty outbox"}
        print(json.dumps(report, indent=2))
        return report

    from nutrition import load_cache as load_nut_cache, load_key, save_cache as save_nut_cache

    key = load_key()
    if not key:
        print("No USDA_API_KEY in Scripts/.env; nutrition will be blank.", file=sys.stderr)

    session = None
    nut_cache: dict = {}
    if key:
        import requests

        session = requests.Session()
        nut_cache = load_nut_cache()

    index = load_index()
    reserved = {str(p) for p in (ROOT / "Recipes").rglob("*.md")}
    processed = []
    failed = []

    print(f"Processing {len(notes)} outbox note(s)...", flush=True)
    for path in notes:
        try:
            action, dest, detail = process_one(
                path,
                key=key,
                session=session,
                nut_cache=nut_cache,
                index=index,
                reserved=reserved,
            )
        except Exception as exc:
            action, dest, detail = "failed", None, f"{type(exc).__name__}: {exc}"
        if action == "processed":
            processed.append(
                {
                    "from": str(path.relative_to(ROOT)),
                    "to": str(dest.relative_to(ROOT)) if dest else "",
                    "ocr": detail,
                }
            )
            dest_rel = dest.relative_to(ROOT) if dest else "?"
            print(f"  ok {path.name} → {dest_rel}", flush=True)
        else:
            failed.append({"path": str(path.relative_to(ROOT)), "error": detail})
            print(f"  fail {path.name}: {detail}", file=sys.stderr)

    if key:
        save_nut_cache(nut_cache)
    save_index(index)

    report = {
        "processed": len(processed),
        "failed": len(failed),
        "notes": processed,
        "failures": failed,
    }
    print(json.dumps(report, indent=2))
    return report


def main() -> None:
    run()


if __name__ == "__main__":
    main()
