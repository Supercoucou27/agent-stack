"""Screenshot every page of the Qt app for visual review."""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
QT_ROOT = REPO_ROOT / "qt_app"
for candidate in (REPO_ROOT, QT_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from PySide6.QtCore import QTimer

from app.application import create_app
from app.main_window import MainWindow
from app.widgets.sidebar import NavItemId


OUTPUT_DIR = Path(__file__).parent / "screenshots_review"
OUTPUT_DIR.mkdir(exist_ok=True)


def _save(widget, name: str) -> None:
    """Grab a screenshot of a widget and save it."""
    pixmap = widget.grab()
    path = OUTPUT_DIR / f"{name}.png"
    pixmap.save(str(path))
    print(f"  saved {path}")


def _nav_and_shoot(win, nav_id: NavItemId, name: str, delay_ms: int = 500) -> int:
    """Navigate to a page, wait for layout, screenshot, return next delay."""
    def _do():
        win._on_page_navigate(nav_id)
        QTimer.singleShot(300, lambda: _save(win, name))
    QTimer.singleShot(delay_ms, _do)
    return delay_ms + 800


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    qapp = create_app(sys.argv)

    win = MainWindow()
    win.show()
    win.resize(1400, 1000)

    # Wait for window to show, then take screenshots
    total_delay = 500

    # Library
    total_delay = _nav_and_shoot(win, NavItemId.LIBRARY, "01_library", total_delay)

    # Discover
    total_delay = _nav_and_shoot(win, NavItemId.DISCOVER, "02_discover", total_delay)

    # Run (single model) - this is the important one
    total_delay = _nav_and_shoot(win, NavItemId.RUN, "03_run", total_delay)

    # Settings
    total_delay = _nav_and_shoot(win, NavItemId.SETTINGS, "04_settings", total_delay)

    # Diagnostics
    total_delay = _nav_and_shoot(win, NavItemId.DIAGNOSTICS, "05_diagnostics", total_delay)

    # Quit after last screenshot
    QTimer.singleShot(total_delay + 200, qapp.quit)

    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
