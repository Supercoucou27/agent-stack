"""Smoke test for Phase 11 Section 4b/4c: enum dropdowns + importance labels."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QLineEdit,
)

app = QApplication.instance() or QApplication(sys.argv)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.application import create_app  # noqa: E402
from app.pages.run import RunPage  # noqa: E402
from llama_data import (  # noqa: E402
    AppConfig,
    ConfigStore,
    LLAMA_OPTION_CATALOG,
    default_paths,
)
from app.widgets.slider_spin import SliderSpinBox  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"pass {message}")


def main() -> int:
    create_app()
    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        paths.ensure()
        cfg = ConfigStore(paths)
        cfg.save(AppConfig(llama_server_path=""))  # empty → catalog path
        page = RunPage(config_store=cfg)

        # Enum options should use QComboBox.
        for option_id, min_items in [
            ("cache_type_k", 5),
            ("cache_type_v", 5),
            ("rope_scaling", 3),
            ("split_mode", 3),
        ]:
            widget = page._editors.get(option_id)
            check(widget is not None, f"{option_id} editor exists")
            check(isinstance(widget, QComboBox), f"{option_id} is QComboBox")
            check(
                widget.count() >= min_items + 1,
                f"{option_id} has >= {min_items} enum items (+ sentinel)",
            )

        # Integer option should be a SliderSpinBox now.
        widget = page._editors.get("ctx_size")
        check(widget is not None, "ctx_size editor exists")
        check(isinstance(widget, SliderSpinBox), "ctx_size is SliderSpinBox")


        # Enum combobox stores value in userData, not display text.
        ctk: QComboBox = page._editors["cache_type_k"]
        ctk.setCurrentIndex(0)
        check(ctk.currentData() is None, "(unset) sentinel returns None")
        idx = ctk.findData("q8_0")
        check(idx >= 0, "q8_0 found in cache_type_k combobox")
        ctk.setCurrentIndex(idx)
        check(ctk.currentData() == "q8_0", "currentData returns q8_0")

        # Importance labels: verify a highlighted option has the property set.
        opt = LLAMA_OPTION_CATALOG.get("cache_type_k")
        check(opt is not None, "cache_type_k catalog entry exists")
        check(opt.importance == 1, "cache_type_k importance == 1")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
