"""Top header bar showing the current page title and an optional subtitle."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from .. import theme


class Header(QFrame):
    """A static title bar. Pages can call :meth:`set_text` to retitle."""

    def __init__(self, title: str = "", subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Header")
        self.setFixedHeight(theme.HEADER_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignVCenter)

        self._title = QLabel(title, self)
        self._title.setObjectName("HeaderTitle")
        layout.addWidget(self._title)

        self._subtitle = QLabel(subtitle, self)
        self._subtitle.setObjectName("HeaderSubtitle")
        self._subtitle.setVisible(bool(subtitle))
        layout.addWidget(self._subtitle)

    def set_text(self, title: str, subtitle: str = "") -> None:
        self._title.setText(title)
        self._subtitle.setText(subtitle)
        self._subtitle.setVisible(bool(subtitle))
