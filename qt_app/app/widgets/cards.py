"""Reusable card primitives: titles, field tiles, status chips, log blocks."""
from __future__ import annotations

from typing import Literal, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

ChipStyle = Literal["success", "warning", "accent", "muted"]


class ElidedLabel(QLabel):
    """Single-line label that elides instead of forcing parent layouts wider."""

    def __init__(self, text: str = "", parent=None, *, mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle):
        super().__init__("", parent)
        self._full_text = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self._apply_elide()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._apply_elide()
        super().resizeEvent(event)

    def _apply_elide(self) -> None:
        metrics = self.fontMetrics()
        QLabel.setText(self, metrics.elidedText(self._full_text, self._mode, max(0, self.width())))


class Card(QFrame):
    """A bordered, rounded panel — the base building block for content."""

    def __init__(self, parent: Optional[QFrame] = None, *, alt: bool = False):
        super().__init__(parent)
        self.setObjectName("CardAlt" if alt else "Card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)


class CardTitle(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("CardTitle")


class OptionCard(QFrame):
    """A vertical card for a single option: label, flag, editor, and a red changed-dot."""

    def __init__(self, label: str, flag: str, *, importance: int = 0, parent=None):
        super().__init__(parent)
        self.setObjectName("OptionCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)
        # Header row: [label  flag]       [●]
        header = QHBoxLayout()
        header.setSpacing(6)
        self._label = QLabel(label, self)
        self._label.setObjectName("OptionCardLabel")
        if importance:
            self._label.setProperty("important", str(importance))
            self._label.style().polish(self._label)
        self._flag = QLabel(flag, self)
        self._flag.setObjectName("OptionCardFlag")
        header.addWidget(self._label)
        header.addWidget(self._flag)
        header.addStretch(1)
        self._dot = QLabel("●", self)
        self._dot.setObjectName("OptionCardChangedDot")
        self._dot.setVisible(False)
        header.addWidget(self._dot)
        outer.addLayout(header)
        # Body area for the editor widget
        self._body = QWidget(self)
        self._body.setStyleSheet("background: transparent; border: none;")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(4)
        outer.addWidget(self._body)

    def add_editor(self, widget: QWidget) -> None:
        """Add the editor widget into the card body."""
        self._body_layout.addWidget(widget)

    def set_changed(self, changed: bool) -> None:
        """Toggle the red dot in the top-right corner."""
        self._dot.setVisible(changed)

    def set_label_text(self, text: str) -> None:
        self._label.setText(text)

    def set_flag_text(self, text: str) -> None:
        self._flag.setText(text)


class FieldTile(QFrame):
    """A small bordered tile showing a label (e.g. ``--ctx-size``) and value."""

    def __init__(self, label: str, value: str, parent=None):
        super().__init__(parent)
        self.setObjectName("InsetRaised")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(48)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        self._label = QLabel(label, self)
        self._label.setObjectName("FieldLabel")
        layout.addWidget(self._label)

        self._value = QLabel(value, self)
        self._value.setObjectName("FieldValue")
        self._value.setWordWrap(True)
        layout.addWidget(self._value)

    def set_value(self, value: str) -> None:
        self._value.setText(value)


class Chip(QLabel):
    """A small colored status pill (GPU likely, Partial GPU, ...)."""

    _STYLE_OBJECT: dict[ChipStyle, str] = {
        "success": "ChipSuccess",
        "warning": "ChipWarning",
        "accent": "ChipAccent",
        "muted": "ChipMuted",
    }

    def __init__(self, text: str, style: ChipStyle = "muted", parent=None):
        super().__init__(text, parent)
        self.setObjectName("Chip")
        self._current_style: ChipStyle = "muted"
        self.set_style(style)

    def set_style(self, style: ChipStyle) -> None:
        # Remove the previous object name suffix by resetting objectName, then
        # set the new style object name. QSS re-evaluates on objectName change.
        self.setObjectName("Chip")
        self._current_style = style
        self.setObjectName(self._STYLE_OBJECT.get(style, "ChipMuted"))
        # Force re-polish so the QSS rule applies.
        self.style().polish(self)


class MonoLog(QFrame):
    """A monospaced, preformatted block used for command previews and logs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Inset")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 8, 12, 8)
        self._layout.setSpacing(4)
        self._layout.setAlignment(Qt.AlignTop)

    def append_line(self, text: str) -> None:
        line = QLabel(text, self)
        line.setWordWrap(True)
        line.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        line.setObjectName("Mono")
        line.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._layout.addWidget(line)


class DownloadRow(QFrame):
    """One line in the download queue: filename + bytes + progress bar + cancel.

    The progress bar shows an indeterminate animation when the total size
    is unknown, and a determinate fill once the server reports
    ``Content-Length``. Each row has its own cancel button; the page wires
    the ``cancelled`` signal to the corresponding download id.
    """

    cancelled = Signal()

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("DownloadRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        self.name = ElidedLabel(label, self)
        self.name.setObjectName("DownloadRowName")
        layout.addWidget(self.name, 1)

        self.bytes_label = QLabel("—", self)
        self.bytes_label.setObjectName("Muted")
        self.bytes_label.setFixedWidth(100)
        self.bytes_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.bytes_label)

        self.bar = QProgressBar(self)
        self.bar.setRange(0, 0)  # indeterminate until we know the total
        self.bar.setValue(0)
        self.bar.setFixedWidth(160)
        self.bar.setFixedHeight(6)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)

        self.cancel_btn = QPushButton("\u00d7", self)
        self.cancel_btn.setToolTip("Cancel download")
        self.cancel_btn.setProperty("variant", "danger")
        self.cancel_btn.setFixedSize(22, 22)
        self.cancel_btn.clicked.connect(self.cancelled)
        layout.addWidget(self.cancel_btn)

    def set_cancel_enabled(self, enabled: bool) -> None:
        self.cancel_btn.setEnabled(enabled)

    def set_progress(self, downloaded: int, total: int | None) -> None:
        if total and total > 0:
            if self.bar.maximum() != 100:
                self.bar.setRange(0, 100)
            pct = max(0, min(100, int(downloaded * 100 / total)))
            self.bar.setValue(pct)
            self.bytes_label.setText(f"{_fmt_bytes(downloaded)} / {_fmt_bytes(total)}")
        else:
            if self.bar.maximum() != 0:
                self.bar.setRange(0, 0)  # indeterminate
            self.bytes_label.setText(f"{_fmt_bytes(downloaded)} / ?")

    def set_status(self, text: str) -> None:
        self.bytes_label.setText(text)


def _fmt_bytes(n: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} {u}"
        f /= 1024
    return f"{n} B"
