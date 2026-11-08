from __future__ import annotations
import os
from pathlib import Path
import uuid
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QInputDialog,
    QMessageBox,
    QToolButton,
)


from llama_data import ConfigStore, LibraryStore, ModelProfile, PROFILE_PRESETS, ProfileStore
from llama_data.llama_options import (
    LLAMA_OPTION_CATALOG,
    LlamaOption,
    OptionKind,
    SettingValueMap,
    clean_raw_args,
)

from ..services.option_schema import (
    RuntimeOption,
    RuntimeSchema,
    SchemaCache,
    build_runtime_schema,
)
from ..services.runtime import LlamaServerController, ServerState, build_argv, generate_models_preset, is_port_available
from ..services.runtime_api import LlamaServerApiClient
from ..widgets.buttons import DangerButton, FilterPill, SecondaryButton, SuccessButton
from ..widgets.cards import Card, CardTitle, ElidedLabel, FieldTile, OptionCard
from ..widgets.flow import FlowLayout
from ..widgets.slider_spin import SliderDoubleSpinBox, SliderSpinBox
from .base import PageBase

MAIN_OPTION_IDS = [
    "ctx_size", "cache_type_k", "cache_type_v", "no_kv_offload", "flash_attn",
    "n_gpu_layers", "jinja", "reasoning", "reasoning_budget",
    "threads", "batch_size", "ubatch_size", "parallel",
    "host", "port", "temp", "top_k", "top_p", "repeat_penalty",
]

# Parser group slugs → display names for toolbox headers.
_GROUP_DISPLAY = {
    # Parser slugs → display names
    "model_loading": "Model loading",
    "performance": "Performance",
    "server_api": "Server / API",
    "sampling": "Sampling",
    "gpu_offload": "GPU / offload",
    "context_kv": "Context / KV-cache",
    "speculative": "Speculative decoding",
    "attention": "Attention",
    "debug": "Debug / logging",
    "advanced": "Advanced",
    # Catalog display names → pass through as-is
    "Model loading": "Model loading",
    "Context / KV cache": "Context / KV-cache",
    "GPU / offload": "GPU / offload",
    "Performance": "Performance",
    "Server / API": "Server / API",
    "Debug / logging": "Debug / logging",
    "Sampling": "Sampling",
    "Attention": "Attention",
    "Multimodal": "Multimodal",
    "Speculative decoding": "Speculative decoding",
    "Advanced": "Advanced",
}


def _option_label(option: LlamaOption) -> str:
    """Build a label string with default and restart metadata."""
    parts: list[str] = []
    if option.label:
        parts.append(option.label)
    if option.help_text:
        parts.append("—")
        parts.append(option.help_text)
    return " ".join(parts)


def _schema_option_label(rt_opt: RuntimeOption) -> str:
    """Build a label string for a non-curated schema option."""
    parts: list[str] = []
    if rt_opt.label:
        parts.append(rt_opt.label)
    elif rt_opt.flag:
        parts.append(rt_opt.flag)
    return " ".join(parts)


class _WrappedTabs(QWidget):
    """Tab widget whose tab bar wraps into multiple rows via FlowLayout.

    Replaces QTabWidget to avoid the single-row overflow that drives
    the container to thousands of pixels wide when scroll buttons are
    disabled.

    Provides the same interface used by the advanced-group code:
    ``addTab``, ``count``, ``widget``, ``currentIndex``,
    ``currentChanged`` signal, and ``tab_bar_height()``.
    """

    currentChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: list[QPushButton] = []
        self._pages: list[QWidget] = []
        self._current_index = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._tab_bar = QWidget(self)
        self._tab_bar.setObjectName("WrappedTabBar")
        self._tab_bar_layout = FlowLayout(self._tab_bar, hspacing=2, vspacing=0)
        outer.addWidget(self._tab_bar)

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("WrappedTabStack")
        outer.addWidget(self._stack)

    def addTab(self, page: QWidget, label: str) -> int:
        idx = len(self._pages)
        btn = QPushButton(label, self._tab_bar)
        btn.setObjectName("WrappedTabBtn")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda checked=False, i=idx: self.setCurrentIndex(i))
        self._tab_bar_layout.addWidget(btn)
        self._buttons.append(btn)
        self._pages.append(page)
        self._stack.addWidget(page)
        if idx == 0:
            self.setCurrentIndex(0)
        return idx

    def count(self) -> int:
        return len(self._pages)

    def widget(self, index: int) -> QWidget | None:
        if 0 <= index < len(self._pages):
            return self._pages[index]
        return None

    def currentIndex(self) -> int:
        return self._current_index

    def setCurrentIndex(self, index: int) -> None:
        if index == self._current_index or not (0 <= index < len(self._pages)):
            return
        self._current_index = index
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == index)
        self.currentChanged.emit(index)

    def tab_bar_height(self) -> int:
        """Return the current rendered height of the wrapped tab bar."""
        return self._tab_bar.height()


def _group_display(group: str) -> str:
    return _GROUP_DISPLAY.get(group, group.replace("_", " ").title())


def _model_combo_label(model) -> str:
    """Build ``name · quant · size · provider`` for the Run model dropdown."""
    from ..services.library_scan import infer_quant as _infer
    name = model.path.rsplit("/", 1)[-1] or model.id
    quant = model.quant or _infer(model.path) or "—"
    size_val = model.size_bytes
    if size_val is not None:
        if size_val >= 1024 ** 3:
            size = f"{size_val / (1024 ** 3):.1f} GB"
        elif size_val >= 1024 ** 2:
            size = f"{size_val / (1024 ** 2):.0f} MB"
        else:
            size = f"{size_val} B"
    else:
        size = "—"
    provider = model.hf_repo or "local"
    return f"{name} · {quant} · {size} · {provider}"


class _StopThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, controller: LlamaServerController, parent=None):
        super().__init__(parent)
        self._controller = controller

    def run(self) -> None:
        try:
            self.completed.emit(self._controller.stop())
        except Exception as exc:
            self.failed.emit(str(exc))


