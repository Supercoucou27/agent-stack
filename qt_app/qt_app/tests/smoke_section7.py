"""Smoke test for Section 7b/7c/7d: ModelPicker combo dropdowns.

Verifies:
- LibraryPage has a model_picker QComboBox with objectName "ModelPicker".
- RunPage's model_combo has objectName "ModelPicker".
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
QT_ROOT = REPO_ROOT / "qt_app"
for candidate in (REPO_ROOT, QT_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from llama_data import AppConfig, ConfigStore, LibraryStore, LocalModel, ProfileStore, default_paths  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"pass {message}")


def main() -> int:
    from PySide6.QtWidgets import QApplication, QComboBox
    _app = QApplication.instance() or QApplication(sys.argv)

    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        config_store = ConfigStore(paths)
        config_store.save(AppConfig(port=8080))

        # Library: one primary model + one companion (companion filtered by 7a).
        library_store = LibraryStore(paths)
        library_store.upsert(LocalModel(
            id="primary-1",
            path="/tmp/models/llama-Q4_K_M.gguf",
            size_bytes=4_000_000_000,
            quant="Q4_K_M",
            hf_repo="org/llama-gguf",
        ))
        library_store.upsert(LocalModel(
            id="companion-1",
            path="/tmp/models/mmproj-llama.gguf",
            size_bytes=100_000_000,
        ))

        profile_store = ProfileStore(paths)

        # -- LibraryPage --
        from app.pages.library import LibraryPage
        lib_page = LibraryPage(
            library_store=library_store,
            profile_store=profile_store,
            config_store=config_store,
        )
        check(hasattr(lib_page, "model_picker"), "LibraryPage has model_picker")
        check(isinstance(lib_page.model_picker, QComboBox), "LibraryPage model_picker is QComboBox")
        check(lib_page.model_picker.objectName() == "ModelPicker", "LibraryPage model_picker objectName == ModelPicker")
        # Only the primary model should appear (companion filtered by scan_library,
        # but here we inserted both — the combo shows all loaded models).
        count = lib_page.model_picker.count()
        check(count >= 1, f"LibraryPage model_picker has >= 1 items, got {count}")

        # -- RunPage --
        from app.pages.run import RunPage
        run_page = RunPage(
            config_store=config_store,
            library_store=library_store,
            profile_store=profile_store,
        )
        check(hasattr(run_page, "model_combo"), "RunPage has model_combo")
        check(isinstance(run_page.model_combo, QComboBox), "RunPage model_combo is QComboBox")
        check(run_page.model_combo.objectName() == "ModelPicker", "RunPage model_combo objectName == ModelPicker")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
