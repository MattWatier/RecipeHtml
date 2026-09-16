#!/usr/bin/env python3
"""Watch Recipes/_Outbox for stable .md files and run process_outbox.py.

Polls (fswatch is not required). Debounces until each .md file's size and
mtime stay unchanged for STABLE_SECS. Empty outbox sleeps quietly.
Failed notes that did not change are not reprocessed until they change.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
OUTBOX = ROOT / "Recipes" / "_Outbox"
PID_FILE = SCRIPTS / "outbox_watch.pid"
LOG_FILE = SCRIPTS / "outbox_watch.log"
PYTHON = ROOT / ".venv" / "bin" / "python"
POLL_SECS = 2.0
STABLE_SECS = 2.0


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def snapshot() -> dict[str, tuple[int, int]]:
    files: dict[str, tuple[int, int]] = {}
    if not OUTBOX.exists():
        return files
    for path in OUTBOX.glob("*.md"):
        if not path.is_file():
            continue
        st = path.stat()
        files[str(path)] = (st.st_mtime_ns, st.st_size)
    return files


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def existing_watcher_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if pid == os.getpid():
        return None
    if pid_alive(pid):
        return pid
    return None


def write_pid() -> None:
    PID_FILE.write_text(str(os.getpid()) + "\n", encoding="utf-8")


def clear_pid() -> None:
    try:
        if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(
            os.getpid()
        ):
            PID_FILE.unlink()
    except OSError:
        pass


def wait_until_stable(current: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    """Return a snapshot that has been unchanged for STABLE_SECS."""
    deadline = time.monotonic() + STABLE_SECS
    while True:
        time.sleep(min(POLL_SECS, 0.5))
        nxt = snapshot()
        if nxt != current:
            current = nxt
            deadline = time.monotonic() + STABLE_SECS
            if not current:
                return current
            continue
        if time.monotonic() >= deadline:
            return current


def run_processor() -> int:
    proc = subprocess.run(
        [str(PYTHON), str(SCRIPTS / "process_outbox.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "").rstrip()
    err = (proc.stderr or "").rstrip()
    if out:
        for line in out.splitlines():
            log("  " + line)
    if err:
        for line in err.splitlines():
            # Never log env/key material; process_outbox does not print the key.
            log("  [stderr] " + line)
    if proc.returncode != 0:
        log(f"process_outbox.py exited {proc.returncode}")
    processed = 0
    text = proc.stdout or ""
    start = text.rfind("{")
    if start >= 0:
        try:
            report = json.loads(text[start:])
            processed = int(report.get("processed", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            processed = sum(
                1 for line in text.splitlines() if line.lstrip().startswith("ok ")
            )
    if processed > 0:
        log(f"OUTBOX_WATCH processed {processed} file(s)")
    return processed


def main() -> int:
    other = existing_watcher_pid()
    if other is not None:
        print(f"Watcher already running as PID {other}", flush=True)
        return 0

    write_pid()
    atexit.register(clear_pid)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    log(
        f"watching {OUTBOX} every {POLL_SECS:.0f}s "
        f"(stable {STABLE_SECS:.0f}s); pid {os.getpid()}"
    )

    last_seen: dict[str, tuple[int, int]] = {}
    while True:
        current = snapshot()
        if not current:
            last_seen = {}
            time.sleep(POLL_SECS)
            continue
        if current == last_seen:
            time.sleep(POLL_SECS)
            continue
        stable = wait_until_stable(current)
        if not stable:
            last_seen = {}
            continue
        if stable == last_seen:
            time.sleep(POLL_SECS)
            continue
        names = ", ".join(Path(p).name for p in sorted(stable))
        log(f"stable .md file(s): {names}")
        run_processor()
        last_seen = snapshot()


if __name__ == "__main__":
    sys.exit(main())
