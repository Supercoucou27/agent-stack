"""Application entry point.

Supports all invocation styles:

- ``python qt_app/main.py`` — script mode
- ``python -m qt_app`` — package mode
- ``llamaui`` — pip-installed entry point

All three require the project root (containing both ``qt_app/`` and
``llama_data/``) on ``sys.path``.  This module ensures that *before*
any qt_app sub-module is imported.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

# --- Ensure llama_data is importable --------------------------------------
# llama_data lives inside qt_app/llama_data/ but is imported as a top-level
# package (``from llama_data import …``) throughout the codebase.
# ``python -m qt_app`` puts qt_app/ on sys.path → llama_data resolves.
# The pip entry point does NOT, so we add it unconditionally here.
_QT_APP_DIR = str(Path(__file__).resolve().parent)
if _QT_APP_DIR not in sys.path:
    sys.path.insert(0, _QT_APP_DIR)

# --- Now safe to import from qt_app and llama_data -------------------------
from qt_app.app import MainWindow, create_app  # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    """Configure the QApplication, show the shell, and run the event loop."""
    app = create_app(argv if argv is not None else sys.argv)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
