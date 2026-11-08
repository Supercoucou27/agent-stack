"""Download queue for HuggingFace model files.

Downloads a file from a URL to a local path using urllib (background-thread
friendly, no external deps). Tracks progress state and persists LocalModel
metadata into LibraryStore on completion.

This module is deliberately synchronous/thread-safe so callers can run it on
a background thread or wrap it with asyncio/QThread as needed.
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from llama_data.models import LocalModel, utc_now
from llama_data.stores import LibraryStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Progress / state types
# ---------------------------------------------------------------------------


class DownloadStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DownloadProgress:
    """Snapshot of an in-progress download."""

    url: str
    dest_path: str
    status: DownloadStatus = DownloadStatus.PENDING
    bytes_downloaded: int = 0
    bytes_total: Optional[int] = None
    error: Optional[str] = None

    @property
    def fraction(self) -> Optional[float]:
        if self.bytes_total and self.bytes_total > 0:
            return self.bytes_downloaded / self.bytes_total
        return None


# Callback signature: called with updated progress after each chunk.
ProgressCallback = Callable[[DownloadProgress], None]

# Internal chunk size for streaming download.
_CHUNK_SIZE = 64 * 1024  # 64 KiB


# ---------------------------------------------------------------------------
# DownloadError
# ---------------------------------------------------------------------------


class DownloadError(Exception):
    """Raised when a download fails for a known reason."""


class DownloadCancelled(DownloadError):
    """Raised when a download is cancelled by the user.

    Subclass of :class:`DownloadError` so existing ``except DownloadError``
    branches still catch it, but callers can detect user-initiated cancels
    with ``isinstance(exc, DownloadCancelled)`` and show a distinct
    status (e.g. "cancelled" instead of "failed").
    """


# ---------------------------------------------------------------------------
# Core download function
# ---------------------------------------------------------------------------


def download_file(
    url: str,
    dest_path: str | Path,
    *,
    token: Optional[str] = None,
    expected_sha: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> DownloadProgress:
    """Download *url* to *dest_path* atomically.

    The file is written to a temp file next to the destination first, then
    renamed into place so partial downloads never corrupt the target.

    Parameters
    ----------
    url:
        Remote URL to download.
    dest_path:
        Local filesystem path for the final file.
    token:
        Optional HuggingFace bearer token for gated/private models.
    expected_sha:
        If provided, the downloaded content is verified against this hex digest
        (SHA-256). Mismatch raises :class:`DownloadError`.
    on_progress:
        Called after every chunk with an updated :class:`DownloadProgress`.
    cancel_check:
        If provided, polled before every chunk. Return ``True`` to abort.

    Returns
    -------
    DownloadProgress
        Final progress snapshot (status will be COMPLETED or CANCELLED).
    """
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    progress = DownloadProgress(
        url=url,
        dest_path=str(dest),
        status=DownloadStatus.DOWNLOADING,
    )

    tmp_path = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    resume_from = tmp_path.stat().st_size if tmp_path.exists() else 0
    if resume_from > 0 and not expected_sha:
        req.add_header("Range", f"bytes={resume_from}-")
        progress.bytes_downloaded = resume_from

    try:
        with urllib.request.urlopen(req) as resp:
            status = getattr(resp, "status", None)
            append = resume_from > 0 and status == 206 and not expected_sha
            if resume_from > 0 and not append:
                resume_from = 0
                progress.bytes_downloaded = 0
            total_raw = resp.headers.get("Content-Length")
            if total_raw is not None:
                try:
                    progress.bytes_total = int(total_raw) + (resume_from if append else 0)
                except (ValueError, TypeError):
                    pass

            sha = hashlib.sha256() if expected_sha else None

            with open(tmp_path, "ab" if append else "wb") as f:
                while True:
                    if cancel_check and cancel_check():
                        progress.status = DownloadStatus.CANCELLED
                        _notify(on_progress, progress)
                        return progress

                    chunk = resp.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    if sha:
                        sha.update(chunk)
                    progress.bytes_downloaded += len(chunk)
                    _notify(on_progress, progress)

            if cancel_check and cancel_check():
                progress.status = DownloadStatus.CANCELLED
                _notify(on_progress, progress)
                return progress

            # SHA verification
            if expected_sha and sha:
                actual = sha.hexdigest()
                if actual != expected_sha:
                    raise DownloadError(
                        f"SHA-256 mismatch: expected {expected_sha}, got {actual}"
                    )

            # Atomic move into final location.
            shutil.move(str(tmp_path), str(dest))

        progress.status = DownloadStatus.COMPLETED
        _notify(on_progress, progress)
        return progress

    except (DownloadError, urllib.error.URLError, OSError) as exc:
        progress.status = DownloadStatus.FAILED
        progress.error = str(exc)
        _notify(on_progress, progress)
        raise DownloadError(str(exc)) from exc
    finally:
        # Preserve partial downloads for resume. Only remove .part when the
        # transfer completed and the temp file should no longer exist.
        if progress.status == DownloadStatus.COMPLETED and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
                pass


def _notify(cb: Optional[ProgressCallback], progress: DownloadProgress) -> None:
    if cb is not None:
        try:
            cb(progress)
        except Exception:
            logger.debug("progress callback raised", exc_info=True)


# ---------------------------------------------------------------------------
# High-level: download + persist metadata
# ---------------------------------------------------------------------------


@dataclass
class HfDownloadRequest:
    """Everything needed to download a single HF file and register it."""

    repo_id: str
    filename: str
    url: str
    dest_dir: str  # directory (not file path) — filename is appended
    size_bytes: Optional[int] = None
    sha: Optional[str] = None
    quant: Optional[str] = None
    architecture: Optional[str] = None
    license: Optional[str] = None
    base_model: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    gated: bool = False
    private: bool = False
    card_text: Optional[str] = None
    cards_dir: Optional[str] = None
    hf_token: Optional[str] = None
    companion_paths: list[str] = field(default_factory=list)


@dataclass
class DownloadService:
    """Stateless service: download a HF file and write metadata to LibraryStore.

    Designed for background-thread usage. Call :meth:`download` with a request
    and a library store; on success the file is on disk and a :class:`LocalModel`
    entry exists.
    """

    def download(
        self,
        req: HfDownloadRequest,
        library: LibraryStore,
        *,
        on_progress: Optional[ProgressCallback] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> LocalModel:
        """Download file and persist metadata.

        Returns the :class:`LocalModel` written to *library*.

        Raises :class:`DownloadError` on failure.
        """
        dest_dir = Path(req.dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / req.filename

        # If the file already exists and matches expected size, skip download.
        if dest_path.exists() and dest_path.is_file():
            actual_size = dest_path.stat().st_size
            if req.size_bytes is None or actual_size == req.size_bytes:
                logger.info("File already exists, skipping download: %s", dest_path)
                model = _build_local_model(req, dest_path)
                library.upsert(model)
                return model

        progress = download_file(
            req.url,
            dest_path,
            token=req.hf_token,
            expected_sha=req.sha,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )

        if progress.status == DownloadStatus.CANCELLED:
            raise DownloadError("download cancelled")

        model = _build_local_model(req, dest_path)
        library.upsert(model)
        return model


def _write_card_cache(req: HfDownloadRequest) -> Optional[str]:
    if not req.card_text or not req.cards_dir:
        return None
    safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "__", req.repo_id)
    cards_dir = Path(req.cards_dir)
    cards_dir.mkdir(parents=True, exist_ok=True)
    path = cards_dir / f"{safe_repo}.md"
    path.write_text(req.card_text, encoding="utf-8")
    return str(path)


def _build_local_model(req: HfDownloadRequest, dest_path: Path) -> LocalModel:
    size = dest_path.stat().st_size if dest_path.exists() else req.size_bytes
    return LocalModel(
        id=str(dest_path.resolve()),
        path=str(dest_path),
        size_bytes=size,
        hf_repo=req.repo_id,
        hf_file=req.filename,
        sha=req.sha,
        quant=req.quant,
        architecture=req.architecture,
        card_cache_path=_write_card_cache(req),
        license=req.license,
        base_model=req.base_model,
        tags=list(req.tags),
        gated=req.gated,
        private=req.private,
        companion_paths=list(req.companion_paths),
    )

# ---------------------------------------------------------------------------


_MAX_CONCURRENT_DOWNLOADS = 3


class _DownloadThread(QThread):
    """One file download running in its own QThread."""

    progress = Signal(str, object)  # (id, DownloadProgress)
    completed = Signal(str, object)  # (id, LocalModel | DownloadError)

    def __init__(
        self,
        job_id: str,
        request: HfDownloadRequest,
        library: LibraryStore,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.job_id = job_id
        self.request = request
        self.library = library
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        # Throttle progress signals so a fast download does not flood the
        # main thread with tens of thousands of queued signal emissions.
        _MIN_PROGRESS_INTERVAL = 0.1
        last_emit = 0.0

        def _throttled(prog: DownloadProgress) -> None:
            nonlocal last_emit
            now = time.monotonic()
            if prog.status != DownloadStatus.DOWNLOADING:
                last_emit = now
                self.progress.emit(self.job_id, prog)
            elif now - last_emit >= _MIN_PROGRESS_INTERVAL:
                last_emit = now
                self.progress.emit(self.job_id, prog)

        try:
            model = DownloadService().download(
                self.request,
                self.library,
                on_progress=_throttled,
                cancel_check=lambda: self._cancelled,
            )
            self.completed.emit(self.job_id, model)
        except DownloadError as exc:
            if self._cancelled:
                self.completed.emit(self.job_id, DownloadCancelled(str(exc) or "cancelled"))
            else:
                self.completed.emit(self.job_id, exc)
        except Exception as exc:
            if self._cancelled:
                self.completed.emit(self.job_id, DownloadCancelled("cancelled"))
            else:
                self.completed.emit(self.job_id, DownloadError(str(exc)))


class DownloadManager(QObject):
    """Concurrent download queue with a capped number of active workers.

    Lives in the main thread. Callers ``enqueue`` requests; the manager
    runs up to ``_MAX_CONCURRENT_DOWNLOADS`` workers at once and drains
    the pending queue as workers finish.
    """

    progress = Signal(str, object)       # (id, DownloadProgress)
    status_changed = Signal(str, str, object)  # (id, status_name, error_or_none)
    finished = Signal(str, object)       # (id, LocalModel | DownloadError)
    queue_changed = Signal(int, int)     # (active, pending)

    def __init__(self, library: LibraryStore, parent: QObject | None = None):
        super().__init__(parent)
        self._library = library
        self._pending: deque[tuple[str, HfDownloadRequest]] = deque()
        self._active: dict[str, _DownloadThread] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"dl-{self._counter:06d}"

    def enqueue(self, request: HfDownloadRequest) -> str:
        """Queue a download. Returns a stable id the caller can use to cancel it."""
        job_id = self._next_id()
        self._pending.append((job_id, request))
        self._emit_queue()
        self._drain()
        return job_id

    def cancel(self, job_id: str) -> None:
        """Cancel a queued or active download."""
        for pending_id, _ in list(self._pending):
            if pending_id == job_id:
                self._pending = deque((i, r) for i, r in self._pending if i != job_id)
                self.status_changed.emit(job_id, DownloadStatus.CANCELLED.value, None)
                self._emit_queue()
                return
        active = self._active.get(job_id)
        if active is not None:
            active.cancel()

    def active_count(self) -> int:
        return len(self._active)

    def pending_count(self) -> int:
        return len(self._pending)

    def _emit_queue(self) -> None:
        self.queue_changed.emit(len(self._active), len(self._pending))

    def _drain(self) -> None:
        while self._pending and len(self._active) < _MAX_CONCURRENT_DOWNLOADS:
            job_id, request = self._pending.popleft()
            self._start(job_id, request)
        self._emit_queue()

    def _start(self, job_id: str, request: HfDownloadRequest) -> None:
        thread = _DownloadThread(job_id, request, self._library, self)
        thread.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        thread.completed.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(thread.deleteLater, Qt.ConnectionType.QueuedConnection)
        self._active[job_id] = thread
        thread.start()
        self.status_changed.emit(job_id, DownloadStatus.DOWNLOADING.value, None)
        self._emit_queue()

    @Slot(str, object)
    def _on_progress(self, job_id: str, progress: DownloadProgress) -> None:
        self.progress.emit(job_id, progress)

    @Slot(str, object)
    def _on_finished(self, job_id: str, result: object) -> None:
        thread = self._active.pop(job_id, None)
        if thread is not None:
            thread.wait(5000)
        if isinstance(result, DownloadCancelled):
            self.status_changed.emit(job_id, DownloadStatus.CANCELLED.value, None)
        elif isinstance(result, DownloadError):
            self.status_changed.emit(job_id, DownloadStatus.FAILED.value, str(result))
        else:
            self.status_changed.emit(job_id, DownloadStatus.COMPLETED.value, None)
        self.finished.emit(job_id, result)
        self._drain()


__all__ = [
    "DownloadCancelled",
    "DownloadError",
    "DownloadManager",
    "DownloadProgress",
    "DownloadService",
    "DownloadStatus",
    "HfDownloadRequest",
    "ProgressCallback",
    "download_file",
]