"""Smoke test: Section 5 — QTabWidget advanced groups."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTabWidget

app = QApplication.instance() or QApplication(sys.argv)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.application import create_app  # noqa: E402
from app.pages.run import RunPage  # noqa: E402
from llama_data import ConfigStore, LibraryStore, ProfileStore, default_paths  # noqa: E402

if not app.styleSheet():
    create_app()

with tempfile.TemporaryDirectory() as td:
    paths = default_paths(Path(td))
    paths.ensure()
    cfg = ConfigStore(paths)
    from llama_data import AppConfig
    cfg.save(AppConfig(llama_server_path=""))
    page = RunPage(
        config_store=cfg,
        library_store=LibraryStore(paths),
        profile_store=ProfileStore(paths),
    )
    # -- Test 1: at least one QTabWidget child -----------------------------
    tab_widgets = page.findChildren(QTabWidget)
    assert len(tab_widgets) > 0, (
        f"Expected at least one QTabWidget, found {len(tab_widgets)}"
    )
    print(f"PASS: Found {len(tab_widgets)} QTabWidget(s)")

    tabs = tab_widgets[0]

    # -- Test 2: tab count >= 5 --------------------------------------------
    count = tabs.count()
    assert count >= 5, f"Expected at least 5 tabs, found {count}"
    print(f"PASS: QTabWidget has {count} tabs")

    # -- Test 3: first tab is selected by default --------------------------
    assert tabs.currentIndex() == 0, "First tab should be selected by default"
    print("PASS: First tab is selected by default")

    # -- Test 4: switching tabs changes the visible page -------------------
    initial_index = tabs.currentIndex()
    if count > 1:
        tabs.setCurrentIndex(1)
        assert tabs.currentIndex() == 1, "setCurrentIndex(1) should switch to tab 1"
        tabs.setCurrentIndex(initial_index)
        assert tabs.currentIndex() == initial_index, "Should restore initial tab"
    print("PASS: Tab switching works")

    # -- Test 5: no QToolBox children remain -------------------------------
    from PySide6.QtWidgets import QToolBox
    toolboxes = page.findChildren(QToolBox)
    assert len(toolboxes) == 0, f"Expected 0 QToolBox, found {len(toolboxes)}"
    print("PASS: No QToolBox widgets remain")

print("\n=== All Section 5 smoke tests passed ===")
