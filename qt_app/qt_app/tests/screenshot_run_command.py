"""Screenshot Run page command preview to verify host value."""
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
        QTimer.singleShot(800, lambda: _save(win, "07_run_command_preview"))
        QTimer.singleShot(1500, qapp.quit)

    QTimer.singleShot(500, _nav_to_run)
    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
