"""Discover FastFoto card pairs and persist the card index."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
FASTFOTO = ROOT / "FastFoto"
INDEX_PATH = SCRIPTS / "data" / "index.json"
ROTATED_ROOT = FASTFOTO / "rotated"

# FastFoto duplex: portrait photos of landscape index cards.
# Fronts (_a) need 90° CW. Backs (_b) are long-edge flipped, so 270° CW
# (90° CCW / front + 180). sips -r is clockwise.
FRONT_ROTATION_CW = 90
BACK_ROTATION_CW = 270
OVERRIDES_PATH = SCRIPTS / "data" / "rotation_overrides.json"


def duplex_rotation(path: Path) -> int:
    """Clockwise degrees to make a FastFoto scan upright."""
    stem = path.stem if isinstance(path, Path) else Path(str(path)).stem
    if stem.endswith("_b"):
        return BACK_ROTATION_CW
    return FRONT_ROTATION_CW


def load_rotation_overrides() -> dict[str, int]:
    if not OVERRIDES_PATH.exists():
        return {}
    data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in data.items()}


def save_rotation_overrides(overrides: dict[str, int]) -> None:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {k: int(v) % 360 for k, v in overrides.items() if int(v) % 360}
    OVERRIDES_PATH.write_text(json.dumps(cleaned, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def display_rotation(path: Path, overrides: dict[str, int] | None = None) -> int:
    """Duplex rotation plus any 180° correction from an upright check."""
    extra = (overrides if overrides is not None else load_rotation_overrides()).get(Path(path).stem, 0)
    return (duplex_rotation(path) + int(extra)) % 360


def discover_cards() -> list[dict]:
    cards: dict[str, dict] = {}
    if not FASTFOTO.exists():
        return []
    for era_dir in sorted(FASTFOTO.iterdir()):
        if not era_dir.is_dir() or era_dir.name in {"rotated", "ocr_raw"}:
            continue
        for img in era_dir.glob("*.jpg"):
            stem = img.stem
            if stem.endswith("_a"):
                key = stem[:-2]
                rec = cards.setdefault(key, {"card_id": key, "a": None, "b": None})
                rec["a"] = img
            elif stem.endswith("_b"):
                key = stem[:-2]
                rec = cards.setdefault(key, {"card_id": key, "a": None, "b": None})
                rec["b"] = img
    out = []
    for key, rec in sorted(cards.items()):
        if rec["a"] is None:
            print(f"skip orphan _b: {key}", file=sys.stderr)
            continue
        era = "1980s" if key.startswith("1980s") else "1990s"
        rotated = []
        for side in ("a", "b"):
            src = rec.get(side)
            if src is None:
                continue
            dest = ROTATED_ROOT / src.relative_to(FASTFOTO)
            rotated.append(dest if dest.exists() else src)
        out.append(
            {
                **rec,
                "era": era,
                "sides": "ab" if rec["b"] else "a",
                "rotated": rotated,
            }
        )
    return out


def load_index() -> dict:
    if INDEX_PATH.exists():
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return {"cards": {}}


def save_index(index: dict) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(INDEX_PATH)


def originals_for(card: dict) -> list[Path]:
    out = [card["a"]]
    if card.get("b"):
        out.append(card["b"])
    return out


def rotated_for(card: dict) -> list[Path]:
    images = [p for p in (card.get("rotated") or []) if p and Path(p).exists()]
    if images:
        return images
    return originals_for(card)
