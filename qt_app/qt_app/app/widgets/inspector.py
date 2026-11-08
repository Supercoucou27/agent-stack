"""Right-side inspector panel.

Phase 8 note: this panel must not fabricate runtime state. It stays generic until
it is explicitly wired to a page/controller that owns real data.

Phase 11: responsive — uses minimum width instead of fixed width, adds a collapse
button in the title bar and a thin re-open strip when collapsed.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from .cards import Chip, MonoLog


class Inspector(QFrame):
    """Right-side inspector panel with collapse/expand toggle."""

    toggled = Signal(bool)  # True = expanded, False = collapsed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CardAlt")
        self.setMinimumWidth(theme.INSPECTOR_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(12)

        # --- Title bar with collapse button ---
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self._title = QLabel("Inspector", self)
        self._title.setObjectName("CardTitle")
        title_row.addWidget(self._title, 1)
        self._collapse_btn = QPushButton("◂", self)
        self._collapse_btn.setObjectName("InspectorCollapseBtn")
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.setToolTip("Collapse inspector")
        self._collapse_btn.clicked.connect(self._on_collapse)
        title_row.addWidget(self._collapse_btn)
        self._layout.addLayout(title_row)

        status_card = QFrame(self)
        status_card.setObjectName("Card")
        s_layout = QVBoxLayout(status_card)
        s_layout.setContentsMargins(12, 12, 12, 12)
        s_layout.setSpacing(6)
        self._chip = Chip("No live runtime bound", "muted", status_card)
        s_layout.addWidget(self._chip)
        self._line1 = QLabel("Select a page or model to inspect real details.", status_card)
        self._line1.setWordWrap(True)
        self._line1.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._line1.setObjectName("Muted")
        s_layout.addWidget(self._line1)
        self._line2 = QLabel("Runtime status is shown on the Run page.", status_card)
        self._line2.setWordWrap(True)
        self._line2.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._line2.setObjectName("Muted")
        s_layout.addWidget(self._line2)
        self._layout.addWidget(status_card)

        cmd_card = QFrame(self)
        cmd_card.setObjectName("InsetRaised")
        c_layout = QVBoxLayout(cmd_card)
        c_layout.setContentsMargins(10, 10, 10, 10)
        c_layout.setSpacing(4)
        self._command = MonoLog(cmd_card)
        self._command.append_line("No command preview available here.")
        c_layout.addWidget(self._command)
        self._layout.addWidget(cmd_card)
        self._layout.addStretch(1)

        # --- Collapsed strip (hidden by default) ---
        self._strip = _CollapsedStrip(self)
        self._strip.expand_requested.connect(self._on_expand)
        self._strip.hide()

    # -- collapse / expand -----------------------------------------------------

    def _on_collapse(self) -> None:
        self._do_collapse()

    def _do_collapse(self) -> None:
        self._content_hide()
        self._strip.show()
        self.toggled.emit(False)

    def _on_expand(self) -> None:
        self._strip.hide()
        self._content_show()
        self.toggled.emit(True)

    def _content_hide(self) -> None:
        """Hide the main content widgets (but keep the QFrame alive)."""
        self._layout.parentWidget()
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            w = item.widget()
            if w is not None:
                w.hide()
            else:
                # layout items (like title_row)
                sub = item.layout()
                if sub is not None:
                    for j in range(sub.count()):
                        sw = sub.itemAt(j).widget()
                        if sw is not None:
                            sw.hide()

    def _content_show(self) -> None:
        """Restore visibility of all content widgets."""
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            w = item.widget()
            if w is not None:
                w.show()
            else:
                sub = item.layout()
                if sub is not None:
                    for j in range(sub.count()):
                        sw = sub.itemAt(j).widget()
                        if sw is not None:
                            sw.show()

    # -- public API (unchanged) ------------------------------------------------

    def update_details(self, title: str, chip_text: str, chip_style: str, line1: str, line2: str, command_lines: list[str] | None = None) -> None:
        self._title.setText(title)
        self._chip.setText(chip_text)
        self._chip.set_style(chip_style)
        self._line1.setText(line1)
        self._line2.setText(line2)
        while self._command._layout.count():
            item = self._command._layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        for line in (command_lines or ["No command preview available here."]):
            self._command.append_line(line)
        return

    def set_context(self, title: str, chip_text: str, chip_style: str) -> None:
        self._title.setText(title)
        self._chip.setText(chip_text)
        self._chip.set_style(chip_style)


class _CollapsedStrip(QFrame):
    """Thin vertical strip shown when the inspector is collapsed.

    Displays a single ▸ glyph that re-opens the inspector.
    """

    expand_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CardAlt")
        self.setFixedWidth(28)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(0)

        btn = QPushButton("▸", self)
        btn.setObjectName("InspectorExpandBtn")
        btn.setFixedSize(24, 24)
        btn.setToolTip("Expand inspector")
        btn.clicked.connect(self.expand_requested.emit)
        layout.addWidget(btn, 0)
        layout.addStretch(1)
