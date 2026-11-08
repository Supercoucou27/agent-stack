from __future__ import annotations

import os
from typing import Optional

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout

from llama_data import ConfigStore
from llama_data.models import AppConfig

from ..services.diagnostics import framework_diagnostics
from ..services.hugging_face import check_hf_connectivity
from ..services.option_schema import build_runtime_schema
from ..widgets.buttons import SecondaryButton
from ..widgets.cards import Card, CardTitle, Chip
from .base import PageBase, PagePolicy


def _fmt(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "—"
    if value is None or value == "":
        return "—"
    return str(value)


def _resolve_hf_token(config: AppConfig) -> tuple[Optional[str], str]:
    """Return (token_or_None, source_label) following the same priority as Settings."""
    env = os.environ.get("HF_TOKEN")
    if env:
        return env, "env-var"
    src = config.hf_token_source
    if src.kind == "saved" and src.token:
        return src.token, "saved"
    return None, "none"


class DiagnosticsPage(PageBase):
    policy = PagePolicy.FULL_WIDTH
    def __init__(self, config_store: ConfigStore | None = None, parent=None):
        self.config_store = config_store or ConfigStore.default()
        super().__init__(parent)

    def build(self) -> None:
        self.setProperty(
            "subtitle",
            "Framework, llama-server parse stats, and HuggingFace connectivity.",
        )

        # --- Card 1: Framework ---
        self._build_framework_card()

        # --- Card 2: llama-server binary ---
        self._build_binary_card()

        # --- Card 3: HuggingFace connectivity ---
        self._build_hf_card()

        self._refresh()

    # ------------------------------------------------------------------
    # Card builders
    # ------------------------------------------------------------------

    def _build_framework_card(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(CardTitle("Framework diagnostics", card))
        self._fw_summary = QLabel("", card)
        self._fw_summary.setObjectName("Muted")
        self._fw_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._fw_summary)
        self._fw_details = QPlainTextEdit(card)
        self._fw_details.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._fw_details.setReadOnly(True)
        self._fw_details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._fw_details)
        self._layout.addWidget(card)

    def _build_binary_card(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(CardTitle("llama-server binary", card))

        row = QHBoxLayout()
        self._bin_summary = QLabel("", card)
        self._bin_summary.setObjectName("Muted")
        self._bin_summary.setWordWrap(True)
        self._bin_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self._bin_summary, 1)
        layout.addLayout(row)
        self._bin_details = QPlainTextEdit(card)
        self._bin_details.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._bin_details.setReadOnly(True)
        self._bin_details.setMaximumHeight(160)
        self._bin_details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._bin_details)

        self._layout.addWidget(card)

    def _build_hf_card(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(CardTitle("Hugging Face connectivity", card))

        status_row = QHBoxLayout()
        lbl = QLabel("API reachable:", card)
        lbl.setObjectName("Muted")
        status_row.addWidget(lbl)
        self._hf_reach_chip = Chip("—", "muted", card)
        status_row.addWidget(self._hf_reach_chip)
        status_row.addStretch()
        layout.addLayout(status_row)

        token_row = QHBoxLayout()
        lbl2 = QLabel("Token source:", card)
        lbl2.setObjectName("Muted")
        token_row.addWidget(lbl2)
        self._hf_token_chip = Chip("—", "muted", card)
        token_row.addWidget(self._hf_token_chip)
        token_row.addStretch()
        layout.addLayout(token_row)

        self._hf_detail = QLabel("", card)
        self._hf_detail.setObjectName("Muted")
        self._hf_detail.setWordWrap(True)
        self._hf_detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._hf_detail)

        refresh_row = QHBoxLayout()
        refresh_row.addStretch(1)
        refresh = SecondaryButton("Refresh", card)
        refresh.clicked.connect(self._refresh)
        refresh_row.addWidget(refresh)
        layout.addLayout(refresh_row)

        self._layout.addWidget(card)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        # Framework
        diag = framework_diagnostics()
        self._fw_summary.setText(
            f"framework={diag.framework}  platform={diag.qt_platform_name or 'unknown'} "
            f"session={diag.xdg_session_type or 'unknown'}  gpu={diag.gpu_vendor.value}"
        )
        rows = diag.to_dict()
        self._fw_details.setPlainText(
            "\n".join(f"{key}: {_fmt(value)}" for key, value in rows.items())
        )

        # Config
        try:
            config = self.config_store.load()
        except Exception:
            config = AppConfig()

        # Binary stats
        path = config.llama_server_path
        if path:
            try:
                probe, schema = build_runtime_schema(path)
                self._bin_summary.setText(
                    f"version={probe.version or 'unknown'}  "
                    f"parsed={schema.parsed_count}  "
                    f"curated={schema.curated_supported_count}  "
                    f"unknown={schema.unknown_count}"
                )
                self._bin_details.setPlainText(
                    f"path: {probe.path}\n"
                    f"exists: {probe.exists}  executable: {probe.is_executable}  "
                    f"looks_like_llama: {probe.looks_like_llama_cpp}\n"
                    f"probed_at: {probe.probed_at}"
                )
            except Exception as exc:
                self._bin_summary.setText(f"Probe failed: {exc}")
                self._bin_details.setPlainText("")
        else:
            self._bin_summary.setText("No llama-server path configured. Set one in Settings.")
            self._bin_details.setPlainText("")

        # HF connectivity
        token, source = _resolve_hf_token(config)
        if source == "env-var":
            self._hf_token_chip.setText("HF_TOKEN env var")
            self._hf_token_chip.set_style("success")
        elif source == "saved":
            self._hf_token_chip.setText("Saved token")
            self._hf_token_chip.set_style("accent")
        else:
            self._hf_token_chip.setText("No token")
            self._hf_token_chip.set_style("muted")

        conn = check_hf_connectivity(token=token)
        if conn.reachable:
            self._hf_reach_chip.setText(f"Yes ({conn.latency_ms:.0f} ms)")
            self._hf_reach_chip.set_style("success")
        else:
            self._hf_reach_chip.setText("No")
            self._hf_reach_chip.set_style("warning")
        self._hf_detail.setText(conn.status_detail or ("—" if conn.reachable else ""))
