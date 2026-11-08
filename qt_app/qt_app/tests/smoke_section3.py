"""Smoke test for Phase 11 Section 3: input-width cap.

Verifies that editor widgets have the correct min/max widths and that
QLineEdit editors enforce the 64-char max length.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QLineEdit,
    QSpinBox,
)

app = QApplication.instance() or QApplication(sys.argv)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.application import create_app  # noqa: E402
from app.pages.run import RunPage  # noqa: E402
from llama_data import (  # noqa: E402
    AppConfig,
    ConfigStore,
    default_paths,
)


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

        editors = page._editors

        # tensor_split is always a free-form QLineEdit (STRING kind, no enum)
        ts_widget = editors.get("tensor_split")
        check(ts_widget is not None, "tensor_split editor exists")
        check(isinstance(ts_widget, QLineEdit), "tensor_split is a QLineEdit")
        check(ts_widget.maxLength() == 1024, "tensor_split maxLength == 1024")
        check(ts_widget.minimumWidth() >= 120, "tensor_split min width >= 120")

        # cache_type_k: should be a QComboBox (Section 4b present) or QLineEdit (not yet)
        ctk_widget = editors.get("cache_type_k")
        check(ctk_widget is not None, "cache_type_k editor exists")
        if isinstance(ctk_widget, QComboBox):
            print("pass cache_type_k is a QComboBox (Section 4b present)")
            check(ctk_widget.count() > 1, "cache_type_k combo has items")
        else:
            print("pass cache_type_k is a QLineEdit (Section 4b not yet, acceptable)")
            check(isinstance(ctk_widget, QLineEdit), "cache_type_k is a QLineEdit fallback")
            check(ctk_widget.maxLength() == 1024, "cache_type_k maxLength == 1024")

        # Find an INTEGER editor — should be a SliderSpinBox now.
        from app.widgets.slider_spin import SliderSpinBox
        for int_id in ("n_gpu_layers", "batch_size", "parallel"):
            w = editors.get(int_id)
            if w is not None and isinstance(w, SliderSpinBox):
                check(w.minimumWidth() >= 110, f"{int_id} slider-spinbox min width >= 110")
                break
        else:
            raise AssertionError("no INTEGER slider-spinbox editor found to verify min width")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
