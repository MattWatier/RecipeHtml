#!/usr/bin/env python3
"""Flip FastFoto/rotated copies that still read upside down.

Never overwrites FastFoto originals. Records extras in
Scripts/data/rotation_overrides.json so ensure_rotated keeps the fix.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from cards import load_rotation_overrides, save_rotation_overrides  # noqa: E402
from ocr import ROTATED_ROOT  # noqa: E402

# 1980s cards fed the opposite way (visual audit of top strips + full images).
FLIP_1980S = {
    "1980s_reciepes_0068_a",
    "1980s_reciepes_0068_b",
    "1980s_reciepes_0069_a",
    "1980s_reciepes_0077_a",
    "1980s_reciepes_0077_b",
    "1980s_reciepes_0079_a",
    "1980s_reciepes_0079_b",
    "1980s_reciepes_0081_a",
    "1980s_reciepes_0081_b",
    "1980s_reciepes_0083_a",
    "1980s_reciepes_0084_a",
    "1980s_reciepes_0084_b",
    "1980s_reciepes_0085_a",
    "1980s_reciepes_0086_a",
    "1980s_reciepes_0086_b",
    "1980s_reciepes_0089_a",
    "1980s_reciepes_0091_a",
    "1980s_reciepes_0091_b",
    "1980s_reciepes_0092_a",
}

# 1990s 0068–0146 is a backwards-fed batch, except these already-upright copies.
KEEP_1990S = {
    "1990s_reciepes_0080_a",
    "1990s_reciepes_0080_b",
    "1990s_reciepes_0081_a",
    "1990s_reciepes_0107_a",
    "1990s_reciepes_0135_a",
    "1990s_reciepes_0123_b",  # photo, not handwriting
}

# Extra inverted cards outside that batch.
FLIP_1990S_EXTRA = {
    "1990s_reciepes_0175_b",
    "1990s_reciepes_0176_a",
    "1990s_reciepes_0176_b",
    "1990s_reciepes_0177_a",
    "1990s_reciepes_0177_b",
    "1990s_reciepes_0178_a",
    "1990s_reciepes_0181_a",
    "1990s_reciepes_0181_b",
    "1990s_reciepes_0182_a",
    "1990s_reciepes_0183_a",
}

# Portrait-written card: duplex 90 left it sideways. 270 CW restores original.
SIDEWAYS_270 = {"1990s_reciepes_0110_a"}


def rotate_in_place(path: Path, degrees: int) -> None:
    degrees = ((int(degrees) % 360) + 360) % 360
    if degrees == 0:
        return
    tmp = path.with_name(path.stem + ".rot.tmp.jpg")
    proc = subprocess.run(
        ["sips", "-r", str(degrees), str(path), "--out", str(tmp)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not tmp.exists():
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError(f"sips failed for {path.name}: {proc.stderr[:300]}")
    tmp.replace(path)


def extra_for(stem: str) -> int:
    if stem in SIDEWAYS_270:
        return 270
    if stem in FLIP_1980S or stem in FLIP_1990S_EXTRA:
        return 180
    if stem.startswith("1990s_reciepes_"):
        try:
            num = int(stem.split("_")[-2])
        except (IndexError, ValueError):
            return 0
        if 68 <= num <= 146 and stem not in KEEP_1990S:
            return 180
    return 0


def main() -> int:
    paths = sorted(p for p in ROTATED_ROOT.rglob("*.jpg") if p.is_file())
    overrides = load_rotation_overrides()
    flipped: list[str] = []
    for p in paths:
        want = extra_for(p.stem) % 360
        have = overrides.get(p.stem, 0) % 360
        delta = (want - have) % 360
        if not delta:
            continue
        rotate_in_place(p, delta)
        if want:
            overrides[p.stem] = want
        else:
            overrides.pop(p.stem, None)
        flipped.append(f"{p.name} +{delta}° (extra {have}→{want})")
    save_rotation_overrides(overrides)
    print(f"corrected {len(flipped)} display copies")
    for row in flipped:
        print(f"  {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
