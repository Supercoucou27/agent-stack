"""Smoke test: Section 1 — downloads off the GUI thread.

Verifies that _DownloadWorker accepts config_store and library_store,
the QThread starts without blocking the GUI, and the setup work
(config load, path expansion, directory creation) runs inside run()
rather than on the GUI thread.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
QT_ROOT = REPO_ROOT / "qt_app"
for candidate in (REPO_ROOT, QT_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from llama_data import AppConfig, ConfigStore, LibraryStore, default_paths
from llama_data.models import HfTokenSource

# -- helpers -----------------------------------------------------------------

def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  PASS: {message}")


def _make_stores(td: str) -> tuple[ConfigStore, LibraryStore]:
    paths = default_paths(Path(td))
    cs = ConfigStore(paths)
    cs.save(AppConfig(models_dir=str(Path(td) / "models"), hf_token_source=HfTokenSource()))
    ls = LibraryStore(paths)
    return cs, ls

# -- Test 1: worker constructor accepts stores --------------------------------
print("\n[1] Worker constructor accepts config_store and library_store")

with tempfile.TemporaryDirectory() as td:
    config_store, library_store = _make_stores(td)

    from app.pages.discover import _DownloadWorker
    from app.services.hugging_face import HfFile, HfRepoSummary

    repo = HfRepoSummary(
        repo_id="test/repo",
        author="test",
        files=[HfFile(name="model.Q4_K_M.gguf", size_bytes=100, download_url="http://127.0.0.1:1/bogus.gguf")],
    )

    worker = _DownloadWorker(
        repo=repo,
        file_indices=(0,),
        card_text=None,
        hf_token=None,
        config_store=config_store,
        library_store=library_store,
    )
    _check(worker.config_store is config_store, "worker stores config_store ref")
    _check(worker.library_store is library_store, "worker stores library_store ref")

# -- Test 2: worker defaults to default stores when not provided ---------------
print("\n[2] Worker defaults to default stores when args omitted")

worker2 = _DownloadWorker(
    repo=repo,
    file_indices=(0,),
    card_text=None,
    hf_token=None,
)
_check(worker2.config_store is not None, "default config_store created")
_check(worker2.library_store is not None, "default library_store created")

# -- Test 3: worker.run() does config load + dir creation inside thread --------
print("\n[3] Worker.run() does setup on QThread, not GUI thread")

with tempfile.TemporaryDirectory() as td:
    config_store, library_store = _make_stores(td)

    worker = _DownloadWorker(
        repo=repo,
        file_indices=(0,),
        card_text=None,
        hf_token=None,
        config_store=config_store,
        library_store=library_store,
    )

    thread = QThread()
    worker.moveToThread(thread)

    results: dict = {}

    def _on_finished(result):
        results["finished"] = result

    worker.finished.connect(_on_finished)
    thread.started.connect(lambda: worker.run())

    thread.start()
    _check(thread.isRunning(), "QThread started and is running")

    # The download will fail (bogus URL) but run() should still execute
    # the config load and dir creation before hitting the network error.
    # Wait up to 5s for the finished signal.
    for _ in range(50):
        app.processEvents()
        if "finished" in results:
            break
        time.sleep(0.1)

    thread.quit()
    thread.wait(2000)

    _check("finished" in results, "worker emitted finished signal")
    status, payload = results["finished"]
    _check(status == "error", f"expected error from bogus URL, got status={status}")

    # Verify that run() created the dest dir from the config
    expected_dir = Path(td) / "models" / "test__repo"
    _check(expected_dir.is_dir(), f"dest_dir created at {expected_dir}")

# -- Test 4: GUI thread not blocked during worker startup ----------------------
print("\n[4] GUI thread not blocked between start() and first event")

with tempfile.TemporaryDirectory() as td:
    config_store, library_store = _make_stores(td)

    worker = _DownloadWorker(
        repo=repo,
        file_indices=(0,),
        card_text=None,
        hf_token=None,
        config_store=config_store,
        library_store=library_store,
    )

    thread = QThread()
    worker.moveToThread(thread)

    gui_processed: dict = {}

    def _mark_gui():
        gui_processed["ok"] = True

    # Schedule a timer that fires on the GUI thread after 50 ms.
    # If the GUI is blocked, this won't execute until the worker finishes.
    QTimer.singleShot(50, _mark_gui)

    thread.started.connect(lambda: worker.run())
    thread.start()
    _check(thread.isRunning(), "thread started")

    # Process events for up to 500 ms to let the timer fire.
    for _ in range(10):
        app.processEvents()
        time.sleep(0.05)

    _check(gui_processed.get("ok"), "GUI thread processed timer while worker ran")

    thread.quit()
    thread.wait(2000)

print("\n=== All Section 1 smoke tests passed ===")
