"""Screenshot advanced panel with different active tabs to verify sizing."""
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
    pixmap = widget.grab()
    path = OUTPUT_DIR / f"{name}.png"
    pixmap.save(str(path))
    print(f"  saved {path}")


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    qapp = create_app(sys.argv)

    win = MainWindow()
    win.show()
    win.resize(1400, 1000)

    page = None

    def _nav_to_run():
        nonlocal page
        win._on_page_navigate(NavItemId.RUN)
        page = win._pages.get(NavItemId.RUN)

    def _switch_tab(idx: int, name: str):
        def _do():
            if page and hasattr(page, '_advanced_tabs'):
                page._advanced_tabs.setCurrentIndex(idx)
            QTimer.singleShot(500, lambda: _save(win, name))
        return _do

    QTimer.singleShot(500, _nav_to_run)
    # Switch through several tabs to see if they each size independently
    QTimer.singleShot(1200, _switch_tab(0, "04_advanced_tab0"))
    QTimer.singleShot(2500, _switch_tab(1, "04_advanced_tab1"))
    QTimer.singleShot(3800, _switch_tab(2, "04_advanced_tab2"))
    QTimer.singleShot(5100, lambda: qapp.quit())

    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
