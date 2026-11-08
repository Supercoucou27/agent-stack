"""Phase 15 smoke: no horizontal scroll at 900x720, Card size policies, grid layout."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QGridLayout  # noqa: E402
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
from app.pages.library import LibraryPage  # noqa: E402
from app.pages.discover import DiscoverPage  # noqa: E402
from app.pages.settings import SettingsPage  # noqa: E402
from app.pages.diagnostics import DiagnosticsPage  # noqa: E402
from app.pages.run import RunPage  # noqa: E402

VIEWPORT_W = 900
VIEWPORT_H = 720


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  pass {message}")


def _no_horizontal_scroll(page, name: str) -> None:
    """Resize page to 900x720 and assert the outer page does not show
    a horizontal scrollbar.

    Inner QScrollArea widgets (e.g. table-style widgets on the
    DiscoverPage) may still scroll horizontally — the user explicitly
    accepted that for table-like content. We only check the top-level
    page's horizontal overflow.
    """
    page.resize(VIEWPORT_W, VIEWPORT_H)
    page.show()
    app.processEvents()
    check(
        page.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        f"{name}: horizontal policy is ScrollBarAsNeeded",
    )
    max_val = page.horizontalScrollBar().maximum()
    check(
        max_val <= 1,
        f"{name}: no horizontal overflow at {VIEWPORT_W}x{VIEWPORT_H} "
        f"(scrollbar max={max_val})",
    )


def test_library_page() -> None:
    print("[library] no horizontal scroll at 900x720")
    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        paths.ensure()
        config_store = ConfigStore(paths)
        config_store.save(AppConfig(port=8080))
        library_store = LibraryStore(paths)
        library_store.upsert(
            LocalModel(
                id="m1",
                path="/tmp/models/llama-Q4_K_M.gguf",
                size_bytes=4_000_000_000,
                quant="Q4_K_M",
            )
        )
        profile_store = ProfileStore(paths)
        page = LibraryPage(
            library_store=library_store,
            profile_store=profile_store,
            config_store=config_store,
        )
        _no_horizontal_scroll(page, "LibraryPage")


def test_discover_page() -> None:
    print("[discover] no horizontal scroll at 900x720")
    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        paths.ensure()
        config_store = ConfigStore(paths)
        page = DiscoverPage(config_store=config_store)
        _no_horizontal_scroll(page, "DiscoverPage")



def test_settings_page() -> None:
    print("[settings] no horizontal scroll at 900x720")
    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        paths.ensure()
        config_store = ConfigStore(paths)
        config_store.save(AppConfig(port=8080))
        page = SettingsPage()
        _no_horizontal_scroll(page, "SettingsPage")


def test_diagnostics_page() -> None:
    print("[diagnostics] no horizontal scroll at 900x720")
    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        paths.ensure()
        config_store = ConfigStore(paths)
        config_store.save(AppConfig(port=8080))
        page = DiagnosticsPage(config_store=config_store)
        _no_horizontal_scroll(page, "DiagnosticsPage")


def test_run_page_grid() -> None:
    print("[run] advanced tabs use 2-column QGridLayout")
    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        paths.ensure()
        config_store = ConfigStore(paths)
        config_store.save(AppConfig(port=8080))
        library_store = LibraryStore(paths)
        library_store.upsert(
            LocalModel(
                id="m1",
                path="/tmp/models/llama-Q4_K_M.gguf",
                size_bytes=4_000_000_000,
                quant="Q4_K_M",
            )
        )
        profile_store = ProfileStore(paths)
        page = RunPage(
            config_store=config_store,
            library_store=library_store,
            profile_store=profile_store,
        )
        page.resize(VIEWPORT_W, VIEWPORT_H)
        page.show()
        app.processEvents()

        tabs = page._advanced_tabs
        check(tabs is not None, "RunPage has advanced tabs")
        for i in range(tabs.count()):
            tab_page = tabs.widget(i)
            inner = tab_page.layout()
            check(
                isinstance(inner, QGridLayout),
                f"RunPage tab '{tabs.tabText(i)}' uses QGridLayout",
            )
            # Only enforce columnCount for tabs that have 2+ option widgets.
            option_count = sum(
                1 for c in tab_page.children() if c.__class__.__name__ == "OptionCard"
            )
            if option_count >= 2:
                assert isinstance(inner, QGridLayout)
                check(
                    inner.columnCount() == 2,
                    f"RunPage tab '{tabs.tabText(i)}' grid has 2 columns",
                )


def main() -> int:
    print("=== Phase 15 smoke ===\n")
    test_library_page()
    test_discover_page()
    test_settings_page()
    test_diagnostics_page()
    test_run_page_grid()
    print("\n=== All Phase 15 checks passed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
