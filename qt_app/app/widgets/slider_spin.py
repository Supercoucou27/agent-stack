"""Composite widget: a horizontal QSlider paired with a spinbox,
sharing one value model.

Used for every numeric option in the Run page so the user can
either drag the slider thumb or type an exact value in the spinbox.
The composite re-emits ``valueChanged`` once per user action; both
internal widgets have their signals blocked during the secondary
update to prevent a feedback loop.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QWidget,
)


class SliderSpinBox(QWidget):
    """A horizontal QSlider paired with a QSpinBox, sharing one value."""

    valueChanged = Signal(int)

    def __init__(self, minimum: int, maximum: int, parent=None):
        super().__init__(parent)
        self._slider = QSlider(Qt.Horizontal, self)
        self._spin = QSpinBox(self)
        self._slider.setRange(minimum, maximum)
        self._spin.setRange(minimum, maximum)
        self._slider.setMinimumWidth(140)
        self._spin.setMinimumWidth(80)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._spin, 0)
        self.setStyleSheet("background: transparent; border: none;")

        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)
        self._suppress_emit = False

    def _on_slider(self, v: int) -> None:
        if self._suppress_emit:
            return
        self._suppress_emit = True
        self._spin.setValue(v)
        self._suppress_emit = False
        self.valueChanged.emit(v)

    def _on_spin(self, v: int) -> None:
        if self._suppress_emit:
            return
        self._suppress_emit = True
        self._slider.setValue(v)
        self._suppress_emit = False
        self.valueChanged.emit(v)

    def value(self) -> int:
        return self._spin.value()

    def setValue(self, v: int) -> None:
        # Block the secondary widget's signal during programmatic
        # set, but ALWAYS emit valueChanged on the composite so the
        # existing `valueChanged.connect(self._on_editor_changed)`
        # connection fires. (Qt's default for setValue on a
        # QSpinBox is to emit, and the composite must preserve that
        # contract.)
        self._suppress_emit = True
        self._slider.setValue(v)
        self._spin.setValue(v)
        self._suppress_emit = False
        self.valueChanged.emit(v)

    def setRange(self, lo: int, hi: int) -> None:
        self._slider.setRange(lo, hi)
        self._spin.setRange(lo, hi)

    def setSingleStep(self, s: int) -> None:
        self._spin.setSingleStep(s)
        self._slider.setSingleStep(s)

    def blockSignals(self, b: bool) -> None:
        self._slider.blockSignals(b)
        self._spin.blockSignals(b)

    def setMinimumWidth(self, w: int) -> None:
        super().setMinimumWidth(w)
        self._slider.setMinimumWidth(max(80, w - 100))


class SliderDoubleSpinBox(QWidget):
    """A horizontal QSlider paired with a QDoubleSpinBox, sharing one float value.

    The slider uses an integer representation: range ``[min, max]`` is
    multiplied by ``10 ** decimals`` and the integer value is shown.
    The spinbox shows the user-facing float value with ``decimals``
    digits after the point.
    """

    valueChanged = Signal(float)

    def __init__(self, minimum: float, maximum: float, decimals: int = 3, parent=None):
        super().__init__(parent)
        self._decimals = decimals
        self._factor = 10 ** decimals
        self._slider = QSlider(Qt.Horizontal, self)
        self._spin = QDoubleSpinBox(self)
        self._slider.setRange(int(minimum * self._factor), int(maximum * self._factor))
        self._spin.setRange(minimum, maximum)
        self._spin.setDecimals(decimals)
        self._slider.setMinimumWidth(140)
        self._spin.setMinimumWidth(80)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._slider, 1)
        layout.addWidget(self._spin, 0)
        self.setStyleSheet("background: transparent; border: none;")

        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)
        self._suppress_emit = False

    def _on_slider(self, v: int) -> None:
        if self._suppress_emit:
            return
        self._suppress_emit = True
        f = v / self._factor
        self._spin.setValue(f)
        self._suppress_emit = False
        self.valueChanged.emit(f)

    def _on_spin(self, v: float) -> None:
        if self._suppress_emit:
            return
        self._suppress_emit = True
        self._slider.setValue(int(round(v * self._factor)))
        self._suppress_emit = False
        self.valueChanged.emit(v)

    def value(self) -> float:
        return self._spin.value()

    def setValue(self, v: float) -> None:
        self._suppress_emit = True
        self._slider.setValue(int(round(v * self._factor)))
        self._spin.setValue(v)
        self._suppress_emit = False
        self.valueChanged.emit(v)

    def setRange(self, lo: float, hi: float) -> None:
        self._slider.setRange(int(lo * self._factor), int(hi * self._factor))
        self._spin.setRange(lo, hi)

    def setSingleStep(self, s: float) -> None:
        self._spin.setSingleStep(s)
        self._slider.setSingleStep(max(1, int(round(s * self._factor))))

    def setDecimals(self, d: int) -> None:
        self._decimals = d
        self._factor = 10 ** d
        self._spin.setDecimals(d)
        # Refresh slider range to match new factor
        self._slider.setRange(
            int(self._spin.minimum() * self._factor),
            int(self._spin.maximum() * self._factor),
        )
        self._slider.setValue(int(round(self._spin.value() * self._factor)))

    def blockSignals(self, b: bool) -> None:
        self._slider.blockSignals(b)
        self._spin.blockSignals(b)

    def setMinimumWidth(self, w: int) -> None:
        super().setMinimumWidth(w)
        self._slider.setMinimumWidth(max(80, w - 100))
