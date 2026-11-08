"""Common base for stacked page widgets."""
from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget


class PagePolicy(Enum):
    """Determines how the shell lays out the sidebar / inspector for a page.

    - STANDARD: three-column layout (sidebar | content | inspector).
    - INSPECTOR_OPTIONAL: three-column, inspector collapsed by default.
    - FULL_WIDTH: inspector hidden, content fills the space.
    """

    STANDARD = "standard"
    INSPECTOR_OPTIONAL = "inspector_optional"
    FULL_WIDTH = "full_width"



class PageBase(QScrollArea):
    """A scrollable page that hosts dense, styled content.

    Subclasses populate the page by adding widgets to ``self._layout``
    inside :meth:`build` (called once from ``__init__``).
    """

    navigate_requested = Signal(str)  # emits NavItemId value string
    policy: PagePolicy = PagePolicy.STANDARD

    def __init__(self, parent=None):
        super().__init__(parent)
        # The scroll viewport takes the page styles; the QScrollArea itself
        # stays transparent so the shell background shows through gaps.
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._body = QWidget(self)
        self._body.setObjectName("PageBody")
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(20, 18, 20, 18)
        self._layout.setSpacing(14)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.setWidget(self._body)

        self.build()

    def build(self) -> None:
        """Override to populate the page. Default: empty body."""
