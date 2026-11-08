"""Left navigation rail with brand and selectable nav items."""
from __future__ import annotations
from enum import Enum
from pathlib import Path
from typing import Dict

from PySide6.QtCore import QByteArray, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme

# ---------------------------------------------------------------------------
# Icon loading
# ---------------------------------------------------------------------------

_ICONS_DIR = Path(__file__).resolve().parents[2] / "icons"


def _load_svg_icon(filename: str) -> QIcon:
    """Load an SVG icon from the icons directory, recolored for the dark theme.

    Replaces dark fill/stroke colours with the theme's muted-foreground colour
    so icons are visible on the dark sidebar.

    **Must only be called after QApplication is constructed** (QPixmap
    requirement).
    """
    import re
    raw = (_ICONS_DIR / filename).read_text(encoding="utf-8")
    # Replace any hex color in the range #000000..#3F3F3F (dark) with FG_SECONDARY.
    def _recolor(m):
        r, g, b = int(m.group(1)[:2], 16), int(m.group(1)[2:4], 16), int(m.group(1)[4:6], 16)
        return theme.FG_SECONDARY if max(r, g, b) < 96 else m.group(0)
    patched = re.sub(r"#([0-9a-fA-F]{6})", _recolor, raw)
    # Also handle 3-digit hex like #000
    def _recolor3(m):
        h = m.group(1)
        full = h[0]*2 + h[1]*2 + h[2]*2
        r, g, b = int(full[:2], 16), int(full[2:4], 16), int(full[4:6], 16)
        return theme.FG_SECONDARY if max(r, g, b) < 96 else m.group(0)
    patched = re.sub(r"#([0-9a-fA-F]{3})(?![0-9a-fA-F])", _recolor3, patched)

    svg_bytes = QByteArray(patched.encode("utf-8"))
    renderer = QSvgRenderer(svg_bytes)
    icon = QIcon()
    for size in (20, 32, 48):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pm)
    return icon

# ---------------------------------------------------------------------------
# Navigation identifiers & labels
# ---------------------------------------------------------------------------

class NavItemId(str, Enum):
    """Stable identifiers for navigation destinations."""

    LIBRARY = "library"
    DISCOVER = "discover"
    RUN = "run"
    SETTINGS = "settings"
    DASHBOARD = "dashboard"
    DIAGNOSTICS = "diagnostics"


_NAV_LABELS: Dict[NavItemId, str] = {
    NavItemId.LIBRARY: "Library",
    NavItemId.DISCOVER: "Discover",
    NavItemId.RUN: "Run",
    NavItemId.SETTINGS: "Settings",
    NavItemId.DASHBOARD: "Dashboard",
    NavItemId.DIAGNOSTICS: "Diagnostics",
}


# Icon filename mapping — loaded lazily to avoid QPixmap before QApplication.
_NAV_ICON_FILES: Dict[NavItemId, str] = {
    NavItemId.LIBRARY: "Library.svg",
    NavItemId.DISCOVER: "Discover.svg",
    NavItemId.RUN: "Run.svg",
    NavItemId.SETTINGS: "Settings.svg",
    NavItemId.DASHBOARD: "Dashboard.svg",
    NavItemId.DIAGNOSTICS: "Diagnostic.svg",
}
_nav_icon_cache: Dict[NavItemId, QIcon] | None = None


def _nav_icons() -> Dict[NavItemId, QIcon]:
    """Return the nav icon dict, loading from SVG on first call."""
    global _nav_icon_cache
    if _nav_icon_cache is None:
        _nav_icon_cache = {k: _load_svg_icon(v) for k, v in _NAV_ICON_FILES.items()}
    return _nav_icon_cache


_ICON_SIZE = 20  # px – icon size for collapsed nav items


# ---------------------------------------------------------------------------
# Sidebar widget
# ---------------------------------------------------------------------------

