"""Native file dialog helpers.

This module is a thin shim around ``QFileDialog`` and ``QStandardPaths`` so
the shell has a single place to ask for "pick a file" / "pick a directory"
and so we can later swap in a KDE portal-backed implementation without
touching callers.

The module deliberately does **not** import ``QFileDialog`` at import time
(Qt requires a ``QApplication`` for some static helpers) and never creates
one itself. The exported functions take an optional ``parent`` widget and
delegate to Qt; the rest of the module is pure data so it stays importable
from tests and from non-Qt code paths.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class FilePickRequest:
    """Declarative description of a file picker invocation.

    UI shells translate this into the actual ``QFileDialog`` call so the
    rest of the app doesn't need to know Qt details.
    """

    title: str
    start_dir: Optional[Path] = None
    name_filters: tuple[str, ...] = ()
    default_suffix: Optional[str] = None
    multiple: bool = False
    directory: bool = False


@dataclass
class FilePickResult:
    accepted: bool
    paths: tuple[Path, ...] = ()
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Pure helpers (no Qt)
# ---------------------------------------------------------------------------


def default_models_dir() -> Path:
    """Return the platform-appropriate user models directory.

    Falls back to ``~/Models`` when Qt's ``QStandardPaths`` is not
    available, so callers can use this safely outside a Qt event loop.
    """
    try:
        from PySide6.QtCore import QStandardPaths  # type: ignore
    except Exception:
        return Path.home() / "Models"
    raw = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.HomeLocation
    )
    if not raw:
        return Path.home() / "Models"
    return Path(raw) / "Models"


def normalize_start_dir(start) -> Path:
    """Coerce a user-provided starting path into an existing directory.

    ``QFileDialog`` is happiest when the initial path actually exists; an
    empty/missing path falls back to the user's home.
    """
    if start is None:
        return Path.home()
    p = Path(os.path.expanduser(os.fspath(start)))
    if p.is_dir():
        return p
    if p.exists():
        return p.parent
    return Path.home()


# ---------------------------------------------------------------------------
# Qt-aware helpers (lazy import; safe to call from a Qt event loop only)
# ---------------------------------------------------------------------------


def _file_dialog_module():
    try:
        from PySide6.QtWidgets import QFileDialog  # type: ignore

        return QFileDialog
    except Exception as exc:  # pragma: no cover - depends on env
        return exc


def pick_file(
    parent=None,
    *,
    title: str = "Select file",
    start_dir=None,
    name_filter: str = "All files (*)",
    default_suffix: Optional[str] = None,
) -> FilePickResult:
    """Show a native single-file picker.

    The Qt call requires a running ``QApplication``; if Qt isn't available
    the function returns an error result rather than raising.
    """
    QFileDialog = _file_dialog_module()
    if not isinstance(QFileDialog, type):
        return FilePickResult(accepted=False, error=f"Qt unavailable: {QFileDialog}")

    initial = str(normalize_start_dir(start_dir))
    try:
        path, _selected = QFileDialog.getOpenFileName(
            parent=parent, caption=title, dir=initial, filter=name_filter
        )
    except Exception as exc:  # pragma: no cover - dialog UI
        return FilePickResult(accepted=False, error=str(exc))
    if not path:
        return FilePickResult(accepted=False)
    return FilePickResult(accepted=True, paths=(Path(path),))


def pick_files(
    parent=None,
    *,
    title: str = "Select files",
    start_dir=None,
    name_filter: str = "All files (*)",
) -> FilePickResult:
    """Show a native multi-file picker."""
    QFileDialog = _file_dialog_module()
    if not isinstance(QFileDialog, type):
        return FilePickResult(accepted=False, error=f"Qt unavailable: {QFileDialog}")

    initial = str(normalize_start_dir(start_dir))
    try:
        paths, _selected = QFileDialog.getOpenFileNames(
            parent=parent, caption=title, dir=initial, filter=name_filter
        )
    except Exception as exc:  # pragma: no cover - dialog UI
        return FilePickResult(accepted=False, error=str(exc))
    if not paths:
        return FilePickResult(accepted=False)
    return FilePickResult(
        accepted=True, paths=tuple(Path(p) for p in paths)
    )


def pick_directory(
    parent=None,
    *,
    title: str = "Select directory",
    start_dir=None,
) -> FilePickResult:
    """Show a native directory picker."""
    QFileDialog = _file_dialog_module()
    if not isinstance(QFileDialog, type):
        return FilePickResult(accepted=False, error=f"Qt unavailable: {QFileDialog}")

    initial = str(normalize_start_dir(start_dir))
    try:
        path = QFileDialog.getExistingDirectory(
            parent=parent, caption=title, dir=initial
        )
    except Exception as exc:  # pragma: no cover - dialog UI
        return FilePickResult(accepted=False, error=str(exc))
    if not path:
        return FilePickResult(accepted=False)
    return FilePickResult(accepted=True, paths=(Path(path),))


def pick_save_file(
    parent=None,
    *,
    title: str = "Save file",
    start_dir=None,
    name_filter: str = "All files (*)",
    default_suffix: Optional[str] = None,
) -> FilePickResult:
    """Show a native save-file dialog."""
    QFileDialog = _file_dialog_module()
    if not isinstance(QFileDialog, type):
        return FilePickResult(accepted=False, error=f"Qt unavailable: {QFileDialog}")

    initial = str(normalize_start_dir(start_dir))
    try:
        path, _selected = QFileDialog.getSaveFileName(
            parent=parent,
            caption=title,
            dir=initial,
            filter=name_filter,
        )
    except Exception as exc:  # pragma: no cover - dialog UI
        return FilePickResult(accepted=False, error=str(exc))
    if not path:
        return FilePickResult(accepted=False)
    if default_suffix and "." not in os.path.basename(path):
        path = path + "." + default_suffix.lstrip(".")
    return FilePickResult(accepted=True, paths=(Path(path),))
