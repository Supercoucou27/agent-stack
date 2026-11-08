"""Screenshot Run page with advanced panel in both states."""
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

    def _nav_to_run():
        win._on_page_navigate(NavItemId.RUN)

    def _screenshot_expanded():
        _save(win, "03_run_advanced_expanded")

    def _collapse_advanced():
        # Find the Run page and click the advanced toggle
        page = win._pages.get(NavItemId.RUN)
        if page and hasattr(page, '_advanced_toggle_btn'):
            btn = page._advanced_toggle_btn
            if btn.isChecked():
                btn.click()
        QTimer.singleShot(500, _screenshot_collapsed)

    def _screenshot_collapsed():
        _save(win, "03_run_advanced_collapsed")
        qapp.quit()

    QTimer.singleShot(500, _nav_to_run)
    QTimer.singleShot(1200, _screenshot_expanded)
    QTimer.singleShot(1800, _collapse_advanced)

    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