class Sidebar(QFrame):
    """Left rail that emits a :class:`NavItemId` when an item is selected.

    Supports collapsing to a narrow icon-only strip via :meth:`set_collapsed`.
    The ``collapse_changed`` signal fires whenever the collapsed state toggles.
    """

    navigated = Signal(object)  # emits NavItemId
    collapse_changed = Signal(bool)  # emits new collapsed state

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self.setObjectName("Sidebar")
        self.setMinimumWidth(theme.SIDEBAR_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Brand row with collapse toggle ---
        brand_row = QWidget(self)
        brand_row_layout = QHBoxLayout(brand_row)
        brand_row_layout.setContentsMargins(0, 0, 0, 0)
        brand_row_layout.setSpacing(0)
        self._brand = QLabel("llamaUI", brand_row)
        self._brand.setObjectName("SidebarBrand")
        brand_row_layout.addWidget(self._brand, 1)
        self._toggle_btn = QPushButton("◀", brand_row)
        self._toggle_btn.setObjectName("SidebarToggle")
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.setFixedWidth(28)
        self._toggle_btn.setToolTip("Collapse sidebar")
        self._toggle_btn.clicked.connect(self.toggle_collapsed)
        brand_row_layout.addWidget(self._toggle_btn)
        layout.addWidget(brand_row)
        self._brand_row = brand_row

        # --- Nav items ---
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: Dict[NavItemId, QPushButton] = {}

        for item_id in NavItemId:
            btn = QPushButton(_NAV_LABELS[item_id], self)
            btn.setObjectName("NavItem")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            self._group.addButton(btn)
            self._buttons[item_id] = btn
            layout.addWidget(btn)

        layout.addStretch(1)

        # --- Status section ---
        self._status = QFrame(self)
        self._status.setObjectName("SidebarStatus")
        status_layout = QVBoxLayout(self._status)
        status_layout.setContentsMargins(14, 10, 14, 12)
        status_layout.setSpacing(4)
        self._status_title = QLabel("Run", self._status)
        self._status_title.setObjectName("SidebarStatusTitle")
        self._status_title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        status_layout.addWidget(self._status_title)
        self._status_chip = QLabel("stopped", self._status)
        self._status_chip.setObjectName("SidebarStatusChip")
        status_layout.addWidget(self._status_chip)
        self._status_line1 = QLabel("No model selected", self._status)
        self._status_line1.setObjectName("Muted")
        self._status_line1.setWordWrap(True)
        status_layout.addWidget(self._status_line1)
        self._status_line2 = QLabel("endpoint=http://127.0.0.1:8080", self._status)
        self._status_line2.setObjectName("Muted")
        self._status_line2.setWordWrap(True)
        status_layout.addWidget(self._status_line2)
        layout.addWidget(self._status)

        for item_id, btn in self._buttons.items():
            btn.clicked.connect(lambda _checked=False, i=item_id: self._on_clicked(i))

    # -- collapse ---------------------------------------------------------------

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        """Toggle between expanded and collapsed (icon-only) sidebar."""
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed

        if collapsed:
            # Hide brand text, make toggle button full-width and prominent.
            self._brand.setVisible(False)
            self._toggle_btn.setText("▶")
            self._toggle_btn.setToolTip("Expand sidebar")
            self._toggle_btn.setFixedWidth(theme.SIDEBAR_COLLAPSED_WIDTH)
            self._toggle_btn.setProperty("collapsed", True)
            self._toggle_btn.style().unpolish(self._toggle_btn)
            self._toggle_btn.style().polish(self._toggle_btn)
            # Switch nav items to icon-only mode.
            for item_id, btn in self._buttons.items():
                btn.setText("")
                btn.setIcon(_nav_icons()[item_id])
                btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
                btn.setProperty("collapsed", True)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            self._status.setVisible(False)
            self.setMinimumWidth(theme.SIDEBAR_COLLAPSED_WIDTH)
            self.setMaximumWidth(theme.SIDEBAR_COLLAPSED_WIDTH)
        else:
            self._brand.setVisible(True)
            self._brand.setText("llamaUI")
            self._brand.setProperty("collapsed", False)
            self._toggle_btn.setText("◀")
            self._toggle_btn.setToolTip("Collapse sidebar")
            self._toggle_btn.setFixedWidth(28)
            self._toggle_btn.setProperty("collapsed", False)
            self._toggle_btn.style().unpolish(self._toggle_btn)
            self._toggle_btn.style().polish(self._toggle_btn)
            for item_id, btn in self._buttons.items():
                btn.setText(_NAV_LABELS[item_id])
                btn.setIcon(QIcon())
                btn.setProperty("collapsed", False)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            self._status.setVisible(True)
            self.setMinimumWidth(theme.SIDEBAR_MIN_WIDTH)
            self.setMaximumWidth(16777215)  # QWIDGETSIZE_MAX

        self._brand.style().unpolish(self._brand)
        self._brand.style().polish(self._brand)

        self.collapse_changed.emit(collapsed)

    # -- original API -----------------------------------------------------------

    def _on_clicked(self, item_id: NavItemId) -> None:
        self.navigated.emit(item_id)

    def set_active(self, item_id: NavItemId) -> None:
        """Mark ``item_id`` as the currently selected page."""
        btn = self._buttons[item_id]
        btn.setChecked(True)

    def update_details(self, title: str, chip_text: str, chip_style: str, line1: str, line2: str, command_lines: list[str] | None = None) -> None:
        self._status_title.setText(title or "Run")
        self._status_chip.setText(chip_text or "—")
        self._status_chip.setProperty("chipStyle", chip_style or "muted")
        self._status_chip.style().unpolish(self._status_chip)
        self._status_chip.style().polish(self._status_chip)
        self._status_line1.setText(line1 or "")
        self._status_line2.setText(line2 or "")
