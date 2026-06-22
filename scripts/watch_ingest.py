"""Phase 3 — Auto-ingest folder watcher.

Watches the Focus ERP Data/ folder. When a new .xlsx file is detected,
automatically runs ingest + load_supabase.

Usage:
  python -m scripts.watch_ingest

Requires: pip install watchdog
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCH_DIR = ROOT / "Focus ERP Data"

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    print("Install watchdog first: pip install watchdog")
    sys.exit(1)


class FocusFileHandler(FileSystemEventHandler):
    def __init__(self) -> None:
        self._processing: set[str] = set()

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in {".xlsx", ".xls"}:
            return
        if str(path) in self._processing:
            return
        self._processing.add(str(path))
        print(f"\n[watch_ingest] New file detected: {path.name}")
        time.sleep(2)  # wait for write to finish
        self._run_pipeline()

    def on_modified(self, event):
        self.on_created(event)

    def _run_pipeline(self):
        print("[watch_ingest] Running ingest pipeline...")
        r1 = subprocess.run([sys.executable, "-m", "scripts.ingest"], cwd=ROOT, capture_output=True, text=True)
        if r1.returncode != 0:
            print(f"[watch_ingest] ingest.py FAILED:\n{r1.stderr}")
            return
        print(r1.stdout or "[watch_ingest] ingest done")

        r2 = subprocess.run([sys.executable, "-m", "scripts.load_supabase"], cwd=ROOT, capture_output=True, text=True)
        if r2.returncode != 0:
            print(f"[watch_ingest] load_supabase.py FAILED:\n{r2.stderr}")
            return
        print(r2.stdout or "[watch_ingest] load done")
        print("[watch_ingest] ✓ Dashboard data updated automatically.")
        self._processing.clear()


def main() -> None:
    if not WATCH_DIR.exists():
        WATCH_DIR.mkdir(parents=True)
        print(f"Created watch folder: {WATCH_DIR}")

    print(f"[watch_ingest] Watching: {WATCH_DIR}")
    print("[watch_ingest] Drop any Focus ERP Excel export here and it will auto-load.")
    print("[watch_ingest] Press Ctrl+C to stop.\n")

    handler = FocusFileHandler()
    observer = Observer()
    observer.schedule(handler, str(WATCH_DIR), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
