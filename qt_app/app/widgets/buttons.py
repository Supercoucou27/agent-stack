"""Button variants used across pages.

Centralizing the variant property keeps individual call sites readable:
``SecondaryButton("Open HF")`` rather than the equivalent QSS-tagged dance.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QSizePolicy


class _VariantButton(QPushButton):
    _VARIANT: str = ""
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        if self._VARIANT:
            self.setProperty("variant", self._VARIANT)
class SecondaryButton(_VariantButton):
    _VARIANT = "secondary"


class DangerButton(_VariantButton):
    _VARIANT = "danger"


class SuccessButton(_VariantButton):
    _VARIANT = "success"


class FilterPill(QPushButton):
    """Toggleable pill for filter chips (Discover, Library, etc.)."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("FilterPill")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
