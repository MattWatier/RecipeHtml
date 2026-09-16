#!/usr/bin/env python3
"""Re-file vault notes into breakfast / mains / sides / drinks / desserts / condiments."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from cards import load_index, save_index  # noqa: E402
from notes import (  # noqa: E402
    KAPER_FENCE,
    _quote_kaper_plain_scalars,
    card_id_from_note,
    extract_field,
    fm_scalar,
    replace_fm_scalar,
    unique_path,
)
from parse import classify_course, folder_for, sanitize_filename  # noqa: E402

RECIPES = ROOT / "Recipes"
SCAN_FOLDERS = (
    "Breakfast",
    "Mains",
    "Sides",
    "Desserts",
    "Drinks",
    "Condiments",
    "Lunch",
    "Dinner",
    "Snacks",
    "_Inbox",
)


def kaper_doc(text: str) -> dict:
    m = KAPER_FENCE.search(text)
    if not m:
        return {}
    yaml_text = re.sub(r"^```kaper\s*", "", m.group(0))
    yaml_text = re.sub(r"```\s*$", "", yaml_text).replace("\t", "  ")
    try:
        doc = yaml.safe_load(_quote_kaper_plain_scalars(yaml_text))
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


def classify_note(text: str, fallback_title: str) -> tuple[str, str]:
    doc = kaper_doc(text)
    title = str(doc.get("title") or extract_field(text, "title") or fallback_title).strip()
    ings = doc.get("ingredients") if isinstance(doc.get("ingredients"), dict) else None
    course, _tags = classify_course(title, text, ingredients=ings, steps=doc.get("steps"))
    return course, title


def main() -> int:
    apply = "--dry-run" not in sys.argv
    reserved = {str(p) for p in RECIPES.rglob("*.md")}
    index = load_index()
    moved = []
    patched = []
    for name in SCAN_FOLDERS:
        folder = RECIPES / name
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            course, title = classify_note(text, path.stem)
            dest_folder = ROOT / folder_for(course)
            dest_folder.mkdir(parents=True, exist_ok=True)
            era = extract_field(text, "era") or (
                "1980s" if "1980s" in (card_id_from_note(text) or "") else "1990s"
            )
            dest = unique_path(
                dest_folder,
                sanitize_filename(title) if title else path.stem,
                era,
                reserved,
                allow=path,
            )
            reserved.discard(str(path))
            reserved.add(str(dest))
            new_text = replace_fm_scalar(text, "course", fm_scalar(course))
            changed = new_text != text or dest.resolve() != path.resolve()
            if not changed:
                continue
            if apply:
                dest.write_text(new_text, encoding="utf-8")
                if dest.resolve() != path.resolve():
                    path.unlink()
                cid = card_id_from_note(new_text)
                if cid:
                    rec = index.setdefault("cards", {}).setdefault(cid, {})
                    rec["path"] = str(dest.relative_to(ROOT))
                    rec["course"] = course
                    rec["title"] = title
            rel_from = str(path.relative_to(ROOT))
            rel_to = str(dest.relative_to(ROOT))
            row = {"from": rel_from, "to": rel_to, "course": course, "title": title}
            if rel_from != rel_to:
                moved.append(row)
            else:
                patched.append(row)
            print(f"{course:10} {rel_from} → {rel_to}")
    if apply:
        save_index(index)
        for stale in ("Lunch", "Dinner", "Snacks"):
            d = RECIPES / stale
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
    print(f"{'applied' if apply else 'dry-run'}: moved {len(moved)} patched {len(patched)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
