from __future__ import annotations
import os
from typing import Optional
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
)
from llama_data.llama_options import LLAMA_OPTION_CATALOG, SettingValueMap
from llama_data.models import AppConfig, HfTokenSource
from llama_data.stores import ConfigStore

from ..services.dialogs import pick_directory, pick_file
from ..services.option_schema import build_runtime_schema
from ..widgets.buttons import DangerButton, SecondaryButton, SuccessButton
from ..widgets.cards import Card, CardTitle, Chip
from .base import PageBase, PagePolicy


class SettingsPage(PageBase):
    """Settings page with real ConfigStore persistence."""
    policy = PagePolicy.INSPECTOR_OPTIONAL

    def __init__(self, parent=None) -> None:
        self._config_store = ConfigStore.default()
        self._config: Optional[AppConfig] = None
        self._save_feedback_timer = None
        super().__init__(parent)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> None:
        self.setProperty("subtitle", "Configure paths, connection, and Hugging Face access.")
        self._load_config()

        self._build_binary_card()
        self._build_models_dir_card()
        self._build_connection_card()
        self._build_server_mode_card()
        self._build_global_defaults_card()
        self._build_hf_token_card()
        self._build_introspection_card()
        self._build_save_row()

    # ------------------------------------------------------------------
    # Card builders
    # ------------------------------------------------------------------

    def _build_binary_card(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(CardTitle("llama-server binary", card))

        row = QHBoxLayout()
        row.setSpacing(8)
        self._server_path_input = QLineEdit(card)
        self._server_path_input.setPlaceholderText("/path/to/llama-server")
        if self._config and self._config.llama_server_path:
            self._server_path_input.setText(self._config.llama_server_path)
        row.addWidget(self._server_path_input, 1)
        browse = SecondaryButton("Browse", card)
        browse.clicked.connect(self._browse_server)
        row.addWidget(browse)
        validate = SuccessButton("Validate + Parse", card)
        validate.clicked.connect(self._validate)
        row.addWidget(validate)
        layout.addLayout(row)

        self._layout.addWidget(card)

    def _build_models_dir_card(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(CardTitle("Model Download Directory", card))

        row = QHBoxLayout()
        row.setSpacing(8)
        self._models_dir_input = QLineEdit(card)
        self._models_dir_input.setPlaceholderText("Directory for downloaded GGUF files")
        if self._config and self._config.models_dir:
            self._models_dir_input.setText(self._config.models_dir)
        row.addWidget(self._models_dir_input, 1)
        browse = SecondaryButton("Browse", card)
        browse.clicked.connect(self._browse_models_dir)
        row.addWidget(browse)
        layout.addLayout(row)

        self._layout.addWidget(card)

    def _build_connection_card(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(CardTitle("Connection Settings", card))
        hint = QLabel("Local host/port are used for launching and attaching to the local llama-server.", card)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._host_input = QLineEdit(card)
        self._host_input.setText(self._config.host if self._config else "127.0.0.1")
        self._host_input.setMinimumWidth(200)
        form.addRow("Host", self._host_input)

        self._port_input = QSpinBox(card)
        self._port_input.setRange(1, 65535)
        self._port_input.setValue(self._config.port if self._config else 8080)
        form.addRow("Port", self._port_input)

        self._remote_monitor_check = QCheckBox("Monitor a remote llama-server instead on Dashboard", card)
        self._remote_monitor_check.setChecked(self._config.remote_monitor_enabled if self._config else False)
        form.addRow("", self._remote_monitor_check)

        self._remote_host_input = QLineEdit(card)
        self._remote_host_input.setText(self._config.remote_monitor_host if self._config else "127.0.0.1")
        self._remote_host_input.setMinimumWidth(200)
        form.addRow("Remote monitor host", self._remote_host_input)

        self._remote_port_input = QSpinBox(card)
        self._remote_port_input.setRange(1, 65535)
        self._remote_port_input.setValue(self._config.remote_monitor_port if self._config else 8080)
        form.addRow("Remote monitor port", self._remote_port_input)

        layout.addLayout(form)
        self._layout.addWidget(card)

    def _build_server_mode_card(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(CardTitle("Server Mode", card))

        hint = QLabel(
            "Router mode serves all models from your models directory. "
            "Remote clients can list and switch models through llama-server's "
            "built-in /v1/models endpoint — no extra configuration needed.",
            card,
        )
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._router_mode_check = QCheckBox("Enable router mode (--models-dir)", card)
        self._router_mode_check.setChecked(self._config.router_mode if self._config else False)
        self._router_mode_check.toggled.connect(self._on_router_mode_toggled)
        layout.addWidget(self._router_mode_check)

        # Show the models dir as a read-only label so it doesn't steal the
        # editable input from the download directory card.
        self._router_dir_display = QLabel(card)
        self._router_dir_display.setObjectName("Muted")
        self._router_dir_display.setWordWrap(True)
        self._router_dir_display.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._router_dir_display)
        self._on_router_mode_toggled(self._router_mode_check.isChecked())

        self._layout.addWidget(card)

    def _on_router_mode_toggled(self, checked: bool) -> None:
        if hasattr(self, '_models_dir_input'):
            self._models_dir_input.setEnabled(True)  # always editable
        if hasattr(self, '_router_dir_display'):
            path = self._models_dir_input.text().strip() if hasattr(self, '_models_dir_input') else ""
            self._router_dir_display.setText(f"Models directory: {path or '—'}")
            self._router_dir_display.setVisible(checked)

    def _build_global_defaults_card(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(CardTitle("Global defaults", card))

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._global_threads = QSpinBox(card)
        self._global_threads.setRange(0, 1024)
        form.addRow("Threads", self._global_threads)

        self._global_batch = QSpinBox(card)
        self._global_batch.setRange(0, 1_000_000)
        form.addRow("Batch size", self._global_batch)

        self._global_gpu_layers = QSpinBox(card)
        self._global_gpu_layers.setRange(0, 1_000_000)
        form.addRow("GPU layers", self._global_gpu_layers)

        self._global_temp = QDoubleSpinBox(card)
        self._global_temp.setDecimals(3)
        self._global_temp.setRange(0.0, 10.0)
        form.addRow("Temperature", self._global_temp)

        fields = [
            ("threads", self._global_threads),
            ("batch_size", self._global_batch),
            ("n_gpu_layers", self._global_gpu_layers),
            ("temp", self._global_temp),
        ]
        for option_id, widget in fields:
            value = self._config.global_settings.get(option_id) if self._config else None
            if value is not None and value.value is not None:
                if isinstance(widget, QDoubleSpinBox):
                    widget.setValue(float(value.value))
                else:
                    widget.setValue(int(value.value))

        layout.addLayout(form)
        self._layout.addWidget(card)

    def _build_hf_token_card(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)
        layout.addWidget(CardTitle("Hugging Face Token", card))

        # Source row
        source_layout = QHBoxLayout()
        source_layout.setSpacing(8)
        source_label = QLabel("Token source:", card)
        source_label.setObjectName("Muted")
        source_layout.addWidget(source_label)
        self._token_chip = Chip("No token", "muted", card)
        source_layout.addWidget(self._token_chip)
        source_layout.addStretch()
        self._clear_token_btn = DangerButton("Clear", card)
        self._clear_token_btn.clicked.connect(self._clear_token)
        source_layout.addWidget(self._clear_token_btn)
        layout.addLayout(source_layout)

        # Input row
        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)
        self._token_input = QLineEdit(card)
        self._token_input.setPlaceholderText("hf_xxxxxxxx (optional)")
        self._token_input.setEchoMode(QLineEdit.EchoMode.Password)
        input_layout.addWidget(self._token_input, 1)
        save_token_btn = SuccessButton("Save + Validate", card)
        save_token_btn.clicked.connect(self._save_token)
        input_layout.addWidget(save_token_btn)
        layout.addLayout(input_layout)

        # Feedback
        self._token_feedback = QLabel("", card)
        self._token_feedback.setWordWrap(True)
        self._token_feedback.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._token_feedback.setObjectName("Muted")
        layout.addWidget(self._token_feedback)

        hint = QLabel(
            "A token is optional but needed for gated models or higher rate limits.\n"
            "If the HF_TOKEN environment variable is set, it is detected automatically.",
            card,
        )
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._layout.addWidget(card)
        self._update_token_chip()

    def _build_introspection_card(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(CardTitle("Binary Introspection", card))

        self._summary = QLabel(
            "Validate a llama-server binary to see its version, supported options, and capabilities.",
            card,
        )
        self._summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._summary.setObjectName("Muted")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._details = QPlainTextEdit(card)
        self._details.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._details.setReadOnly(True)
        self._details.setMaximumHeight(220)
        self._details.setPlaceholderText("Parsed binary details will appear here...")
        layout.addWidget(self._details)

        self._layout.addWidget(card)

    def _build_save_row(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addStretch()

        self._save_feedback = QLabel("", self._body)
        self._save_feedback.setWordWrap(True)
        self._save_feedback.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._save_feedback.setObjectName("Muted")
        row.addWidget(self._save_feedback)

        save = SuccessButton("Save Configuration", self._body)
        save.clicked.connect(self._save_config)
        row.addWidget(save)
        self._layout.addLayout(row)

    # ------------------------------------------------------------------
    # ConfigStore I/O
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        """Load config from disk. Falls back to defaults on missing/invalid."""
        try:
            self._config = self._config_store.load()
        except Exception:
            self._config = AppConfig()

    def _persist_config(self, config: AppConfig) -> None:
        """Write config to disk and update local reference."""
        self._config_store.save(config)
        self._config = config

    # ------------------------------------------------------------------
    # Build a config snapshot from current form state
    # ------------------------------------------------------------------

    def _config_from_form(self, token_source: Optional[HfTokenSource] = None) -> AppConfig:
        server_path = self._server_path_input.text().strip() or None
        models_dir = self._models_dir_input.text().strip() or None
        host = self._host_input.text().strip() or "127.0.0.1"
        port = self._port_input.value()

        if token_source is None:
            token_source = self._config.hf_token_source if self._config else HfTokenSource()

        global_settings = self._config.global_settings if self._config else None
        global_settings = global_settings.copy() if global_settings is not None else SettingValueMap()
        for option_id, widget in (("threads", self._global_threads), ("batch_size", self._global_batch), ("n_gpu_layers", self._global_gpu_layers), ("temp", self._global_temp)):
            option = LLAMA_OPTION_CATALOG.get(option_id)
            if option is not None:
                value = float(widget.value()) if isinstance(widget, QDoubleSpinBox) else int(widget.value())
                global_settings = global_settings.with_value(option, value if value != 0 else None)
        return AppConfig(
            llama_server_path=server_path,
            models_dir=models_dir,
            host=host,
            port=port,
            hf_token_source=token_source,
            global_settings=global_settings,
            router_mode=self._router_mode_check.isChecked() if hasattr(self, '_router_mode_check') else (self._config.router_mode if self._config else False),
            selected_model_id=self._config.selected_model_id if self._config else None,
            selected_profile_id=self._config.selected_profile_id if self._config else None,
            remote_monitor_enabled=self._remote_monitor_check.isChecked(),
            remote_monitor_host=self._remote_host_input.text().strip() or "127.0.0.1",
            remote_monitor_port=self._remote_port_input.value(),
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _browse_server(self) -> None:
        result = pick_file(self, title="Select llama-server executable", name_filter="All files (*)")
        if result.accepted and result.paths:
            self._server_path_input.setText(str(result.paths[0]))

    def _browse_models_dir(self) -> None:
        result = pick_directory(self, title="Select model download directory")
        if result.accepted and result.paths:
            self._models_dir_input.setText(str(result.paths[0]))
            # Update the router mode display if it exists
            if hasattr(self, '_router_dir_display'):
                self._router_dir_display.setText(f"Models directory: {str(result.paths[0])}")

    def _validate(self) -> None:
        path = self._server_path_input.text().strip()
        if not path:
            self._summary.setText("Enter or browse to a llama-server binary first.")
            return
        probe, schema = build_runtime_schema(path)
        self._summary.setText(
            f"exists={probe.exists} executable={probe.is_executable} "
            f"looks_like_llama={probe.looks_like_llama_cpp} "
            f"parsed={schema.parsed_count} curated={schema.curated_supported_count} "
            f"unknown={schema.unknown_count}"
        )
        lines = [
            f"version: {probe.version or 'unknown'}",
            f"binary: {schema.binary.path}",
            "",
            "first parsed options:",
        ]
        for option in schema.options[:40]:
            marker = "curated" if option.curated else "unknown"
            lines.append(
                f"{option.flag:24} {option.kind:10} {option.group:16} {marker}  {option.description}"
            )
        self._details.setPlainText("\n".join(lines))

    def _save_config(self) -> None:
        try:
            cfg = self._config_from_form()
            self._persist_config(cfg)
            self._show_save_feedback("Configuration saved.", success=True)
        except Exception as exc:
            self._show_save_feedback(f"Save failed: {exc}", success=False)

    def _validate_token(self, token: str | None) -> str:
        from ..services.hugging_face import check_hf_connectivity
        result = check_hf_connectivity(token)
        return f"validated ({result.status_detail})" if result.reachable else f"saved, validation failed ({result.status_detail})"

    def _save_token(self) -> None:
        raw = self._token_input.text().strip()
        if not raw:
            return
        token_source = HfTokenSource(kind="saved", token=raw)
        try:
            cfg = self._config_from_form(token_source=token_source)
            self._persist_config(cfg)
            self._token_input.clear()
            self._update_token_chip()
            self._show_token_feedback(f"Token {self._validate_token(raw)}.")
        except Exception as exc:
            self._show_token_feedback(f"Failed: {exc}")

    def _clear_token(self) -> None:
        token_source = HfTokenSource(kind="none")
        try:
            cfg = self._config_from_form(token_source=token_source)
            self._persist_config(cfg)
            self._token_input.clear()
            self._update_token_chip()
            self._show_token_feedback("Token cleared.")
        except Exception as exc:
            self._show_token_feedback(f"Failed: {exc}")

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _update_token_chip(self) -> None:
        if self._config is None:
            self._token_chip.setText("No token")
            self._token_chip.set_style("muted")
            self._clear_token_btn.setVisible(False)
            return

        src = self._config.hf_token_source
        # Check env var first (same priority as Tauri resolve_hf_token)
        if os.environ.get("HF_TOKEN"):
            self._token_chip.setText("Detected HF_TOKEN env var")
            self._token_chip.set_style("success")
            self._clear_token_btn.setVisible(False)
        elif src.kind == "saved" and src.token:
            self._token_chip.setText("Saved token")
            self._token_chip.set_style("accent")
            self._clear_token_btn.setVisible(True)
        elif src.kind == "env_var":
            self._token_chip.setText("Env var (not set)")
            self._token_chip.set_style("warning")
            self._clear_token_btn.setVisible(False)
        else:
            self._token_chip.setText("No token")
            self._token_chip.set_style("muted")
            self._clear_token_btn.setVisible(False)

    def _show_token_feedback(self, msg: str) -> None:
        self._token_feedback.setText(msg)
        if self._save_feedback_timer is not None:
            self._save_feedback_timer.stop()
        from PySide6.QtCore import QTimer

        self._save_feedback_timer = QTimer(self)
        self._save_feedback_timer.setSingleShot(True)
        self._save_feedback_timer.timeout.connect(lambda: self._token_feedback.setText(""))
        self._save_feedback_timer.start(4000)

    def _show_save_feedback(self, msg: str, *, success: bool) -> None:
        self._save_feedback.setText(msg)
        self._save_feedback.setStyleSheet(
            f"color: {'#22c55e' if success else '#ef4444'};"
        )
        if self._save_feedback_timer is not None:
            self._save_feedback_timer.stop()
        from PySide6.QtCore import QTimer

        self._save_feedback_timer = QTimer(self)
        self._save_feedback_timer.setSingleShot(True)
        self._save_feedback_timer.timeout.connect(lambda: self._save_feedback.setText(""))
        self._save_feedback_timer.start(4000)