class RunPage(PageBase):
    inspector_changed = Signal(dict)
    def __init__(
        self,
        config_store: ConfigStore | None = None,
        library_store: LibraryStore | None = None,
        profile_store: ProfileStore | None = None,
        parent=None,
    ):
        self.config_store = config_store or ConfigStore.default()
        self.library_store = library_store or LibraryStore.default()
        self.profile_store = profile_store or ProfileStore.default()
        self.controller = LlamaServerController(on_log=None)
        self._models: list = []
        self._profiles: list = []
        self._editors: dict[str, QWidget] = {}
        self._option_cards: dict[str, OptionCard] = {}
        self._schema: RuntimeSchema | None = None
        self._schema_options_by_id: dict[str, RuntimeOption] = {}
        self._schema_cache = SchemaCache()
        self._mmproj_warning: QLabel | None = None
        super().__init__(parent)

    def build(self) -> None:
        self.setProperty(
            "subtitle",
            "Start/stop local llama-server, edit the active profile, "
            "inspect command, health, and logs.",
        )
        self._load_schema()
        self._build_runtime_header()
        config = self.config_store.load()
        self._mode_combo.setCurrentIndex(1 if config.router_mode else 0)
        self._build_main_settings()
        self._build_advanced_groups()
        # Apply mode visibility AFTER the main/advanced cards exist, otherwise
        # the hasattr() guards skip them and single-model widgets stay visible
        # on startup even when config has router_mode enabled.
        self._apply_mode_visibility()
        self._build_logs()
        self._reload_models()
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._poll_status)
        self._timer.start()

        # If llama-server is already running on the configured host:port,
        # attach to it instead of showing STOPPED.
        self._try_attach_existing()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _load_schema(self) -> None:
        """Load or build the runtime schema for the configured binary."""
        try:
            config = self.config_store.load()
            path = config.llama_server_path
        except Exception:
            path = None

        if not path:
            self._schema = None
            self._schema_options_by_id = {}
            return
        try:
            _probe, fresh = build_runtime_schema(path)
            cached = self._schema_cache.load(fresh.binary)
            self._schema = cached or fresh
            if cached is None:
                self._schema_cache.save(fresh)
        except Exception:
            self._schema = None

        self._schema_options_by_id = {
            opt.id: opt for opt in (self._schema.options if self._schema else [])
        }

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_runtime_header(self) -> None:
        hero = Card(self._body)
        self._header_card = hero
        layout = QVBoxLayout(hero)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(CardTitle("Run local llama-server", hero))
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:", hero))
        self._mode_combo = QComboBox(hero)
        self._mode_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._mode_combo.setMinimumContentsLength(12)
        self._mode_combo.addItems(["Single Model", "Router (All Models)"])
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self._mode_combo)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        row = QHBoxLayout()
        self.model_combo = QComboBox(hero)
        self.model_combo.setObjectName("ModelPicker")
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(22)
        self.model_combo.view().setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.profile_combo = QComboBox(hero)
        self.profile_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.profile_combo.setMinimumContentsLength(14)
        self.profile_combo.view().setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.profile_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        self._model_label = QLabel("Model", hero)
        row.addWidget(self._model_label)
        row.addWidget(self.model_combo, 2)
        self._profile_label = QLabel("Profile", hero)
        row.addWidget(self._profile_label)
        row.addWidget(self.profile_combo, 1)
        layout.addLayout(row)
        save = SuccessButton("Save Profile", hero); save.clicked.connect(self._save_profile)
        save_as = SecondaryButton("Save As", hero); save_as.clicked.connect(self._save_profile_as)
        duplicate = SecondaryButton("Duplicate", hero); duplicate.clicked.connect(self._duplicate_profile)
        reset = DangerButton("Reset", hero); reset.clicked.connect(self._reset_form_to_profile)
        self.preset_combo = QComboBox(hero)
        self.preset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preset_combo.setMinimumContentsLength(14)
        self.preset_combo.view().setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.preset_combo.addItems(["Preset…", *[p.name for p in PROFILE_PRESETS]])
        apply_preset = SecondaryButton("Apply Preset", hero); apply_preset.clicked.connect(self._apply_preset_from_combo)
        reset_defaults = SecondaryButton("Reset to defaults", hero); reset_defaults.clicked.connect(self._reset_to_defaults)
        start = SuccessButton("Start", hero); start.clicked.connect(self._start)
        self._stop_button = DangerButton("Stop", hero); self._stop_button.clicked.connect(self._stop)
        restart = SecondaryButton("Restart", hero); restart.clicked.connect(self._restart)
        switch = SecondaryButton("Load via API / Restart fallback", hero); switch.clicked.connect(self._switch_model)
        self._switch_button = switch

        # Wrapping action rows keep controls reachable in narrower windows.
        primary_actions_widget = QWidget(hero)
        primary_actions_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        primary_actions = FlowLayout(primary_actions_widget, hspacing=8, vspacing=8)
        for widget in (start, self._stop_button, restart, switch):
            primary_actions.addWidget(widget)

        meta_actions_widget = QWidget(hero)
        meta_actions_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._meta_actions_widget = meta_actions_widget
        meta_actions = FlowLayout(meta_actions_widget, hspacing=8, vspacing=8)
        for widget in (save, save_as, duplicate, reset, self.preset_combo, apply_preset, reset_defaults):
            meta_actions.addWidget(widget)

        layout.addWidget(primary_actions_widget)
        layout.addWidget(meta_actions_widget)

        stats = QHBoxLayout()
        self.state_tile = FieldTile("State", "stopped", hero)
        self.pid_tile = FieldTile("PID", "—", hero)
        self.endpoint_tile = FieldTile("Endpoint", "—", hero)
        self.profile_tile = FieldTile("Profile", "—", hero)
        for tile in (self.state_tile, self.pid_tile, self.endpoint_tile, self.profile_tile):
            stats.addWidget(tile)
        layout.addLayout(stats)

        # Schema info line
        if self._schema:
            schema_info = QLabel(
                f"Binary schema: {self._schema.parsed_count} options parsed "
                f"({self._schema.curated_supported_count} curated, "
                f"{self._schema.unknown_count} unknown) "
                f"from {self._schema.binary.path.rsplit('/', 1)[-1]}",
                hero,
            )
            schema_info.setObjectName("Muted")
            schema_info.setWordWrap(True)
            schema_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout.addWidget(schema_info)

        self._build_router_panel()
        layout.addWidget(self._router_panel)
        self.status = QLabel("Stopped.", hero)
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status)
        self.command = QPlainTextEdit(hero)
        self.command.setReadOnly(True)
        self.command.setMaximumHeight(100)
        self.command.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self.command)
        self._layout.addWidget(hero)

    def _build_main_settings(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for i, option_id in enumerate(MAIN_OPTION_IDS[:16]):
            catalog_opt = LLAMA_OPTION_CATALOG.get(option_id)
            if catalog_opt is None:
                continue
            # When schema is loaded, only show options the binary supports
            if self._schema and option_id not in self._schema_options_by_id:
                continue
            row = i // 2
            col = i % 2
            option_card = OptionCard(
                label=catalog_opt.label,
                flag=catalog_opt.flag,
                importance=catalog_opt.importance,
                parent=card,
            )
            option_card.setToolTip(catalog_opt.help_text)
            widget = self._make_editor(catalog_opt, card)
            self._editors[option_id] = widget
            option_card.add_editor(widget)
            grid.addWidget(option_card, row, col)
        layout.addLayout(grid)
        # Raw extra args — overflow main options + extra_args if present
        raw_extra_ids = list(MAIN_OPTION_IDS[16:])
        if "extra_args" not in raw_extra_ids and "extra_args" not in set(MAIN_OPTION_IDS):
            raw_extra_ids.append("extra_args")
        raw_extras: list[tuple[str, LlamaOption]] = []
        for option_id in raw_extra_ids:
            catalog_opt = LLAMA_OPTION_CATALOG.get(option_id)
            if catalog_opt is None:
                continue
            if self._schema and option_id not in self._schema_options_by_id:
                continue
            raw_extras.append((option_id, catalog_opt))
        if raw_extras:
            heading = QLabel("Raw extra args", card)
            heading.setObjectName("CardTitle")
            layout.addWidget(heading)
            extras_row = QHBoxLayout()
            extras_row.setSpacing(10)
            for option_id, catalog_opt in raw_extras:
                option_card = OptionCard(
                    label=catalog_opt.label,
                    flag=catalog_opt.flag,
                    importance=catalog_opt.importance,
                    parent=card,
                )
                option_card.setToolTip(catalog_opt.help_text)
                widget = self._make_editor(catalog_opt, card)
                self._editors[option_id] = widget
                option_card.add_editor(widget)
                extras_row.addWidget(option_card)
            extras_row.addStretch(1)
            layout.addLayout(extras_row)
        self._main_settings_card = card
        self._layout.addWidget(card)

    def _build_advanced_groups(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        header_row = QWidget(card)
        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(8, 2, 8, 2)
        header_layout.setSpacing(6)
        self._advanced_toggle_btn = QToolButton(header_row)
        self._advanced_toggle_btn.setText("Advanced groups")
        self._advanced_toggle_btn.setCheckable(True)
        self._advanced_toggle_btn.setChecked(True)
        self._advanced_toggle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._advanced_toggle_btn.setArrowType(Qt.DownArrow)
        self._advanced_toggle_btn.setObjectName("AdvancedToggleBtn")
        self._advanced_toggle_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._advanced_toggle_btn.setMaximumWidth(220)
        self.arg_search = QLineEdit(header_row)
        self.arg_search.setObjectName("ArgumentSearchBox")
        self.arg_search.setPlaceholderText("Search arguments…")
        self.arg_search.setClearButtonEnabled(True)
        self.arg_search.textChanged.connect(self._apply_argument_filter)
        self.arg_filter_changed = FilterPill("Only changed", header_row)
        self.arg_filter_changed.toggled.connect(self._apply_argument_filter)
        header_layout.addWidget(self._advanced_toggle_btn)
        header_layout.addWidget(self.arg_search)
        header_layout.addWidget(self.arg_filter_changed)
        # The body of the card — a wrapped-tab container whose tab bar
        # flows into multiple rows instead of scrolling horizontally.
        self._advanced_body = QWidget(card)
        body_layout = QVBoxLayout(self._advanced_body)
        body_layout.setContentsMargins(12, 0, 12, 8)
        body_layout.setSpacing(6)
        self._advanced_tabs = _WrappedTabs(self._advanced_body)
        self._advanced_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._advanced_tabs.currentChanged.connect(self._refit_advanced_panel)
        self._option_cards.clear()
        handled = set(MAIN_OPTION_IDS)
        if self._schema:
            self._build_schema_advanced(self._advanced_tabs, handled)
        else:
            self._build_catalog_advanced(self._advanced_tabs, handled)
        body_layout.addWidget(self._advanced_tabs)
        self._advanced_body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        # Wire the toggle: hide search/filter when collapsed so the header
        # stays compact (just the toggle button).
        def _toggle_advanced(checked: bool) -> None:
            self._advanced_body.setVisible(checked)
            self._advanced_toggle_btn.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
            self.arg_search.setVisible(checked)
            self.arg_filter_changed.setVisible(checked)
            self._refit_advanced_panel()
        self._advanced_toggle_btn.toggled.connect(_toggle_advanced)
        layout.addWidget(header_row)
        layout.addWidget(self._advanced_body)
        self._advanced_card = card
        self._layout.addWidget(card)
    def _refit_advanced_panel(self) -> None:
        """Resize the advanced card to fit the **active** tab's content.

        When the body is collapsed, sizes the card to just the header row.
        When expanded, sizes to the active tab page + tab bar + header.
        """

        def _do() -> None:
            try:
                if not self._advanced_tabs:
                    return
                card = self._advanced_body.parentWidget()
                if card is None:
                    return
                card_layout = card.layout()
                card_m = card_layout.contentsMargins()
                header_item = card_layout.itemAt(0) if card_layout is not None else None
                header = header_item.widget() if header_item else None
                header_layout = header.layout() if header else None
                if header_layout is not None:
                    header_layout.activate()
                header_h = header.minimumSizeHint().height() if header else 0
                if not self._advanced_body.isVisible():
                    # Collapsed: reset body constraints and size to header only.
                    self._advanced_body.setMinimumHeight(0)
                    self._advanced_body.setMaximumHeight(0)
                    card_h = header_h + card_m.top() + card_m.bottom()
                    card.setMinimumHeight(card_h)
                    card.setMaximumHeight(card_h)
                    return

                active_index = self._advanced_tabs.currentIndex()
                if active_index < 0:
                    return
                page = self._advanced_tabs.widget(active_index)
                if page is None:
                    return

                # Ensure the page layout has settled at its current width.
                content = page.widget() if isinstance(page, QScrollArea) else page
                inner_layout = content.layout()
                if inner_layout is not None:
                    inner_layout.activate()

                # Height the active page needs, capped so overflow scrolls.
                h = content.minimumSizeHint().height()
                if h < 80:
                    h = 80
                if h > 400:
                    h = 400

                # Set every page: active gets its calculated height,
                # inactive are squashed to 0 so they don't affect sizing.
                for i in range(self._advanced_tabs.count()):
                    p = self._advanced_tabs.widget(i)
                    if p is None:
                        continue
                    if i == active_index:
                        p.setMinimumHeight(h)
                        p.setMaximumHeight(h)
                    else:
                        p.setMinimumHeight(0)
                        p.setMaximumHeight(0)

                # WrappedTabs height = tab bar + active page.
                tab_bar_h = self._advanced_tabs.tab_bar_height()
                tabs_h = tab_bar_h + h

                # Body height = tabs widget + body layout margins.
                body_layout = self._advanced_body.layout()
                body_m = body_layout.contentsMargins()
                body_h = tabs_h + body_m.top() + body_m.bottom()
                self._advanced_body.setMinimumHeight(body_h)
                self._advanced_body.setMaximumHeight(body_h)

                # Card height = header + body + spacing + card margins.
                card_h = (
                    body_h
                    + header_h
                    + card_layout.spacing()
                    + card_m.top()
                    + card_m.bottom()
                )
                card.setMinimumHeight(card_h)
                card.setMaximumHeight(card_h)
            except Exception:
                return

        # Defer the recompute: currentChanged fires BEFORE the new page's
        # layout has been recomputed.  QTimer.singleShot(0) schedules the
        # call for after the current event.
        QTimer.singleShot(0, _do)

    def _build_schema_advanced(self, tabs: _WrappedTabs, handled: set[str]) -> None:
        """Build advanced groups from the parsed runtime schema."""
        groups: dict[str, list[RuntimeOption]] = {}
        for rt_opt in self._schema.options:
            if rt_opt.id in handled:
                continue
            groups.setdefault(rt_opt.group, []).append(rt_opt)

        # Preserve catalog group order, then append any extra groups
        group_order = list(LLAMA_OPTION_CATALOG.groups_in_order())
        for g in groups:
            if g not in group_order:
                group_order.append(g)

        extra = LLAMA_OPTION_CATALOG.get("extra_args")
        if extra is not None and "extra_args" not in handled:
            tab_page = QWidget(tabs)
            grid = QGridLayout(tab_page)
            grid.setContentsMargins(8, 8, 8, 8)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(8)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            tab_page.setMinimumHeight(0)
            tab_page.setMaximumHeight(16777215)
            tab_page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            option_card = OptionCard(
                label=extra.label,
                flag=extra.flag,
                importance=extra.importance,
                parent=tab_page,
            )
            option_card.setToolTip(extra.help_text)
            widget = self._make_editor(extra, tab_page)
            self._editors[extra.id] = widget
            option_card.add_editor(widget)
            self._option_cards[extra.id] = option_card
            grid.addWidget(option_card, 0, 0)
            scroll = QScrollArea(tabs)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setWidget(tab_page)
            tabs.addTab(scroll, "Raw extra args")

        for group_name in group_order:
            options = groups.get(group_name, [])
            if not options:
                continue
            tab_page = QWidget(tabs)
            grid = QGridLayout(tab_page)
            grid.setContentsMargins(8, 8, 8, 8)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(8)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            tab_page.setMinimumHeight(0)
            tab_page.setMaximumHeight(16777215)
            tab_page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            for idx, rt_opt in enumerate(options):
                catalog_opt = LLAMA_OPTION_CATALOG.get(rt_opt.id)
                if catalog_opt is not None:
                    option_card = OptionCard(
                        label=catalog_opt.label,
                        flag=catalog_opt.flag,
                        importance=catalog_opt.importance,
                        parent=tab_page,
                    )
                    option_card.setToolTip(catalog_opt.help_text)
                    widget = self._make_editor(catalog_opt, tab_page)
                else:
                    option_card = OptionCard(
                        label=rt_opt.label or rt_opt.flag,
                        flag=rt_opt.flag,
                        importance=0,
                        parent=tab_page,
                    )
                    option_card.setToolTip(rt_opt.description)
                    widget = self._make_schema_editor(rt_opt, tab_page)
                self._editors[rt_opt.id] = widget
                if rt_opt.id == "mmproj":
                    option_card.add_editor(self._wrap_mmproj_editor(widget, tab_page))
                else:
                    option_card.add_editor(widget)
                self._option_cards[rt_opt.id] = option_card
                row, col = divmod(idx, 2)
                grid.addWidget(option_card, row, col)
            scroll = QScrollArea(tabs)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setWidget(tab_page)
            tabs.addTab(scroll, _group_display(group_name))

    def _build_catalog_advanced(self, tabs: _WrappedTabs, handled: set[str]) -> None:
        """Build advanced groups from the static catalog (fallback)."""
        for group in LLAMA_OPTION_CATALOG.groups_in_order():
            options = [o for o in LLAMA_OPTION_CATALOG.by_group(group) if o.id not in handled]
            if not options:
                continue
            tab_page = QWidget(tabs)
            grid = QGridLayout(tab_page)
            grid.setContentsMargins(8, 8, 8, 8)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(8)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            tab_page.setMinimumHeight(0)
            tab_page.setMaximumHeight(16777215)
            tab_page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            for idx, option in enumerate(options):
                option_card = OptionCard(
                    label=option.label,
                    flag=option.flag,
                    importance=option.importance,
                    parent=tab_page,
                )
                option_card.setToolTip(option.help_text)
                widget = self._make_editor(option, tab_page)
                self._editors[option.id] = widget
                if option.id == "mmproj":
                    option_card.add_editor(self._wrap_mmproj_editor(widget, tab_page))
                else:
                    option_card.add_editor(widget)
                self._option_cards[option.id] = option_card
                row, col = divmod(idx, 2)
                grid.addWidget(option_card, row, col)
            scroll = QScrollArea(tabs)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setWidget(tab_page)
            tabs.addTab(scroll, _group_display(group))

    def _build_logs(self) -> None:
        logs = Card(self._body)
        logs_layout = QVBoxLayout(logs)
        logs_layout.setContentsMargins(16, 14, 16, 14)
        logs_layout.setSpacing(8)
        logs_layout.addWidget(CardTitle("Server logs", logs))

        filter_row = QHBoxLayout()
        self.log_search = QLineEdit(logs)
        self.log_search.setPlaceholderText("Search logs…")
        self.log_search.textChanged.connect(self._render_logs)
        self.log_source = QComboBox(logs)
        self.log_source.addItems(["all", "stdout", "stderr"])
        self.log_source.currentIndexChanged.connect(self._render_logs)
        copy_btn = SecondaryButton("Copy", logs)
        copy_btn.clicked.connect(self._copy_logs)
        clear = SecondaryButton("Clear", logs)
        clear.clicked.connect(self._clear_logs)
        filter_row.addWidget(self.log_search, 1)
        filter_row.addWidget(self.log_source)
        filter_row.addWidget(copy_btn)
        filter_row.addWidget(clear)
        logs_layout.addLayout(filter_row)

        self.logs = QPlainTextEdit(logs)
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(10000)
        self.logs.setMaximumHeight(260)
        self.logs.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        logs_layout.addWidget(self.logs)
        self._layout.addWidget(logs)

    # ------------------------------------------------------------------
    # Mode / router panel
    # ------------------------------------------------------------------

    def _on_mode_changed(self, index: int) -> None:
        is_router = index == 1
        config = self.config_store.load()
        config.router_mode = is_router
        self.config_store.save(config)
        self._apply_mode_visibility()
        self._update_command_preview()

    def _apply_mode_visibility(self) -> None:
        config = self.config_store.load()
        is_router = config.router_mode
        self._model_label.setVisible(not is_router)
        self.model_combo.setVisible(not is_router)
        self._profile_label.setVisible(not is_router)
        self.profile_combo.setVisible(not is_router)
        self._meta_actions_widget.setVisible(not is_router)
        self._switch_button.setVisible(not is_router)
        if hasattr(self, "_main_settings_card"):
            card = self._main_settings_card
            card.setVisible(not is_router)
            if is_router:
                card.setMinimumHeight(0)
                card.setMaximumHeight(16777215)
        if hasattr(self, "_advanced_card"):
            card = self._advanced_card
            card.setVisible(not is_router)
            if is_router:
                # Remove fixed-height constraints from _refit_advanced_panel.
                card.setMinimumHeight(0)
                card.setMaximumHeight(16777215)
                self._advanced_body.setMinimumHeight(0)
                self._advanced_body.setMaximumHeight(16777215)
            else:
                # Reset tab page constraints so minimumSizeHint is correct.
                for i in range(self._advanced_tabs.count()):
                    p = self._advanced_tabs.widget(i)
                    if p is not None:
                        p.setMinimumHeight(0)
                        p.setMaximumHeight(16777215)
                self._refit_advanced_panel()
        if hasattr(self, "_router_panel"):
            self._router_panel.setVisible(is_router)

    def _build_router_panel(self) -> None:
        config = self.config_store.load()
        self._router_panel = QWidget(self._header_card)
        layout = QVBoxLayout(self._router_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        dir_label = QLabel(f"Models directory: {config.models_dir or '—'}", self._router_panel)
        dir_label.setObjectName("Muted")
        dir_label.setWordWrap(True)
        dir_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(dir_label)

        max_row = QHBoxLayout()
        max_row.addWidget(QLabel("Max loaded models:", self._router_panel))
        self._models_max_spin = QSpinBox(self._router_panel)
        self._models_max_spin.setRange(1, 32)
        self._models_max_spin.setValue(1)
        self._models_max_spin.valueChanged.connect(self._update_command_preview)
        max_row.addWidget(self._models_max_spin)
        max_row.addStretch(1)
        layout.addLayout(max_row)

        layout.addWidget(QLabel("Loaded Models:", self._router_panel))
        self._loaded_models_container = QWidget(self._router_panel)
        self._loaded_models_container.setLayout(QVBoxLayout())
        self._loaded_models_container.layout().setContentsMargins(0, 0, 0, 0)
        self._loaded_models_container.layout().setSpacing(4)
        layout.addWidget(self._loaded_models_container)
        layout.addStretch(1)

        self._router_panel.setVisible(False)

    def _poll_router_models(self) -> None:
        try:
            host, port = self._effective_host_port()
            client = LlamaServerApiClient(host, port)
            models = client.list_loaded_models()
            container = self._loaded_models_container
            panel_layout = container.layout()
            # Clear previous rows.
            while panel_layout.count():
                item = panel_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            if not models:
                placeholder = QLabel("No models discovered.", container)
                placeholder.setObjectName("Muted")
                panel_layout.addWidget(placeholder)
                return
            for model in models:
                model_id = model.get("id", "unknown")
                # The /models endpoint may return "size" or other fields
                # but the key status/state field varies by llama-server version.
                # Try common keys: "status", "state", or infer from "object".
                status = model.get("status") or model.get("state", "unknown")
                row = QHBoxLayout()
                name_label = ElidedLabel(
                    f"{model_id}  ({status})",
                    container,
                )
                name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                row.addWidget(name_label, 1)
                if status in ("loaded", "loading"):
                    unload_btn = SecondaryButton("Unload", container)
                    unload_btn.clicked.connect(
                        lambda _checked=False, mid=model_id: self._unload_model(mid)
                    )
                    row.addWidget(unload_btn)
                row_widget = QWidget(container)
                row_widget.setLayout(row)
                panel_layout.addWidget(row_widget)
        except Exception:
            pass

    def _unload_model(self, model_name: str) -> None:
        try:
            host, port = self._effective_host_port()
            client = LlamaServerApiClient(host, port)
            client.unload_model(model_name)
            self._poll_router_models()
        except Exception as exc:
            self.status.setText(f"Unload failed: {exc}")

    # ------------------------------------------------------------------
    # Editor factories
    # ------------------------------------------------------------------

    def _make_editor(self, option: LlamaOption, parent: QWidget) -> QWidget:
        """Create a typed editor for a curated catalog option."""
        default = option.default.to_json() if option.default else None
        if option.kind is OptionKind.BOOLEAN:
            w = QCheckBox(parent)
            w.setChecked(bool(default))
            w.toggled.connect(self._on_editor_changed)
            return w
        if option.kind is OptionKind.INTEGER:
            if option.min_value is not None and option.max_value is not None:
                w = SliderSpinBox(int(option.min_value), int(option.max_value), parent)
            else:
                w = QSpinBox(parent)
                w.setRange(0, 1_000_000)
            if option.step is not None:
                w.setSingleStep(int(option.step))
            if default is not None:
                try:
                    w.setValue(int(default))
                except (ValueError, TypeError):
                    pass
            w.setMinimumWidth(180)
            w.valueChanged.connect(self._on_editor_changed)
            return w
        if option.kind is OptionKind.FLOAT:
            if option.min_value is not None and option.max_value is not None:
                w = SliderDoubleSpinBox(float(option.min_value), float(option.max_value), parent=parent)
            else:
                w = QDoubleSpinBox(parent)
                w.setDecimals(3)
                w.setRange(0.0, 1000.0)
            if option.step is not None:
                w.setSingleStep(float(option.step))
            if default is not None:
                try:
                    w.setValue(float(default))
                except (ValueError, TypeError):
                    pass
            w.setMinimumWidth(180)
            w.valueChanged.connect(self._on_editor_changed)
            return w
        # STRING / STRING_LIST — use QComboBox if enum_values are defined.
        if option.enum_values:
            w = QComboBox(parent)
            w.addItem("(unset)", None)
            for value, label in option.enum_values:
                w.addItem(label, value)
            # Set default if present.
            if default is not None:
                idx = w.findData(default)
                if idx >= 0:
                    w.setCurrentIndex(idx)
            w.currentIndexChanged.connect(self._on_editor_changed)
            return w
        w = QLineEdit(parent)
        w.setText(str(default or ""))
        # Most text fields are short (numbers, enums), but a few are
        # file paths (mmproj, chat-template) that can exceed 64
        # characters easily. The legacy 64-char cap was too tight for
        # real-world paths.
        w.setMaxLength(1024)
        w.textChanged.connect(self._on_editor_changed)
        w.setMinimumWidth(120)
        return w

    def _make_schema_editor(self, rt_opt: RuntimeOption, parent: QWidget) -> QWidget:
        """Create a typed editor for a non-curated schema option.

        For QLineEdit, the default value goes in the placeholder so the
        editor starts empty — that way ``_settings_from_form`` can tell
        "user has not touched this field" (empty text → no flag) apart
        from "user has typed the default" (non-empty text → emit flag).
        For numeric/boolean editors, the editor cannot be empty, so the
        default is the initial value and a flag/value pair will always
        be emitted by ``_settings_from_form``; that pair is filtered at
        build time by ``clean_raw_args``.
        """
        default = rt_opt.default
        if rt_opt.kind == "boolean":
            w = QCheckBox(parent)
            if default is not None:
                w.setChecked(default.lower() in ("true", "1", "yes"))
            w.toggled.connect(self._on_editor_changed)
            return w
        if rt_opt.kind == "integer":
            w = SliderSpinBox(-1_000_000, 1_000_000, parent)
            w.setMinimumWidth(180)
            if default is not None:
                try:
                    w.setValue(int(default))
                except (ValueError, TypeError):
                    pass
            w.valueChanged.connect(self._on_editor_changed)
            return w
        if rt_opt.kind == "float":
            w = SliderDoubleSpinBox(-1000.0, 1000.0, parent=parent)
            w.setMinimumWidth(180)
            if default is not None:
                try:
                    w.setValue(float(default))
                except (ValueError, TypeError):
                    pass
            w.valueChanged.connect(self._on_editor_changed)
            return w
        # STRING with enum_values → dropdown. (We pass None as the
        # default so the form starts in the "unset" state.)
        if rt_opt.kind == "string" and getattr(rt_opt, "enum_values", None):
            w = QComboBox(parent)
            w.addItem("(unset)", None)
            for value, label in rt_opt.enum_values:
                w.addItem(label, value)
            w.setMinimumWidth(120)
            w.currentIndexChanged.connect(self._on_editor_changed)
            return w
        w = QLineEdit(parent)
        w.setMaxLength(1024)
        w.setMinimumWidth(120)
        if default is not None:
            w.setPlaceholderText(str(default))
        w.textChanged.connect(self._on_editor_changed)
        return w

    # ------------------------------------------------------------------
    # Editor read / write
    # ------------------------------------------------------------------

    def _editor_value(self, option: LlamaOption, widget: QWidget):
        if option.kind is OptionKind.BOOLEAN:
            return widget.isChecked()
        if option.kind is OptionKind.INTEGER:
            return int(widget.value())
        if option.kind is OptionKind.FLOAT:
            return float(widget.value())
        if option.kind is OptionKind.STRING_LIST:
            if isinstance(widget, QComboBox):
                return widget.currentData()
            text = widget.text().strip()
            return [part for part in text.split() if part]
        # STRING: enum combobox or free-form line edit.
        if isinstance(widget, QComboBox):
            return widget.currentData()
        return widget.text().strip() or None

    def _schema_editor_value(self, rt_opt: RuntimeOption, widget: QWidget):
        if rt_opt.kind == "boolean":
            return widget.isChecked()
        if rt_opt.kind == "integer":
            return int(widget.value())
        if rt_opt.kind == "float":
            return float(widget.value())
        if isinstance(widget, QComboBox):
            return widget.currentData()
        return widget.text().strip() or None

    def _set_editor_value(self, option: LlamaOption, widget: QWidget, value) -> None:
        if option.kind is OptionKind.BOOLEAN:
            widget.setChecked(bool(value))
        elif option.kind is OptionKind.INTEGER:
            widget.setValue(int(value or 0))
        elif option.kind is OptionKind.FLOAT:
            widget.setValue(float(value or 0.0))
        elif option.kind is OptionKind.STRING_LIST:
            # Render a list of strings as space-separated, so the
            # editor shows ``--api-prefix /v1`` rather than the
            # Python repr ``['--api-prefix', '/v1']``. ``_editor_value``
            # reverses this on read.
            if isinstance(widget, QLineEdit):
                if isinstance(value, (list, tuple)):
                    widget.setText(" ".join(str(v) for v in value))
                else:
                    widget.setText(str(value or ""))
                return
        if isinstance(widget, QComboBox):
            if value is None:
                widget.setCurrentIndex(0)
            else:
                idx = widget.findData(value)
                widget.setCurrentIndex(idx if idx >= 0 else 0)
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value or ""))

    def _load_unknown_editor(
        self, option_id: str, widget: QWidget, profile: ModelProfile | None,
    ) -> None:
        """Restore an unknown-option editor from profile raw_args."""
        rt_opt = self._schema_options_by_id.get(option_id)
        if rt_opt is None or profile is None:
            return

        flag = rt_opt.flag
        args = list(profile.raw_args)
        for i, arg in enumerate(args):
            if arg != flag:
                continue
            if rt_opt.kind == "boolean":
                if isinstance(widget, QCheckBox):
                    widget.setChecked(True)
            elif i + 1 < len(args):
                val = args[i + 1]
                if rt_opt.kind == "integer" and isinstance(widget, QSpinBox):
                    try:
                        widget.setValue(int(val))
                    except (ValueError, TypeError):
                        pass
                elif rt_opt.kind == "float" and isinstance(widget, QDoubleSpinBox):
                    try:
                        widget.setValue(float(val))
                    except (ValueError, TypeError):
                        pass
                elif isinstance(widget, QLineEdit):
                    widget.setText(val)
            return  # found the flag, done
        else:
            # Not in raw_args — restore schema default
            if rt_opt.default is not None:
                self._set_schema_editor_default(rt_opt, widget)

    def _set_schema_editor_default(self, rt_opt: RuntimeOption, widget: QWidget) -> None:
        default = rt_opt.default
        if default is None:
            return
        if rt_opt.kind == "boolean" and isinstance(widget, QCheckBox):
            widget.setChecked(default.lower() in ("true", "1", "yes"))
        elif rt_opt.kind == "integer" and isinstance(widget, QSpinBox):
            try:
                widget.setValue(int(default))
            except (ValueError, TypeError):
                pass
        elif rt_opt.kind == "float" and isinstance(widget, QDoubleSpinBox):
            try:
                widget.setValue(float(default))
            except (ValueError, TypeError):
                pass
        elif isinstance(widget, QLineEdit):
            # Keep the text empty so the form state reflects "user has
            # not set this". The placeholder already shows the default.
            widget.clear()

    def _wrap_mmproj_editor(self, editor: QWidget, parent: QWidget) -> QWidget:
        """Wrap the mmproj QLineEdit with an inline file-not-found warning label."""
        wrapper = QWidget(parent)
        vbox = QVBoxLayout(wrapper)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(2)
        vbox.addWidget(editor)
        self._mmproj_warning = QLabel(wrapper)
        self._mmproj_warning.setStyleSheet("color: #e74c3c; font-size: 11px;")
        self._mmproj_warning.hide()
        vbox.addWidget(self._mmproj_warning)
        editor.textChanged.connect(self._validate_mmproj)
        return wrapper

    def _on_editor_changed(self) -> None:
        """Slot connected to every editor signal: refresh preview, dots, and filter."""
        self._update_command_preview()
        self._refresh_option_cards()

    def _refresh_option_cards(self) -> None:
        """Recompute user_set and update red dots + visibility filter."""
        _, _, user_set = self._settings_from_form()
        for option_id, card in self._option_cards.items():
            card.set_changed(option_id in user_set)
        self._apply_argument_filter()

    def _apply_argument_filter(self) -> None:
        """Show/hide option cards based on search text and 'Only changed' pill."""
        search_text = self.arg_search.text().strip().lower()
        only_changed = self.arg_filter_changed.isChecked()
        _, _, user_set = self._settings_from_form()

        for option_id, card in self._option_cards.items():
            visible = True

            if only_changed and option_id not in user_set:
                visible = False

            if visible and search_text:
                catalog_opt = LLAMA_OPTION_CATALOG.get(option_id)
                if catalog_opt is not None:
                    searchable = " ".join(
                        [
                            catalog_opt.label.lower(),
                            catalog_opt.flag.lower(),
                            _group_display(catalog_opt.group).lower(),
                            (catalog_opt.help_text or "").lower(),
                        ]
                    )
                else:
                    rt_opt = self._schema_options_by_id.get(option_id)
                    if rt_opt is not None:
                        searchable = " ".join(
                            [
                                (rt_opt.label or "").lower(),
                                rt_opt.flag.lower(),
                                _group_display(rt_opt.group).lower(),
                                (rt_opt.description or "").lower(),
                            ]
                        )
                    else:
                        searchable = option_id.lower()

                if search_text not in searchable:
                    visible = False

            card.setVisible(visible)

    # ------------------------------------------------------------------
    # Settings collection
    # ------------------------------------------------------------------

    def _settings_from_form(self) -> tuple[SettingValueMap, list[str], set[str]]:
        """Collect all editor values into curated settings + raw_args + user_set."""
        settings = SettingValueMap()
        raw_args: list[str] = []
        user_set: set[str] = set()

        for option_id, widget in self._editors.items():
            catalog_opt = LLAMA_OPTION_CATALOG.get(option_id)
            if catalog_opt is not None:
                value = self._editor_value(catalog_opt, widget)
                settings = settings.with_value(catalog_opt, value)
                # Track which curated options differ from catalog default
                if catalog_opt.default is not None:
                    if value != catalog_opt.default.value:
                        user_set.add(option_id)
                elif value is not None:
                    user_set.add(option_id)
                # Unknown schema option → serialize to raw_args.
                # Empty QLineEdit text means "user has not set this";
                # the build-time clean_raw_args will drop the resulting
                # flag/value pair if it matches a natural default.
                rt_opt = self._schema_options_by_id.get(option_id)
                if rt_opt is None:
                    continue
                value = self._schema_editor_value(rt_opt, widget)
                if rt_opt.kind == "boolean":
                    default_on = (
                        rt_opt.default is not None
                        and rt_opt.default.lower() in ("true", "1", "yes")
                    )
                    if value != default_on:
                        if value:
                            raw_args.append(rt_opt.flag)
                elif rt_opt.kind in ("integer", "float"):
                    if rt_opt.default is None or str(value) != rt_opt.default:
                        raw_args.extend([rt_opt.flag, str(value)])
                else:
                    if value is not None and value != "":
                        raw_args.extend([rt_opt.flag, str(value)])

        return settings, clean_raw_args(raw_args), user_set

    # ------------------------------------------------------------------
    # Selection state
    # ------------------------------------------------------------------

    def _selected_model(self):
        idx = self.model_combo.currentIndex()
        return self._models[idx] if 0 <= idx < len(self._models) else None

    def _selected_profile(self):
        idx = self.profile_combo.currentIndex()
        return self._profiles[idx] if 0 <= idx < len(self._profiles) else None

    def _on_model_changed(self) -> None:
        self._persist_selection()
        self._reload_profiles()
        self._auto_populate_mmproj()

    def _auto_populate_mmproj(self) -> None:
        model = self._selected_model()
        editor = self._editors.get("mmproj")
        if editor is None:
            return
        if model and model.mmproj_path:
            editor.setText(model.mmproj_path)
            profile = self._selected_profile()
            if profile is not None:
                profile.user_set.add("mmproj")
                self.profile_store.upsert(profile)
            self.status.setText(f"Auto-detected mmproj: {Path(model.mmproj_path).name}")
        else:
            editor.clear()
            profile = self._selected_profile()
            if profile is not None and "mmproj" in profile.user_set:
                profile.user_set.discard("mmproj")
                self.profile_store.upsert(profile)
        self._validate_mmproj()
        self._update_command_preview()

    def _validate_mmproj(self) -> None:
        editor = self._editors.get("mmproj")
        if editor is None or getattr(self, "_mmproj_warning", None) is None:
            return
        path_str = editor.text().strip()
        if path_str and not Path(path_str).exists():
            self._mmproj_warning.setText("File not found")
            self._mmproj_warning.show()
        else:
            self._mmproj_warning.hide()

    def _on_profile_changed(self) -> None:
        self._persist_selection()
        self._load_profile_into_form()

    def _persist_selection(self) -> None:
        """Save current model/profile selection to config."""
        try:
            config = self.config_store.load()
        except Exception:
            from llama_data.models import AppConfig
            config = AppConfig()
        model = self._selected_model()
        profile = self._selected_profile()
        config.selected_model_id = model.id if model else None
        config.selected_profile_id = profile.id if profile else None
        self.config_store.save(config)

    # ------------------------------------------------------------------
    # Model / profile reload
    # ------------------------------------------------------------------

    def _reload_models(self) -> None:
        from ..services.library_scan import is_companion_gguf, scan_models_dir
        try:
            scan_models_dir(self.config_store, self.library_store)
        except Exception:
            # Fall back to whatever metadata is already persisted.
            pass
        self._models = sorted(
            (m for m in self.library_store.load() if not is_companion_gguf(Path(m.path))),
            key=lambda m: m.path.casefold(),
        )
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model in self._models:
            self.model_combo.addItem(_model_combo_label(model), model.id)

        # Restore selection from config
        try:
            config = self.config_store.load()
            if config.selected_model_id:
                for i, m in enumerate(self._models):
                    if m.id == config.selected_model_id:
                        self.model_combo.setCurrentIndex(i)
                        break
        except Exception:
            pass
        self.model_combo.blockSignals(False)
        self._reload_profiles()

    def _reload_profiles(self) -> None:
        model = self._selected_model()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self._profiles = self.profile_store.list_for_model(model.id) if model else []
        self._profiles.sort(key=lambda p: (not p.is_default, p.name.lower()))
        for profile in self._profiles:
            self.profile_combo.addItem(
                profile.name + (" \u2605" if profile.is_default else ""),
                profile.id,
            )
        chosen_index = 0
        try:
            config = self.config_store.load()
            if config.selected_profile_id:
                for i, p in enumerate(self._profiles):
                    if p.id == config.selected_profile_id:
                        chosen_index = i
                        break
            else:
                for i, p in enumerate(self._profiles):
                    if p.is_default:
                        chosen_index = i
                        break
        except Exception:
            pass
        if self._profiles:
            self.profile_combo.setCurrentIndex(chosen_index)
        self.profile_combo.blockSignals(False)
        self._load_profile_into_form()

    def _load_profile_into_form(self) -> None:
        profile = self._selected_profile()
        config = self.config_store.load()
        for option_id, widget in self._editors.items():
            catalog_opt = LLAMA_OPTION_CATALOG.get(option_id)
            if catalog_opt is not None:
                # Priority: profile override > Settings default > catalog default
                if profile and profile.settings.get(option_id):
                    value = profile.settings.get(option_id).to_json()
                elif option_id == "host":
                    value = config.host
                elif option_id == "port":
                    value = config.port
                elif config.global_settings.get(option_id):
                    value = config.global_settings.get(option_id).to_json()
                elif catalog_opt.default:
                    value = catalog_opt.default.to_json()
                else:
                    value = None
                self._set_editor_value(catalog_opt, widget, value)
            else:
                self._load_unknown_editor(option_id, widget, profile)
        self._update_command_preview()
        self._refresh_option_cards()

    def _effective_host_port(self) -> tuple[str, int]:
        config = self.config_store.load()
        return config.host, config.port

    # ------------------------------------------------------------------
    def _save_profile(self) -> None:
        model = self._selected_model()
        if not model:
            self.status.setText("Select a model first.")
            return
        profile = self._selected_profile()
        settings, raw_args, user_set = self._settings_from_form()
        if profile is not None:
            if profile.model_id != model.id:
                self.status.setText("Profile belongs to a different model. Use Save As.")
                self._save_profile_as()
                return
            profile.settings = settings
            profile.raw_args = raw_args
            profile.user_set = user_set
            self.profile_store.upsert(profile)
            self.status.setText(f"Saved {profile.name}.")
        else:
            profile = ModelProfile(
                id=str(uuid.uuid4()),
                model_id=model.id,
                name="Default",
                is_default=True,
                settings=settings,
                raw_args=raw_args,
                user_set=user_set,
            )
            self.profile_store.upsert(profile)
            self._reload_profiles()
            self.status.setText(f"Saved Default profile for {Path(model.path).stem}.")
        cfg = self.config_store.load()
        cfg.selected_profile_id = profile.id
        self.config_store.save(cfg)
        self._update_command_preview()

    def _suggest_profile_name(self, model) -> str:
        base = os.path.splitext(os.path.basename(model.path))[0]
        parts = base.rsplit("-", 1)
        if len(parts) == 2 and "_" in parts[1]:
            return f"{parts[0]} ({parts[1]})"
        return base

    def _save_profile_as(self) -> None:
        model = self._selected_model()
        if not model:
            self.status.setText("Select a model first.")
            return
        current = self._selected_profile()
        suggestion = (
            f"{current.name} (copy)"
            if current is not None
            else self._suggest_profile_name(model)
        )
        name, ok = QInputDialog.getText(
            self, "Save profile as", "Profile name:", text=suggestion
        )
        if not ok:
            self.status.setText("Save As cancelled.")
            return
        name = name.strip()
        if not name:
            self.status.setText("Profile name cannot be empty.")
            return
        existing_for_model = self.profile_store.list_for_model(model.id)
        match = next((p for p in existing_for_model if p.name == name), None)
        if match is not None:
            reply = QMessageBox.question(
                self,
                "Overwrite profile?",
                f"A profile named '{name}' already exists for this model. Overwrite?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self.status.setText("Save As cancelled.")
                return
        self._save_profile_as_with_name(name, force_overwrite=True)

    def _save_profile_as_with_name(self, name: str, force_overwrite: bool = True) -> None:
        model = self._selected_model()
        if not model:
            self.status.setText("Select a model first.")
            return
        existing = self.profile_store.list_for_model(model.id)
        match = next((p for p in existing if p.name == name), None)
        if match is not None and not force_overwrite:
            reply = QMessageBox.question(
                self,
                "Overwrite?",
                f"A profile named '{name}' already exists. Overwrite?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                self.status.setText("Save As cancelled.")
                return
        settings, raw_args, user_set = self._settings_from_form()
        if match is not None:
            profile = match
            profile.settings = settings
            profile.raw_args = raw_args
            profile.user_set = user_set
        else:
            profile = ModelProfile(
                id=str(uuid.uuid4()),
                model_id=model.id,
                name=name,
                settings=settings,
                raw_args=raw_args,
                user_set=user_set,
            )
        self.profile_store.upsert(profile)
        cfg = self.config_store.load()
        cfg.selected_profile_id = profile.id
        self.config_store.save(cfg)
        self._reload_profiles()
        # Select the newly-saved profile
        for i, p in enumerate(self._profiles):
            if p.id == profile.id:
                self.profile_combo.setCurrentIndex(i)
                break
        self.status.setText(f"Profile saved as '{name}'.")
        self._update_command_preview()

    def _duplicate_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            self._save_profile_as()
            return
        dup = ModelProfile(
            id=str(uuid.uuid4()),
            model_id=profile.model_id,
            name=f"{profile.name} copy",
            settings=profile.settings.copy(),
            raw_args=list(profile.raw_args),
            preset_origin=profile.preset_origin,
            schema_version=profile.schema_version,
            user_set=set(profile.user_set),
        )
        self.profile_store.upsert(dup)
        cfg = self.config_store.load()
        cfg.selected_profile_id = dup.id
        self.config_store.save(cfg)
        self._reload_profiles()
        self.status.setText("Profile duplicated.")

    def _reset_form_to_profile(self) -> None:
        self._load_profile_into_form()
        self.status.setText("Run editor reset to saved/default values.")

    def _reset_to_defaults(self) -> None:
        """Reset all editors to catalog defaults and clear user_set."""
        for option_id, widget in self._editors.items():
            catalog_opt = LLAMA_OPTION_CATALOG.get(option_id)
            if catalog_opt is not None:
                default_val = catalog_opt.default.to_json() if catalog_opt.default else None
                self._set_editor_value(catalog_opt, widget, default_val)
            else:
                rt_opt = self._schema_options_by_id.get(option_id)
                if rt_opt and rt_opt.default is not None:
                    self._set_schema_editor_default(rt_opt, widget)
        profile = self._selected_profile()
        if profile is not None:
            profile.user_set = set()
            self.profile_store.upsert(profile)
        self.status.setText("All options reset to catalog defaults.")
        self._update_command_preview()
        self._refresh_option_cards()

    def _apply_preset_from_combo(self) -> None:
        name = self.preset_combo.currentText()
        preset = next((p for p in PROFILE_PRESETS if p.name == name), None)
        if preset is None:
            self.status.setText("Choose a preset first.")
            return
        from llama_data.llama_options import apply_preset_to_settings
        settings = apply_preset_to_settings(preset)
        for option_id, widget in self._editors.items():
            option = LLAMA_OPTION_CATALOG.get(option_id)
            if option is None:
                continue
            value = settings.get(option_id)
            if value is not None:
                self._set_editor_value(option, widget, value.to_json())
        self.status.setText(f"Applied preset: {name}.")
        self._update_command_preview()
        self._refresh_option_cards()
    # ------------------------------------------------------------------
    # Argv / command preview
    # ------------------------------------------------------------------

    def _argv(self) -> list[str]:
        config = self.config_store.load()
        model = self._selected_model()
        profile = self._selected_profile()
        if model is None and not config.router_mode:
            raise RuntimeError("No model selected. Add a model in Library first.")
        # Router mode: just serve the directory, no model/profile needed.
        if config.router_mode:
            preset_path: str | None = None
            if config.models_dir:
                all_models = self.library_store.load()
                all_profiles = self.profile_store.load()
                defaults = {
                    p.model_id: p
                    for p in all_profiles
                    if p.is_default
                }
                preset_path = generate_models_preset(
                    all_models, defaults, config.models_dir,
                )
            models_max = getattr(self, "_models_max_spin", None)
            models_max_val = models_max.value() if models_max else 0
            return build_argv(
                config, model or LocalModel(id="", path=""),
                models_preset_path=preset_path,
                models_max=models_max_val,
            )
        # IMPORTANT: do not mutate ``profile`` here. This method is
        # called from ``_set_editor_value`` during ``_load_profile_into_form``
        # (and from any editor's ``textChanged``). Mutating the profile
        # overwrites earlier editors' values with the form's current
        # (still-being-populated) state, dropping fields that come
        # later in the iteration. Read the form into a fresh local
        # model and pass that to ``build_argv``; the profile is
        # only updated by the explicit Save action.
        if profile is not None:
            settings, raw_args, user_set = self._settings_from_form()
            preview_profile = ModelProfile(
                id=profile.id,
                model_id=profile.model_id,
                name=profile.name,
                settings=settings,
                raw_args=raw_args,
                user_set=user_set,
                preset_origin=profile.preset_origin,
                schema_version=profile.schema_version,
                is_default=profile.is_default,
                last_used_at=profile.last_used_at,
                created_at=profile.created_at,
                updated_at=profile.updated_at,
            )
        else:
            settings, raw_args, user_set = self._settings_from_form()
            preview_profile = ModelProfile(
                id="__ephemeral__",
                model_id=model.id,
                name="Unsaved",
                settings=settings,
                raw_args=raw_args,
                user_set=user_set,
            )
        return build_argv(config, model, preview_profile)

    def _update_command_preview(self) -> None:
        try:
            self.command.setPlainText(" ".join(self._argv()))
        except Exception as exc:
            self.command.setPlainText(str(exc))

    # ------------------------------------------------------------------
    # Server control
    # ------------------------------------------------------------------

    def _start(self) -> None:
        try:
            config = self.config_store.load()
            model = self._selected_model()
            profile = self._selected_profile()
            host, port = self._effective_host_port()
            if not is_port_available(host, port) and self.controller.try_attach(host, port, router_mode=config.router_mode):
                self.status.setText(f"Attached to existing llama-server on {host}:{port}")
                return
            status = self.controller.start(
                self._argv(),
                host,
                port,
                model_path=config.models_dir if config.router_mode else (model.path if model else None),
                profile_name=profile.name if (profile and not config.router_mode) else None,
                router_mode=config.router_mode,
            )
            if model is not None:
                from llama_data.models import utc_now
                model.last_used_at = utc_now()
                self.library_store.upsert(model)
            if profile is not None:
                profile.last_used_at = model.last_used_at if model is not None else None
                self.profile_store.upsert(profile)
            self._set_status(status)
        except Exception as exc:
            self.status.setText(f"Start failed: {exc}")

    def _stop(self) -> None:
        self._stop_button.setEnabled(False)
        self.status.setText("Stopping llama-server…")
        self._stop_thread = _StopThread(self.controller, self)
        self._stop_thread.completed.connect(self._on_stop_completed, Qt.ConnectionType.QueuedConnection)
        self._stop_thread.failed.connect(self._on_stop_failed, Qt.ConnectionType.QueuedConnection)
        self._stop_thread.finished.connect(self._stop_thread.deleteLater)
        self._stop_thread.start()

    def _on_stop_completed(self, status) -> None:
        self._stop_button.setEnabled(True)
        self._set_status(status)
        self.status.setText("llama-server stopped.")

    def _on_stop_failed(self, message: str) -> None:
        self._stop_button.setEnabled(True)
        self.status.setText(f"Stop failed: {message}")

    def _restart(self) -> None:
        try:
            model = self._selected_model()
            profile = self._selected_profile()
            host, port = self._effective_host_port()
            self._set_status(
                self.controller.restart(
                    self._argv(),
                    host,
                    port,
                    model_path=model.path if model else None,
                    profile_name=profile.name if profile else None,
                )
            )
        except Exception as exc:
            self.status.setText(f"Restart failed: {exc}")

    def _switch_model(self) -> None:
        state = self.controller.status.state
        if state not in {ServerState.RUNNING, ServerState.HEALTHY, ServerState.UNHEALTHY}:
            self.status.setText("Server is not running. Start or Restart instead.")
            return
        host, port = self._effective_host_port()
        model = self._selected_model()
        if not model:
            self.status.setText("No model selected.")
            return
        result = LlamaServerApiClient(host, port).switch_model(model.path)
        if result.restart_required:
            self.status.setText(result.message + " Restarting local process.")
            self._restart()
        else:
            self.controller.note_model_switched(model.path, self._selected_profile().name if self._selected_profile() else None)
            self.status.setText(result.message)

    def _try_attach_existing(self) -> None:
        """On startup, check if llama-server is already running and attach."""
        config = self.config_store.load()
        host, port = self._effective_host_port()
        if self.controller.try_attach(host, port, router_mode=config.router_mode):
            self.status.setText(
                f"Attached to existing llama-server on {host}:{port}"
            )
            self._update_command_preview()

    # ------------------------------------------------------------------
    # Polling / logs
    # ------------------------------------------------------------------

    def _poll_status(self) -> None:
        status = self.controller.status
        config = self.config_store.load()
        if status.state in {ServerState.RUNNING, ServerState.HEALTHY, ServerState.UNHEALTHY}:
            if config.router_mode:
                # Router endpoints are not passive health/status probes in
                # llama-server: /health and /models can be proxied to a model
                # and trigger lazy loads / LRU eviction. In router mode the UI
                # observes only the process/log stream; clients are the only
                # HTTP traffic allowed to touch the router.
                status = self.controller.status
            else:
                self.controller.poll_health()
                status = self.controller.status
        self._set_status(status)
        self._render_logs()
        self.inspector_changed.emit({
            "title": "Run",
            "chip_text": status.state.value,
            "chip_style": "success" if status.state in {ServerState.HEALTHY, ServerState.RUNNING} else ("warning" if status.state == ServerState.UNHEALTHY else "muted"),
            "line1": status.model_path or "No model selected",
            "line2": f"profile={status.profile_name or '—'} endpoint=http://{status.host}:{status.port}",
            "command_lines": self.command.toPlainText().splitlines()[:4] or ["No command preview available here."],
        })

    def _set_status(self, status) -> None:
        self.state_tile.set_value(status.state.value)
        self.pid_tile.set_value(str(status.pid or "\u2014"))
        self.endpoint_tile.set_value(f"http://{status.host}:{status.port}")
        self.profile_tile.set_value(status.profile_name or "\u2014")
        health = status.api_status.health if status.api_status else "—"
        capability = "api-load=yes" if status.api_status and status.api_status.model_load_supported else "api-load=no"
        slots = status.api_status.total_slots if status.api_status and status.api_status.total_slots is not None else "—"
        self.status.setText(
            f"model={status.model_path or '—'} health={health} {capability} slots={slots} last_error={status.last_error or '—'}"
        )

    def _render_logs(self) -> None:
        query = self.log_search.text().strip().lower()
        source = self.log_source.currentText()
        lines = self.controller.log_buffer.lines()
        rendered: list[str] = []
        for line in lines:
            if source != "all" and line.source != source:
                continue
            if query and query not in line.text.lower():
                continue
            rendered.append(f"{line.timestamp} [{line.source}] {line.text}")
        self.logs.setPlainText("\n".join(rendered[-1000:]))
        cursor = self.logs.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.logs.setTextCursor(cursor)

    def _copy_logs(self) -> None:
        text = self.logs.toPlainText()
        if text:
            self.logs.copy()

    def _clear_logs(self) -> None:
        self.controller.log_buffer.clear()
        self.logs.clear()


__all__ = ["RunPage"]
