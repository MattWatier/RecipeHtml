"""macOS Vision OCR wrapper with cached results."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
BIN = SCRIPTS / "bin" / "ocr_vision"
SWIFT_SRC = SCRIPTS / "ocr_vision.swift"
CACHE_PATH = SCRIPTS / "data" / "ocr_cache.json"
FASTFOTO = ROOT / "FastFoto"
ROTATED_ROOT = FASTFOTO / "rotated"


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def compile_vision() -> bool:
    if BIN.exists() and BIN.stat().st_mtime >= SWIFT_SRC.stat().st_mtime:
        return True
    BIN.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "xcrun",
        "swiftc",
        "-O",
        "-o",
        str(BIN),
        str(SWIFT_SRC),
        "-framework",
        "Vision",
        "-framework",
        "AppKit",
        "-framework",
        "Foundation",
        "-framework",
        "CoreImage",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return BIN.exists()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"Vision OCR compile failed: {exc}", file=sys.stderr)
        return False


def _ocrmac_one(path: Path) -> dict:
    from ocrmac import ocrmac

    best = {"text": "", "rotation": 0, "confidence": 0.0, "engine": "ocrmac", "lines": []}
    best_score = -1.0
    try:
        from PIL import Image
    except ImportError:
        Image = None
    rotations = [90, 0, 270, 180]
    work = path
    tmp_paths = []
    for rot in rotations:
        candidate = path
        if rot and Image is not None:
            img = Image.open(path)
            # PIL rotate is CCW; 90 CW == rotate 270 CCW.
            rotated = img.rotate(-rot, expand=True)
            tmp = SCRIPTS / "data" / "ocr_raw" / f"{path.stem}_r{rot}.jpg"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            rotated.convert("RGB").save(tmp, quality=95)
            tmp_paths.append(tmp)
            candidate = tmp
        anns = ocrmac.OCR(str(candidate), recognition_level="accurate").recognize()
        lines = []
        texts = []
        confs = []
        for item in anns:
            if len(item) >= 2:
                texts.append(str(item[0]))
                try:
                    confs.append(float(item[1]))
                except (TypeError, ValueError):
                    confs.append(0.5)
                lines.append({"text": str(item[0]), "x": 0, "y": 0, "w": 0, "h": 0, "conf": confs[-1]})
        text = "\n".join(texts)
        conf = sum(confs) / len(confs) if confs else 0.0
        score = conf * 2 + min(len(text) / 180, 1.5)
        if score > best_score:
            best_score = score
            best = {
                "text": text,
                "rotation": rot,
                "confidence": conf,
                "engine": "ocrmac",
                "lines": lines,
                "path": str(path),
            }
        if rot == 90 and score >= 3.0 and len(text) >= 40:
            break
    return best


def ocr_images(paths: list[Path], cache: dict, skip_cache: bool = False) -> list[dict]:
    results = []
    pending: list[Path] = []
    for p in paths:
        key = str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
        if not skip_cache and key in cache and cache[key].get("text") is not None:
            results.append({**cache[key], "path": str(p), "cache_key": key})
        else:
            pending.append(p)
            results.append(None)

    if pending:
        use_vision = compile_vision()
        pending_results: dict[str, dict] = {}
        if use_vision:
            proc = subprocess.Popen(
                [str(BIN), "--stdin-paths"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdin_data = "\n".join(str(p) for p in pending) + "\n"
            out, err = proc.communicate(stdin_data, timeout=60 * 30)
            if proc.returncode != 0:
                print(f"ocr_vision failed: {err[:500]}", file=sys.stderr)
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pending_results[obj.get("path", "")] = obj
        else:
            print("Falling back to ocrmac for OCR.", file=sys.stderr)
            for p in pending:
                pending_results[str(p)] = _ocrmac_one(p)

        filled = []
        pi = 0
        for item in results:
            if item is not None:
                filled.append(item)
                continue
            p = pending[pi]
            pi += 1
            obj = pending_results.get(str(p), {
                "text": "",
                "rotation": 0,
                "confidence": 0.0,
                "engine": "none",
                "lines": [],
                "path": str(p),
            })
            key = str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
            cache[key] = {
                "text": obj.get("text", ""),
                "rotation": obj.get("rotation", 0),
                "confidence": obj.get("confidence", 0.0),
                "engine": obj.get("engine", "vision"),
                "lines": obj.get("lines", []),
            }
            filled.append({**cache[key], "path": str(p), "cache_key": key})
        results = filled
        save_cache(cache)
    return results


def upright_path(original: Path) -> Path:
    """FastFoto/{era}/stem.jpg -> FastFoto/rotated/{era}/stem.jpg"""
    rel = original.relative_to(FASTFOTO)
    if rel.parts and rel.parts[0] == "rotated":
        return original
    return ROTATED_ROOT / rel


def save_upright(original: Path, rotation: int, *, overwrite: bool = False) -> Path:
    """Write a readable copy; never overwrite the FastFoto original."""
    dest = upright_path(original)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not overwrite:
        return dest
    rot = int(rotation or 0) % 360
    if rot == 0:
        shutil.copy2(original, dest)
        return dest
    # sips -r rotates clockwise. Display copies use duplex_rotation: _a 90, _b 270.
    proc = subprocess.run(
        ["sips", "-r", str(rot), str(original), "--out", str(dest)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not dest.exists():
        print(f"sips rotate failed for {original.name}: {proc.stderr[:200]}", file=sys.stderr)
        shutil.copy2(original, dest)
    return dest


def combine_sides(front: dict, back: dict | None) -> tuple[str, list[dict]]:
    parts = [(front.get("text") or "").strip()]
    lines = list(front.get("lines") or [])
    if back and (back.get("text") or "").strip():
        parts.append("--- BACK ---")
        parts.append(back["text"].strip())
        lines = lines + [{"text": "--- BACK ---", "x": 0, "y": 1, "w": 1, "h": 0, "conf": 1.0}]
        lines.extend(back.get("lines") or [])
    return "\n\n".join(p for p in parts if p), lines
