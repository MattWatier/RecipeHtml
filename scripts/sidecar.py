"""OCR sidecars aligned from recipe notes.

The note's ## RAW OCR section is the source of truth for human edits.
Rebuild reads the note first, then falls back to Scripts/ocr_raw/{card_id}.txt,
and syncs the sidecar from the note so they stay aligned.

Write-once on first OCR unless force=True (--force-ocr). Rebuild may overwrite
sidecars when copying FROM the note (notes win).
"""

from __future__ import annotations

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
SIDECAR_DIR = SCRIPTS / "ocr_raw"

FRONT_MARK = "=== FRONT ==="
BACK_MARK = "=== BACK ==="
NOTE_BACK_MARK = "--- BACK ---"


def sidecar_path(card_id: str) -> Path:
    return SIDECAR_DIR / f"{card_id}.txt"


def sidecar_wiki(card_id: str) -> str:
    rel = sidecar_path(card_id).relative_to(ROOT).as_posix()
    return f"[[{rel}]]"


def format_sidecar(front: str, back: str | None = None) -> str:
    parts = [FRONT_MARK, (front or "").rstrip(), ""]
    if back and back.strip():
        parts.extend([BACK_MARK, back.rstrip(), ""])
    return "\n".join(parts).rstrip() + "\n"


def parse_ocr_blob(text: str) -> tuple[str, str]:
    """Return (front, back) from sidecar markers, note --- BACK ---, or plain text."""
    raw = (text or "").replace("\r\n", "\n")
    if FRONT_MARK in raw or BACK_MARK in raw:
        return parse_sidecar(raw)
    if NOTE_BACK_MARK in raw:
        front, _, back = raw.partition(NOTE_BACK_MARK)
        return front.strip(), back.strip()
    return raw.strip(), ""


def parse_sidecar(text: str) -> tuple[str, str]:
    """Return (front, back). Marker-less files are treated as front-only."""
    raw = (text or "").replace("\r\n", "\n")
    if FRONT_MARK not in raw and BACK_MARK not in raw:
        return raw.strip(), ""
    front = ""
    back = ""
    section = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal front, back, buf
        body = "\n".join(buf).strip()
        if section == "front":
            front = body
        elif section == "back":
            back = body
        buf = []

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped == FRONT_MARK:
            flush()
            section = "front"
            continue
        if stripped == BACK_MARK:
            flush()
            section = "back"
            continue
        buf.append(line)
    flush()
    return front, back


def combine_sidecar_text(front: str, back: str) -> str:
    parts = [(front or "").strip()]
    if (back or "").strip():
        parts.append("--- BACK ---")
        parts.append(back.strip())
    return "\n\n".join(p for p in parts if p)


def read_sidecar(card_id: str) -> tuple[str, str, str] | None:
    path = sidecar_path(card_id)
    if not path.exists():
        return None
    front, back = parse_sidecar(path.read_text(encoding="utf-8"))
    return front, back, combine_sidecar_text(front, back)


def write_sidecar(card_id: str, front: str, back: str | None = None, *, force: bool = False) -> bool:
    """Write sidecar. Returns True if written, False if skipped (already exists)."""
    path = sidecar_path(card_id)
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return False
    path.write_text(format_sidecar(front, back), encoding="utf-8")
    return True


def sync_sidecar_from_note_ocr(card_id: str, ocr_body: str) -> bool:
    """Overwrite sidecar from note RAW OCR. Notes win."""
    front, back = parse_ocr_blob(ocr_body)
    return write_sidecar(card_id, front, back, force=True)
