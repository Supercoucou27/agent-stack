from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout

from ..widgets.cards import Card, CardTitle, FieldTile, MonoLog
from .base import PageBase


class PlaceholderPage(PageBase):
    """Dense placeholder page for Phase 2 shell wiring."""

    def __init__(self, title: str, subtitle: str, parent=None):
        self._title_text = title
        self._subtitle_text = subtitle
        super().__init__(parent)
        self.setProperty("subtitle", subtitle)

    def build(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(CardTitle(self._title_text, card))
        body = QLabel(self._subtitle_text, card)
        body.setObjectName("Muted")
        body.setWordWrap(True)
        layout.addWidget(body)
        self._layout.addWidget(card)


class RunPlaceholderPage(PageBase):
    """Initial Run page shell matching the approved direction."""

    def build(self) -> None:
        hero = Card(self._body)
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(16, 14, 16, 14)
        hero_layout.setSpacing(6)
        hero_layout.addWidget(CardTitle("Run local llama-server", hero))
        subtitle = QLabel(
            "Main settings stay visible; advanced llama.cpp flags live in collapsible Kobold-style groups.",
            hero,
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        hero_layout.addWidget(subtitle)
        self._layout.addWidget(hero)

        settings = Card(self._body)
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(16, 14, 16, 14)
        settings_layout.setSpacing(12)
        settings_layout.addWidget(CardTitle("Main settings", settings))

        grid_host = QFrame(settings)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        tiles = [
            ("Context --ctx-size", "32768"),
            ("KV cache --cache-type-k", "q8_0"),
            ("GPU layers -ngl", "99"),
            ("CPU threads -t", "16"),
            ("Batch --batch-size", "2048"),
            ("UBatch --ubatch-size", "512"),
            ("Top-P --top-p", "0.95"),
            ("Top-K --top-k", "40"),
            ("Temperature --temp", "0.70"),
            ("Repeat penalty", "1.10"),
            ("Host --host", "127.0.0.1"),
            ("Port --port", "8080"),
        ]
        for idx, (label, value) in enumerate(tiles):
            grid.addWidget(FieldTile(label, value, grid_host), idx // 4, idx % 4)
        settings_layout.addWidget(grid_host)
        self._layout.addWidget(settings)

        advanced = Card(self._body)
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(16, 14, 16, 14)
        advanced_layout.setSpacing(8)
        advanced_layout.addWidget(CardTitle("Advanced groups", advanced))
        for text in (
            "▾ Model loading",
            "▸ GPU / offload",
            "▸ Context / KV cache",
            "▸ Performance",
            "▸ Server / API",
            "▸ Sampling defaults",
            "▸ Multimodal",
            "▸ Raw extra args",
        ):
            row = QLabel(text, advanced)
            row.setObjectName("Muted")
            row.setAlignment(Qt.AlignmentFlag.AlignLeft)
            advanced_layout.addWidget(row)
        self._layout.addWidget(advanced)

        logs = Card(self._body)
        logs_layout = QVBoxLayout(logs)
        logs_layout.setContentsMargins(16, 14, 16, 14)
        logs_layout.setSpacing(8)
        logs_layout.addWidget(CardTitle("Server logs", logs))
        log = MonoLog(logs)
        log.append_line("llama-server logs will stream here via QProcess.")
        log.append_line("health, active model, command, and API support stay visible in the inspector.")
        logs_layout.addWidget(log)
        self._layout.addWidget(logs)
