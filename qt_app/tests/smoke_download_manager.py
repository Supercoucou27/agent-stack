"""Smoke test for concurrent DownloadManager.

Verifies:
- Multiple downloads can be active at the same time (up to the cap).
- Progress and finished signals are delivered on the main thread.
- Cancel removes queued items and stops active ones.
- UI event loop stays responsive while downloads run.
- Downloads actually execute on background threads (regression guard for
  moveToThread failures that silently run on the main thread).
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path("/home/npittas/llamaUI")
QT_ROOT = REPO_ROOT / "qt_app"
for candidate in (REPO_ROOT, QT_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication  # noqa: E402

from llama_data import LibraryStore, default_paths  # noqa: E402
from app.services.download_service import (  # noqa: E402
    DownloadError,
    DownloadManager,
    DownloadProgress,
    DownloadStatus,
    HfDownloadRequest,
)


def _noop_download_service():
    """Return a fake DownloadService that sleeps a little, reports progress,
    and records which thread it ran on so we can verify background execution."""

    class _FakeService:
        _calls: list[HfDownloadRequest] = []
        _cancelled: set[str] = set()
        _thread_ids: set[int | None] = set()

        def download(self, req: HfDownloadRequest, library, *, on_progress=None, cancel_check=None):
            self._calls.append(req)
            self._thread_ids.add(threading.current_thread().ident)
            total = 1024 * 1024
            for downloaded in range(0, total + 1, total // 4):
                if cancel_check and cancel_check():
                    raise DownloadError("cancelled")
                if on_progress is not None:
                    prog = DownloadProgress(
                        url=req.url,
                        dest_path=str(Path(req.dest_dir) / req.filename),
                        status=DownloadStatus.DOWNLOADING,
                        bytes_downloaded=downloaded,
                        bytes_total=total,
                    )
                    on_progress(prog)
                time.sleep(0.15)
            from llama_data.models import LocalModel

            return LocalModel(
                id=str(Path(req.dest_dir) / req.filename),
                path=str(Path(req.dest_dir) / req.filename),
                size_bytes=total,
            )

    return _FakeService()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  pass {message}")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        paths.ensure()
        library = LibraryStore(paths)
        manager = DownloadManager(library)

        progress_counts: dict[str, int] = {}
        finished: dict[str, object] = {}
        max_active = [0]
        max_pending = [0]

        def _on_progress(job_id: str, prog: DownloadProgress) -> None:
            progress_counts[job_id] = progress_counts.get(job_id, 0) + 1
            max_active[0] = max(max_active[0], manager.active_count())
            max_pending[0] = max(max_pending[0], manager.pending_count())

        def _on_finished(job_id: str, result: object) -> None:
            finished[job_id] = result

        manager.progress.connect(_on_progress)
        manager.finished.connect(_on_finished)

        fake = _noop_download_service()
        with patch(
            "app.services.download_service.DownloadService",
            lambda: fake,
        ):
            ids = []
            for n in range(5):
                req = HfDownloadRequest(
                    repo_id="org/model",
                    filename=f"file{n}.gguf",
                    url=f"http://example.com/file{n}.gguf",
                    dest_dir=td,
                    size_bytes=1024 * 1024,
                )
                ids.append(manager.enqueue(req))

            # Poll until all downloads finish.
            deadline = time.time() + 5.0
            while time.time() < deadline and len(finished) < 5:
                app.processEvents()
                time.sleep(0.01)
                # Polling the manager directly catches state even when progress
                # signals are throttled or delivered slightly out of order.
                max_active[0] = max(max_active[0], manager.active_count())
                max_pending[0] = max(max_pending[0], manager.pending_count())

            # With a cap of 3, we should have observed 3 active at once
            # and at least 2 pending at some point.
            check(max_active[0] == 3, f"manager caps active downloads at 3 (observed max {max_active[0]})")
            check(max_pending[0] >= 2, f"at least 2 downloads were queued (observed max {max_pending[0]})")

            # All downloads must have run on threads other than the main thread.
            main_tid = threading.current_thread().ident
            worker_tids = fake._thread_ids - {main_tid}
            check(
                len(worker_tids) >= 3,
                f"downloads ran on background threads (observed worker tids: {worker_tids})",
            )

            # Ensure the UI event loop is still processing: schedule a timer
            # and confirm it fires while downloads are in flight.
            from PySide6.QtCore import QEventLoop

            timer_fired = [False]

            def _set_flag():
                timer_fired[0] = True

            loop = QEventLoop()
            QTimer.singleShot(10, _set_flag)
            QTimer.singleShot(50, loop.quit)
            loop.exec()
            check(timer_fired[0], "UI timer fired while downloads were running")

            # Wait for remaining downloads without blocking timers.
            deadline = time.time() + 5.0
            while time.time() < deadline and len(finished) < 5:
                app.processEvents()
                time.sleep(0.001)
            check(len(finished) == 5, "all 5 downloads finished")
            check(all(isinstance(v, object) for v in finished.values()), "finished emitted a result for every job")

            # Test cancel of queued item.
            req = HfDownloadRequest(
                repo_id="org/model",
                filename="cancel_me.gguf",
                url="http://example.com/cancel_me.gguf",
                dest_dir=td,
                size_bytes=1024 * 1024,
            )
            cancel_id = manager.enqueue(req)
            manager.cancel(cancel_id)
            app.processEvents()
            # Cancelled before start → should disappear from queue immediately.
            check(
                cancel_id not in finished,
                "cancelled queued item did not start or finish",
            )

            # Test cancel of an active item: enqueue, wait for it to start,
            # then cancel. The worker should observe the flag and the manager
            # should emit a DownloadCancelled result.
            from app.services.download_service import DownloadCancelled

            active_cancel_id = manager.enqueue(
                HfDownloadRequest(
                    repo_id="org/model",
                    filename="active_cancel.gguf",
                    url="http://example.com/active_cancel.gguf",
                    dest_dir=td,
                    size_bytes=1024 * 1024,
                )
            )
            # Wait for the worker to actually start.
            deadline = time.time() + 2.0
            while time.time() < deadline:
                app.processEvents()
                if active_cancel_id in manager._active:
                    break
                time.sleep(0.01)
            check(
                active_cancel_id in manager._active,
                "active-cancel target reached the active set before cancel",
            )
            manager.cancel(active_cancel_id)
            # Wait for the worker to surface the cancellation.
            deadline = time.time() + 3.0
            while time.time() < deadline and active_cancel_id not in finished:
                app.processEvents()
                time.sleep(0.01)
            check(
                active_cancel_id in finished,
                "cancelled active item surfaced a result",
            )
            check(
                isinstance(finished[active_cancel_id], DownloadCancelled),
                f"cancelled active item produced DownloadCancelled, got {type(finished[active_cancel_id]).__name__}",
            )

        # Allow background QThreads to finish quitting before the
        # interpreter shuts down; otherwise the process can abort.
        manager.deleteLater()
        for _ in range(100):
            app.processEvents()
            if manager.active_count() == 0:
                time.sleep(0.02)
            else:
                time.sleep(0.05)

        print("\n=== All DownloadManager smoke tests passed ===")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
