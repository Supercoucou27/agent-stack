"""Screenshot Run page scrolled to show Advanced groups section."""
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

    def _scroll_to_advanced():
        if page and hasattr(page, '_advanced_card'):
            # Scroll the page body to show the advanced card
            body = page._body if hasattr(page, '_body') else page
            from PySide6.QtWidgets import QScrollArea
            parent = body.parentWidget()
            while parent is not None:
                if isinstance(parent, QScrollArea):
                    # Find the advanced card position and scroll to it
                    adv = page._advanced_card
                    parent.ensureWidgetVisible(adv, 50, 50)
                    break
                parent = parent.parentWidget()
        QTimer.singleShot(500, _screenshot_visible)

    def _screenshot_visible():
        _save(win, "06_run_advanced_visible")

    def _toggle_advanced():
        if page and hasattr(page, '_advanced_toggle_btn'):
            btn = page._advanced_toggle_btn
            if btn.isChecked():
                btn.click()
        QTimer.singleShot(500, _screenshot_collapsed)

    def _screenshot_collapsed():
        _save(win, "06_run_advanced_collapsed")
        qapp.quit()

    QTimer.singleShot(500, _nav_to_run)
    QTimer.singleShot(1200, _scroll_to_advanced)
    QTimer.singleShot(2500, _toggle_advanced)

    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
