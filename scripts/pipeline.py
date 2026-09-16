#!/usr/bin/env python3
"""Build the Obsidian recipe vault from FastFoto scans.

Stages: pair → rotate → OCR sidecars (write-once) → parse/write notes → nutrition.

Human OCR edits live in the note's ## RAW OCR section. After editing that,
run Scripts/rebuild_from_ocr.py (does not re-OCR). Notes win; sidecars are
synced from the note on rebuild.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from cards import discover_cards, display_rotation  # noqa: E402
from ocr import load_cache as load_ocr_cache, ocr_images, save_upright  # noqa: E402
from rebuild_from_ocr import run_rebuild  # noqa: E402
from sidecar import sidecar_path, write_sidecar  # noqa: E402

SAMPLE_STEMS = [
    "1980s_reciepes_0001",
    "1980s_reciepes_0002",
    "1980s_reciepes_0005",
    "1980s_reciepes_0010",
    "1990s_reciepes_0001",
    "1990s_reciepes_0002",
    "1990s_reciepes_0012",
    "1980s_reciepes_0014",
]


def cache_text(ocr_cache: dict, path: Path | None) -> str:
    if path is None:
        return ""
    key = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    rec = ocr_cache.get(key) or {}
    return rec.get("text") or ""


def ensure_rotated(cards: list[dict], ocr_cache: dict | None = None, *, overwrite: bool = False) -> None:
    """Write FastFoto/rotated copies (duplex plus any stored 180° overrides)."""
    _ = ocr_cache  # kept for callers; display rotation is duplex + overrides, not OCR cache
    print("Writing upright copies to FastFoto/rotated/ (duplex + 180° overrides) ...", flush=True)
    for card in cards:
        rotated = []
        rotated.append(save_upright(card["a"], display_rotation(card["a"]), overwrite=overwrite))
        if card.get("b"):
            rotated.append(save_upright(card["b"], display_rotation(card["b"]), overwrite=overwrite))
        card["rotated"] = rotated


def ensure_ocr_sidecars(
    stems: list[str] | None = None,
    *,
    reocr: bool = False,
    force_ocr: bool = False,
    skip_ocr: bool = False,
) -> list[dict]:
    """OCR originals as needed and write Scripts/ocr_raw/{card_id}.txt write-once."""
    cards = discover_cards()
    if stems:
        want = set(stems)
        cards = [c for c in cards if c["card_id"] in want]
        missing = want - {c["card_id"] for c in cards}
        if missing:
            print(f"stems not found: {sorted(missing)}", file=sys.stderr)

    ocr_cache = load_ocr_cache()
    need_ocr: list[Path] = []
    for card in cards:
        path = sidecar_path(card["card_id"])
        if path.exists() and not force_ocr and not reocr:
            continue
        if skip_ocr and not reocr and not force_ocr:
            continue
        if reocr or force_ocr or cache_text(ocr_cache, card["a"]) == "":
            need_ocr.append(card["a"])
            if card.get("b"):
                need_ocr.append(card["b"])
        elif not path.exists():
            # Have cache text; still write sidecar below without engine.
            pass

    if need_ocr and not skip_ocr:
        unique_paths = list(dict.fromkeys(need_ocr))
        print(f"OCR {len(unique_paths)} images for sidecar fill...", flush=True)
        ocr_images(unique_paths, ocr_cache, skip_cache=reocr or force_ocr)
        ocr_cache = load_ocr_cache()

    written = skipped = 0
    for card in cards:
        front = cache_text(ocr_cache, card["a"])
        back = cache_text(ocr_cache, card.get("b"))
        if sidecar_path(card["card_id"]).exists() and not force_ocr:
            skipped += 1
            continue
        if write_sidecar(card["card_id"], front, back, force=force_ocr):
            written += 1
        else:
            skipped += 1
    print(f"OCR sidecars written={written} skipped_existing={skipped}", flush=True)
    return cards


def run(
    stems: list[str] | None,
    skip_ocr: bool,
    nutrition_only: bool,
    skip_nutrition: bool,
    force_ocr: bool = False,
    reocr: bool = False,
) -> dict:
    cards = discover_cards()
    if stems:
        want = set(stems)
        cards = [c for c in cards if c["card_id"] in want]

    ocr_cache = load_ocr_cache()
    if not nutrition_only:
        ensure_rotated(cards, ocr_cache, overwrite=reocr)
        ensure_ocr_sidecars(
            stems=stems,
            reocr=reocr,
            force_ocr=force_ocr,
            skip_ocr=skip_ocr,
        )
    return run_rebuild(stems, skip_nutrition=skip_nutrition or False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--nutrition-only", action="store_true")
    ap.add_argument("--skip-ocr", action="store_true")
    ap.add_argument("--skip-nutrition", action="store_true")
    ap.add_argument("--force-ocr", action="store_true", help="Overwrite OCR sidecars")
    ap.add_argument("--reocr", action="store_true", help="Re-run Vision OCR into cache (sidecars still write-once unless --force-ocr)")
    ap.add_argument("--stems", nargs="*")
    args = ap.parse_args()
    stems = args.stems
    if args.sample:
        stems = SAMPLE_STEMS
    if not args.sample and not args.all and not stems and not args.nutrition_only:
        ap.error("pass --sample, --all, --nutrition-only, or --stems")
    if args.all:
        stems = None
    run(
        stems,
        skip_ocr=args.skip_ocr,
        nutrition_only=args.nutrition_only,
        skip_nutrition=args.skip_nutrition,
        force_ocr=args.force_ocr,
        reocr=args.reocr,
    )


if __name__ == "__main__":
    main()
