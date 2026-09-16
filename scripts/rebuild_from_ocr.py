#!/usr/bin/env python3
"""Regenerate Kaper forms from the note's ## RAW OCR section.

The recipe note is the source of truth. After you fix handwriting in
## RAW OCR, run this script (does not re-OCR unless --reocr). Sidecars
at Scripts/ocr_raw/{card_id}.txt are synced FROM the note.

Examples:
  python Scripts/rebuild_from_ocr.py
  python Scripts/rebuild_from_ocr.py --card 1980s_reciepes_0002
  python Scripts/rebuild_from_ocr.py --all
  python Scripts/rebuild_from_ocr.py --status ocr-draft
  python Scripts/rebuild_from_ocr.py --force          # also rewrite reviewed notes
  python Scripts/rebuild_from_ocr.py --skip-nutrition
  python Scripts/rebuild_from_ocr.py --migrate-ocr    # visible ## RAW OCR only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from cards import discover_cards, load_index, rotated_for, save_index  # noqa: E402
from duplicates import find_duplicates  # noqa: E402
from notes import (  # noqa: E402
    extract_details_ocr,
    extract_field,
    extract_raw_ocr,
    find_notes_by_card_id,
    migrate_all_raw_ocr_sections,
    patch_possible_duplicates,
    raw_ocr_heading_present,
    update_or_create_note,
)
from parse import flatten_ings, flatten_names, parse_recipe  # noqa: E402
from sidecar import (  # noqa: E402
    combine_sidecar_text,
    parse_ocr_blob,
    read_sidecar,
    sidecar_path,
    sync_sidecar_from_note_ocr,
)

EMPTY_NUTR = {
    "calories": "",
    "protein_g": "",
    "fat_g": "",
    "carbs_g": "",
    "nutrition_confidence": "unknown",
}


PLACEHOLDER_OCR = "(no text recovered)"


def _usable(body: str | None) -> bool:
    if body is None:
        return False
    s = body.strip()
    return bool(s) and s != PLACEHOLDER_OCR


def resolve_ocr(card_id: str, note_path: Path | None) -> tuple[str, str, str]:
    """Notes win. Return (combined_ocr, source, raw_body). source: note|sidecar|empty."""
    if note_path and note_path.exists():
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if text:
            if raw_ocr_heading_present(text):
                body = extract_raw_ocr(text) or ""
                front, back = parse_ocr_blob(body)
                return combine_sidecar_text(front, back), "note", body
            details = extract_details_ocr(text)
            if _usable(details):
                front, back = parse_ocr_blob(details)
                return combine_sidecar_text(front, back), "note", details
    got = read_sidecar(card_id)
    if got:
        return got[2], "sidecar", got[2]
    return "", "empty", ""


def _note_status(path: Path | None) -> str:
    if not path or not path.exists():
        return "ocr-draft"
    try:
        return extract_field(path.read_text(encoding="utf-8"), "status") or "ocr-draft"
    except OSError:
        return "ocr-draft"


def nutr_from_note(path: Path | None) -> dict:
    if not path or not path.exists():
        return dict(EMPTY_NUTR)
    t = path.read_text(encoding="utf-8")
    conf = extract_field(t, "nutrition_confidence") or "unknown"
    return {
        "calories": extract_field(t, "calories"),
        "protein_g": extract_field(t, "protein_g"),
        "fat_g": extract_field(t, "fat_g"),
        "carbs_g": extract_field(t, "carbs_g"),
        "nutrition_confidence": conf,
    }


def run_rebuild(
    stems: list[str] | None = None,
    *,
    status: str | None = None,
    force: bool = False,
    skip_nutrition: bool = False,
) -> dict:
    cards = discover_cards()
    by_id = {c["card_id"]: c for c in cards}
    if stems:
        want = list(dict.fromkeys(stems))
        missing = [s for s in want if s not in by_id]
        if missing:
            print(f"stems not found: {missing}", file=sys.stderr)
        cards = [by_id[s] for s in want if s in by_id]

    index = load_index()
    existing = find_notes_by_card_id()
    reserved = {str(p) for p in existing.values()}

    if status:
        cards = [c for c in cards if _note_status(existing.get(c["card_id"])).lower() == status.lower()]

    parsed_map = {}
    ocr_map = {}
    ocr_body_map = {}
    ocr_source_map = {}
    ocr_fail = []
    missing_ocr = []

    for card in cards:
        cid = card["card_id"]
        card["rotated"] = rotated_for(card)
        ocr_text, source, body = resolve_ocr(cid, existing.get(cid))
        ocr_map[cid] = ocr_text
        ocr_body_map[cid] = body
        ocr_source_map[cid] = source
        if source == "empty":
            missing_ocr.append(cid)
        parsed = parse_recipe(ocr_text, cid)
        parsed_map[cid] = parsed
        if not parsed.ocr_ok:
            ocr_fail.append(cid)

    if missing_ocr:
        print(
            f"{len(missing_ocr)} cards missing note RAW OCR and sidecars (run pipeline.py first): "
            f"{missing_ocr[:8]}{'…' if len(missing_ocr) > 8 else ''}",
            file=sys.stderr,
        )

    # Duplicate detection uses every note/sidecar we can read, not only the write set,
    # so --card still flags matches against the rest of the vault.
    dupe_records = []
    all_cards = discover_cards()
    write_ids = {c["card_id"] for c in cards}
    parsed_all = dict(parsed_map)
    for card in all_cards:
        cid = card["card_id"]
        if cid in parsed_all:
            p = parsed_all[cid]
        else:
            text, _src, _body = resolve_ocr(cid, existing.get(cid))
            p = parse_recipe(text, cid)
            parsed_all[cid] = p
        rec = index.get("cards", {}).get(cid, {})
        note_path = existing.get(cid)
        note_title = note_path.stem if note_path else Path(rec.get("path", cid)).stem
        dupe_records.append(
            {
                "card_id": cid,
                "title": p.title,
                "ingredients": flatten_names(p),
                "note_title": note_title,
            }
        )
    dupes = find_duplicates(dupe_records)

    nutr_map: dict[str, dict] = {}
    if skip_nutrition:
        for card in cards:
            nutr_map[card["card_id"]] = nutr_from_note(existing.get(card["card_id"]))
    else:
        from nutrition import estimate_recipe, load_cache as load_nut_cache, load_key, save_cache as save_nut_cache

        key = load_key()
        if not key:
            print("No USDA_API_KEY in Scripts/.env; skipping nutrition.", file=sys.stderr)
            for card in cards:
                nutr_map[card["card_id"]] = nutr_from_note(existing.get(card["card_id"]))
        else:
            import requests

            session = requests.Session()
            nut_cache = load_nut_cache()
            print(f"Nutrition for {len(cards)} recipes...", flush=True)
            for i, card in enumerate(cards, 1):
                cid = card["card_id"]
                parsed = parsed_map[cid]
                try:
                    nutr = estimate_recipe(flatten_ings(parsed), parsed.servings, session, key, nut_cache)
                except Exception as exc:
                    print(f"  nutrition error {cid}: {type(exc).__name__}", file=sys.stderr)
                    nutr = dict(EMPTY_NUTR)
                nutr_map[cid] = nutr
                if i % 20 == 0 or i == len(cards):
                    print(f"  nutrition {i}/{len(cards)}", flush=True)
                    save_nut_cache(nut_cache)
            save_nut_cache(nut_cache)

    created = updated = skipped = sidecars_synced = 0
    print(f"Writing {len(cards)} notes from ## RAW OCR (sidecar fallback)...", flush=True)
    for card in cards:
        cid = card["card_id"]
        card["rotated"] = rotated_for(card)
        _path, action = update_or_create_note(
            card=card,
            parsed=parsed_map[cid],
            ocr_text=ocr_map[cid],
            nutrition=nutr_map.get(cid, dict(EMPTY_NUTR)),
            duplicates=dupes.get(cid, []),
            index=index,
            existing=existing,
            reserved=reserved,
            force=force,
        )
        if action == "created":
            created += 1
        elif action == "updated":
            updated += 1
        elif action == "skipped-reviewed":
            skipped += 1
        if ocr_source_map.get(cid) == "note" and _usable(ocr_body_map.get(cid)):
            sync_sidecar_from_note_ocr(cid, ocr_body_map[cid])
            sidecars_synced += 1

    partner_ids = set()
    for cid in write_ids:
        for link in dupes.get(cid, []):
            title = link.strip("[]")
            for rec in dupe_records:
                if rec["note_title"] == title and rec["card_id"] not in write_ids:
                    partner_ids.add(rec["card_id"])
    for pid in partner_ids:
        path = existing.get(pid)
        if path:
            patch_possible_duplicates(path, dupes.get(pid, []))
    save_index(index)

    notes = list((ROOT / "Recipes").rglob("*.md"))
    folders = Counter()
    nutr_counts = Counter()
    dupe_notes = 0
    for p in notes:
        text = p.read_text(encoding="utf-8")
        rel = p.relative_to(ROOT / "Recipes").parts[0]
        folders[rel] += 1
        mm = re.search(r"^nutrition_confidence:\s*(\S+)", text, re.M)
        if mm:
            nutr_counts[mm.group(1).strip('"')] += 1
        if re.search(r"^possible_duplicates:\s*\n(?:[ \t]+-[^\n]+\n)+", text, re.M):
            dupe_notes += 1

    report = {
        "notes_in_vault": len(notes),
        "cards_processed": len(cards),
        "notes_created": created,
        "notes_updated": updated,
        "notes_skipped_reviewed": skipped,
        "folders": dict(folders),
        "nutrition": dict(nutr_counts),
        "duplicates_flagged": dupe_notes,
        "ocr_failures": ocr_fail,
        "ocr_failure_count": len(ocr_fail),
        "missing_ocr": missing_ocr,
        "ocr_from_note": sum(1 for s in ocr_source_map.values() if s == "note"),
        "ocr_from_sidecar": sum(1 for s in ocr_source_map.values() if s == "sidecar"),
        "sidecars_synced_from_notes": sidecars_synced,
        "sidecar_dir": str(sidecar_path("x").parent.relative_to(ROOT)),
    }
    print(json.dumps(report, indent=2))
    return report


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Rebuild Kaper notes from the note's ## RAW OCR section (sidecar fallback)."
    )
    ap.add_argument("--card", action="append", dest="cards", help="Card stem (repeatable)")
    ap.add_argument("--all", action="store_true", help="All cards (default if no --card)")
    ap.add_argument("--status", default=None, help="Only notes with this status (e.g. ocr-draft)")
    ap.add_argument("--force", action="store_true", help="Also rewrite notes with status reviewed")
    ap.add_argument("--skip-nutrition", action="store_true")
    ap.add_argument("--reocr", action="store_true", help="Re-run Vision OCR (does not overwrite sidecars unless --force-ocr)")
    ap.add_argument("--force-ocr", action="store_true", help="Overwrite OCR sidecars from Vision (destroys human edits)")
    ap.add_argument(
        "--migrate-ocr",
        action="store_true",
        help="Only add a visible ## RAW OCR section to notes; do not rebuild Kaper",
    )
    ap.add_argument(
        "--cleanup-steps",
        action="store_true",
        help="Reshape existing Kaper steps (no markdown, duration vs note, drop cookware ingredients). Does not rewrite RAW OCR.",
    )
    args = ap.parse_args()
    if args.migrate_ocr:
        report = migrate_all_raw_ocr_sections()
        print(json.dumps(report, indent=2))
        return
    if args.cleanup_steps:
        from notes import rewrite_kaper_steps_in_vault

        report = rewrite_kaper_steps_in_vault()
        print(json.dumps(report, indent=2, default=str))
        return
    if args.reocr or args.force_ocr:
        from pipeline import ensure_ocr_sidecars, ensure_rotated

        cards = discover_cards()
        if args.cards:
            want = set(args.cards)
            cards = [c for c in cards if c["card_id"] in want]
        ensure_rotated(cards, overwrite=True)
        ensure_ocr_sidecars(stems=args.cards, reocr=True, force_ocr=args.force_ocr)
    stems = args.cards
    if args.all or not stems:
        stems = None
    run_rebuild(stems, status=args.status, force=args.force, skip_nutrition=args.skip_nutrition)


if __name__ == "__main__":
    main()
