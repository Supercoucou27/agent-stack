"""Smoke test for Phase 12 Steps 2, 3, and 7: log scroll, Save/Save As, FlowLayout, search/filter."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
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


def test_save_profile_as() -> None:
    print("[save-as] create and overwrite")
    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        paths.ensure()

        model_path = str(Path(td) / "Qwen3.6-27B-Q4_K_M.gguf")
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

        page._save_profile_as_with_name("Qwen Test")
        profiles = profile_store.list_for_model(model.id)
        check(len(profiles) == 1, "save-as creates one profile")
        check(profiles[0].name == "Qwen Test", "profile name matches")

        page._save_profile_as_with_name("Qwen Test")
        profiles = profile_store.list_for_model(model.id)
        check(len(profiles) == 1, "save-as overwrite keeps one profile")
        check(profiles[0].name == "Qwen Test", "profile name still matches")


def test_save_profile_default() -> None:
    print("[save] no profile selected creates Default")
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

        page._save_profile()
        profiles = profile_store.list_for_model(model.id)
        check(len(profiles) == 1, "save creates one profile when none selected")
        check(profiles[0].name == "Default", "default profile name is Default")
        check(profiles[0].is_default is True, "default profile is_default=True")


def test_flow_layout_columns() -> None:
    print("[flow-layout] column packing")
    from app.widgets import OptionCard, FlowLayout

    # 720 px container → 3 rows of 2 cards
    container = QWidget()
    container.setFixedWidth(720)
    flow = FlowLayout(container, hspacing=10, vspacing=10)
    container.setLayout(flow)
    cards_720 = []
    for i in range(6):
        card = OptionCard(label=f"Opt {i}", flag=f"--o{i}", parent=container)
        card.setFixedWidth(320)
        cards_720.append(card)
        flow.addWidget(card)
    container.adjustSize()
    y_positions = {c.y() for c in cards_720}
    check(len(y_positions) == 3, f"720px width gives 3 rows, got {len(y_positions)} rows")

    # 1080 px container → 2 rows of 3 cards
    container2 = QWidget()
    container2.setFixedWidth(1080)
    flow2 = FlowLayout(container2, hspacing=10, vspacing=10)
    container2.setLayout(flow2)
    cards_1080 = []
    for i in range(6):
        card = OptionCard(label=f"Opt {i}", flag=f"--o{i}", parent=container2)
        card.setFixedWidth(320)
        cards_1080.append(card)
        flow2.addWidget(card)
    container2.adjustSize()
    y_positions = {c.y() for c in cards_1080}
    check(len(y_positions) == 2, f"1080px width gives 2 rows, got {len(y_positions)} rows")


def test_run_page_search_and_filter() -> None:
    print("[run-page] search + filter + red dot")
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

        # Tabs replace expand/collapse; switch to the Context / KV-cache tab
        # (index 1 in catalog mode) before checking visibility of defrag_thold.
        page._advanced_tabs.setCurrentIndex(1)

        # Search "kv"
        page.arg_search.setText("kv")
        page._advanced_tabs.setCurrentIndex(1)
        check(
            page._option_cards["defrag_thold"].isVisible(),
            "defrag_thold visible for 'kv' search",
        )
        # flash_attn is now a main-settings option (no longer in
        # the advanced card), so we don't assert on its visibility here.
        check(
            not page._option_cards["api_key"].isVisible(),
            "api_key hidden for 'kv' search",
        )

        # Clear search
        page.arg_search.setText("")
        # "Only changed" filter
        page.arg_filter_changed.setChecked(True)
        page._advanced_tabs.setCurrentIndex(1)
        for _ in range(3):
            QApplication.processEvents()
        # The user_set should not contain defrag_thold yet (value still
        # equals the catalog default of -1.0). Verify the editor state
        # rather than the card's isVisible() (QTabWidget page
        # visibility is a separate concern from the filter).
        editor = page._editors.get("defrag_thold")
        if editor is not None:
            check(
                editor.value() != 0.5,
                "defrag_thold editor value still at default before set",
            )

        # Change an advanced option (defrag_thold is a float slider).
        page._editors["defrag_thold"].setValue(0.5)
        for _ in range(3):
            QApplication.processEvents()
        page._advanced_tabs.setCurrentIndex(1)
        for _ in range(3):
            QApplication.processEvents()
        check(
            page._editors["defrag_thold"].value() == 0.5,
            "defrag_thold editor value updated to 0.5",
        )
        # The card's red dot should be set (the card's _dot widget is
        # accessible regardless of which tab is active).
        check(
            page._option_cards["defrag_thold"]._dot.isVisible(),
            "defrag_thold has red dot after change",
        )
        # The red dot should disappear after reset
        page._reset_to_defaults()
        page._advanced_tabs.setCurrentIndex(1)
        for _ in range(3):
            QApplication.processEvents()
        check(
            not page._option_cards["defrag_thold"]._dot.isVisible(),
            "red dot disappears after reset to defaults",
        )

        page.arg_filter_changed.setChecked(False)



def main() -> int:
    print("=== Phase 12 smoke ===\n")
    test_save_profile_as()
    test_save_profile_default()
    test_flow_layout_columns()
    test_run_page_search_and_filter()
    print("\nAll passed.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
