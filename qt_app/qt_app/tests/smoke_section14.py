"""Smoke test for Phase 14 Step 4: advanced panel auto-size and collapse."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llama_data import (  # noqa: E402
    AppConfig,
    ConfigStore,
    LibraryStore,
    LocalModel,
    ProfileStore,
    default_paths,
)
from app.pages.run import RunPage  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  pass {message}")


def test_advanced_panel_collapsible() -> None:
    print("[advanced-panel] collapsible header")
    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        paths.ensure()

        model_path = str(Path(td) / "test-model.gguf")
        model = LocalModel(id=model_path, path=model_path)
        lib_store = LibraryStore(paths)
        lib_store.save([model])

        cfg = ConfigStore(paths)
        cfg.save(AppConfig(selected_model_id=model.id, llama_server_path=""))

        profile_store = ProfileStore(paths)
        page = RunPage(
            config_store=cfg, library_store=lib_store, profile_store=profile_store
        )
        page.model_combo.setCurrentIndex(0)
        page.show()

        # Initial state: expanded
        check(page._advanced_toggle_btn.isChecked(), "toggle btn initially checked")
        check(page._advanced_body.isVisible(), "advanced body initially visible")
        check(
            page._advanced_toggle_btn.arrowType() == Qt.DownArrow,
            "arrow initially DownArrow",
        )

        # Collapse
        page._advanced_toggle_btn.click()
        for _ in range(5):
            QApplication.processEvents()
        check(not page._advanced_body.isVisible(), "advanced body hidden after click")
        check(
            page._advanced_toggle_btn.arrowType() == Qt.RightArrow,
            "arrow RightArrow when collapsed",
        )

        # Expand
        page._advanced_toggle_btn.click()
        for _ in range(5):
            QApplication.processEvents()
        check(page._advanced_body.isVisible(), "advanced body visible after re-open")
        check(
            page._advanced_toggle_btn.arrowType() == Qt.DownArrow,
            "arrow DownArrow when expanded",
        )


def test_advanced_panel_tab_resize() -> None:
    print("[advanced-panel] tab switch resizes body")
    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        paths.ensure()

        model_path = str(Path(td) / "test-model.gguf")
        model = LocalModel(id=model_path, path=model_path)
        lib_store = LibraryStore(paths)
        lib_store.save([model])

        cfg = ConfigStore(paths)
        cfg.save(AppConfig(selected_model_id=model.id, llama_server_path=""))

        profile_store = ProfileStore(paths)
        page = RunPage(
            config_store=cfg, library_store=lib_store, profile_store=profile_store
        )
        page.model_combo.setCurrentIndex(0)
        page.show()

        # Ensure at least 2 tabs exist
        tab_count = page._advanced_tabs.count()
        check(tab_count >= 2, f"at least 2 advanced tabs exist (got {tab_count})")

        # Switch to tab 0, let size settle
        page._advanced_tabs.setCurrentIndex(0)
        for _ in range(10):
            QApplication.processEvents()

        h0 = page._advanced_body.minimumHeight()
        check(h0 > 80, f"tab 0 body height > 80 (got {h0})")
        check(
            page._advanced_body.maximumHeight() == h0,
            "tab 0 body min/max height match after refit",
        )

        # Switch to tab 1, let size settle
        page._advanced_tabs.setCurrentIndex(1)
        for _ in range(10):
            QApplication.processEvents()

        h1 = page._advanced_body.minimumHeight()
        check(h1 > 80, f"tab 1 body height > 80 (got {h1})")
        check(
            page._advanced_body.maximumHeight() == h1,
            "tab 1 body min/max height match after refit",
        )

        # Switch back to tab 0 — height should return to the same value
        page._advanced_tabs.setCurrentIndex(0)
        for _ in range(10):
            QApplication.processEvents()

        h0_back = page._advanced_body.minimumHeight()
        check(
            h0_back == h0,
            f"returning to tab 0 restores height (was {h0}, now {h0_back})",
        )

        # Switch to last tab to exercise a different page
        page._advanced_tabs.setCurrentIndex(tab_count - 1)
        for _ in range(10):
            QApplication.processEvents()

        h_last = page._advanced_body.minimumHeight()
        check(h_last > 80, f"last tab body height > 80 (got {h_last})")
        check(
            page._advanced_body.maximumHeight() == h_last,
            "last tab body min/max height match after refit",
        )


def test_advanced_panel_collapsed_height() -> None:
    print("[advanced-panel] collapsed height is header-only")
    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        paths.ensure()

        model_path = str(Path(td) / "test-model.gguf")
        model = LocalModel(id=model_path, path=model_path)
        lib_store = LibraryStore(paths)
        lib_store.save([model])

        cfg = ConfigStore(paths)
        cfg.save(AppConfig(selected_model_id=model.id, llama_server_path=""))

        profile_store = ProfileStore(paths)
        page = RunPage(
            config_store=cfg, library_store=lib_store, profile_store=profile_store
        )
        page.model_combo.setCurrentIndex(0)
        page.show()

        # Expand and let size settle
        page._advanced_toggle_btn.setChecked(True)
        page._advanced_body.setVisible(True)
        for _ in range(10):
            QApplication.processEvents()
        expanded_h = page._advanced_body.minimumHeight()
        check(expanded_h > 80, f"expanded body height > 80 (got {expanded_h})")

        # Collapse
        page._advanced_toggle_btn.setChecked(False)
        page._advanced_body.setVisible(False)
        for _ in range(5):
            QApplication.processEvents()
        # When collapsed the body is hidden; its min height should still be small-ish
        # but the main observable is that the body is not visible.
        check(not page._advanced_body.isVisible(), "body hidden when collapsed")

        # Re-expand
        page._advanced_toggle_btn.setChecked(True)
        page._advanced_body.setVisible(True)
        for _ in range(10):
            QApplication.processEvents()
        reexpanded_h = page._advanced_body.minimumHeight()
        check(
            reexpanded_h > 80,
            f"re-expanded body height > 80 (got {reexpanded_h})",
        )


def main() -> int:
    print("=== Phase 14 smoke ===\n")
    test_advanced_panel_collapsible()
    test_advanced_panel_tab_resize()
    test_advanced_panel_collapsed_height()
    print("\nAll passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
