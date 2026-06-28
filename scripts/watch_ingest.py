"""Auto-refresh folder watcher (roadmap N0, Phase 1).

Watches a folder (your OneDrive Focus-export folder) and, a few seconds after a batch of fresh
exports lands, runs the verified refresh engine ONCE (scripts.refresh): ingest gate -> load ->
flush cache -> verify -> what-changed -> briefing. Runs SILENTLY in the background on the work PC
during work hours — it never touches your screen, so you can keep working.

  # point it at your OneDrive export folder (or set WATCH_DIR in .env)
  WATCH_DIR="C:\\Users\\me\\OneDrive\\YQ Focus Exports"  python -m scripts.watch_ingest

Requires: pip install watchdog
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

WATCH_DIR = Path(os.getenv("WATCH_DIR") or (ROOT / "Focus ERP Updated Reports"))
DEBOUNCE_S = float(os.getenv("WATCH_DEBOUNCE_S", "10"))  # batch a burst of drops into ONE refresh

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    print("Install watchdog first: pip install watchdog")
    sys.exit(1)


class FocusFileHandler(FileSystemEventHandler):
    """Marks 'dirty' on any spreadsheet change; the main loop debounces + runs one refresh."""

    def __init__(self) -> None:
        self.dirty = False
        self.last_event = 0.0

    def _touch(self, event) -> None:
        if event.is_directory:
            return
        if Path(event.src_path).suffix.lower() not in {".xlsx", ".xls", ".csv"}:
            return
        self.dirty = True
        self.last_event = time.time()
        print(f"[watch] change: {Path(event.src_path).name}")

    def on_created(self, event):
        self._touch(event)

    def on_modified(self, event):
        self._touch(event)

    def on_moved(self, event):
        self._touch(event)


def _run_refresh() -> None:
    print(f"[watch] running verified refresh on: {WATCH_DIR}")
    r = subprocess.run([sys.executable, "-m", "scripts.refresh", str(WATCH_DIR)],
                       cwd=ROOT, capture_output=True, text=True)
    print(((r.stdout or "") + (("\n" + r.stderr) if r.stderr else ""))[-1500:])
    print(f"[watch] refresh {'OK' if r.returncode == 0 else 'FAILED - see briefing/log above'}.\n")


def main() -> None:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[watch] watching: {WATCH_DIR}")
    print(f"[watch] debounce: {DEBOUNCE_S:.0f}s (a batch of dropped reports = one refresh)")
    print("[watch] runs silently in the background; Ctrl+C to stop.\n")
    handler = FocusFileHandler()
    observer = Observer()
    observer.schedule(handler, str(WATCH_DIR), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
            if handler.dirty and (time.time() - handler.last_event) >= DEBOUNCE_S:
                handler.dirty = False
                _run_refresh()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
