"""QApplication bootstrap, environment hints, and global configuration.

This module owns the lifecycle of the single QApplication. Constructing it is
separated from constructing the main window so future entry points (CLI mode,
tests) can reuse the configured application object.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from . import theme


# Allow HiDPI scaling on Wayland compositors that report fractional scale.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")


def _should_force_software_rendering() -> bool:
    """On NVIDIA + Wayland, QSG_RHI_BACKEND=software is a known safe fallback.

    The product is QWidget-only, so QSG is not used directly. This is a
    defensive hook for Qt internals (QQuickWidget etc. that some Qt
    submodules bring in). It does not affect rendering of QWidget windows.
    """
    return os.environ.get("QT_QUICK_BACKEND") == "software"


def create_app(argv: Optional[list[str]] = None) -> QApplication:
    """Create the singleton QApplication with theme and high-DPI configured."""
    if argv is None:
        argv = sys.argv

    app = QApplication.instance() or QApplication(argv)

    app.setApplicationName("llamaUI")
    app.setApplicationDisplayName("llamaUI")
    app.setOrganizationName("llamaUI")
    app.setOrganizationDomain("llamaUI.local")
    app.setDesktopFileName("llamaui")

    # Application icon — use the bundled PNG at the largest available size.
    _icons_dir = Path(__file__).resolve().parent.parent / "icons"
    if _icons_dir.is_dir():
        from PySide6.QtGui import QIcon
        icon = QIcon()
        for p in sorted(_icons_dir.glob("llamaui-*.png")):
            icon.addFile(str(p))
        app.setWindowIcon(icon)

    # KDE Wayland + NVIDIA hint: keep the EGL/GBM path the platform picks.
    # The PySide6 6.11 default Wayland plugin renders QWidget windows
    # directly to the surface, bypassing the WebKitGTK layer that broke
    # Tauri on the same machine.
    theme.apply_palette(app)
    app.setStyleSheet(theme.build_stylesheet())

    return app
