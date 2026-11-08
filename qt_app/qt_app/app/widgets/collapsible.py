"""Collapsible group — an expand/collapse card section with a clickable header."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class CollapsibleGroup(QWidget):
    """A titled, collapsible section widget.

    - Header strip shows a caret, title, and optional item count.
    - Clicking the header toggles the body visibility.
    - Emits ``toggled(bool)`` on expand/collapse.
    """

    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        parent=None,
        *,
        initially_expanded: bool = False,
    ):
        super().__init__(parent)
        self.setObjectName("CollapsibleGroup")
        self._expanded = initially_expanded

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header strip: [▸ title]            (item count)
        self._header = QFrame(self)
        self._header.setObjectName("CollapsibleGroupHeader")
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(12, 6, 12, 6)
        self._caret = QLabel("▸", self._header)
        self._caret.setObjectName("CollapsibleGroupCaret")
        self._title_label = QLabel(title, self._header)
        self._title_label.setObjectName("CollapsibleGroupTitle")
        self._count_label = QLabel("", self._header)
        self._count_label.setObjectName("Muted")
        header_layout.addWidget(self._caret)
        header_layout.addWidget(self._title_label, 1)
        header_layout.addWidget(self._count_label)
        outer.addWidget(self._header)

        # Body
        self._body = QFrame(self)
        self._body.setObjectName("CollapsibleGroupBody")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(12, 8, 12, 12)
        self._body_layout.setSpacing(8)
        outer.addWidget(self._body)

        # Click handler
        self._header.mousePressEvent = self._on_header_click
        self.set_expanded(initially_expanded)

    # -- public API --

    def add_widget(self, w: QWidget) -> None:
        self._body_layout.addWidget(w)

    def add_layout(self, layout) -> None:
        self._body_layout.addLayout(layout)

    def set_count(self, n: int) -> None:
        self._count_label.setText(f"({n})" if n else "")

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._body.setVisible(expanded)
        self._caret.setText("▾" if expanded else "▸")
        self.toggled.emit(expanded)

    # -- internal --

    def _on_header_click(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_expanded(not self._expanded)
