"""Smoke test: responsive two-pane shell and sidebar runtime status."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.application import create_app  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.widgets.sidebar import NavItemId  # noqa: E402
from app import theme  # noqa: E402

if not app.styleSheet():
    create_app()

win = MainWindow()
win.show()
app.processEvents()

splitter = win._splitter

assert splitter.count() == 2, f"Splitter should have 2 children, got {splitter.count()}"
assert splitter.widget(0) is win.sidebar
assert splitter.widget(1).objectName() == "CenterColumn"
print("PASS: Splitter children are [sidebar, center]")

assert win.sidebar.minimumWidth() == theme.SIDEBAR_MIN_WIDTH, (
    f"Sidebar minimumWidth should be {theme.SIDEBAR_MIN_WIDTH}, got {win.sidebar.minimumWidth()}"
)
print(f"PASS: Sidebar minimumWidth = {win.sidebar.minimumWidth()}")

for item_id in NavItemId:
    win.navigate(item_id)
    app.processEvents()
    assert splitter.count() == 2, f"{item_id.value}: navigation should keep two-pane shell"
    page = win._pages[item_id]
    assert not page.horizontalScrollBar().isVisible(), f"{item_id.value}: horizontal scrollbar should stay hidden"
print("PASS: All pages keep two-pane shell without horizontal scrollbars")

payload = {
    "title": "Run",
    "chip_text": "running",
    "chip_style": "success",
    "line1": "Long model name that should wrap inside the sidebar instead of widening the shell",
    "line2": "endpoint=http://127.0.0.1:8080",
}
win._on_inspector_changed(payload)
assert win.sidebar._status_chip.text() == "running"
print("PASS: Runtime status routes to sidebar")

print("\n=== All Section 0 smoke tests passed ===")
