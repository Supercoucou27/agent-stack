"""Library page: shows local GGUF models from :class:`LibraryStore`.

Phase 6: table of local models with search/filter and summary tiles.
Phase 7: real directory scan, detail panel on row selection with metadata,
model card cache display, and action buttons (Run, Reveal, Open HF).
Phase 11: combo-box picker replaces table rows.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
)

from llama_data import ConfigStore, LibraryStore, LocalModel, ProfileStore

from ..services.hugging_face import compute_hardware_fit
from ..services.library_scan import infer_quant, open_hf, read_card_cache, reveal_file, scan_models_dir
from ..widgets.buttons import DangerButton, SecondaryButton, SuccessButton
from ..widgets.cards import Card, CardTitle, ElidedLabel, FieldTile
from ..widgets.flow import FlowLayout
from .base import PageBase


_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def _fmt_size(size_bytes: Optional[int]) -> str:
    """Render a byte count as a short human string. ``None`` -> ``"—"``."""
    if size_bytes is None or size_bytes < 0:
        return "—"
    value = float(size_bytes)
    for unit in _SIZE_UNITS:
        if value < 1024.0 or unit == _SIZE_UNITS[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def _fmt_quant(model: LocalModel) -> str:
    return (model.quant or infer_quant(model.path) or "—")


def _fmt_last_used(value: Optional[str]) -> str:
    if not value:
        return "—"
    # Persisted as ISO-8601 with timezone. Trim microseconds + tz for density.
    return value.split(".")[0].replace("T", " ")[:16]


def _fmt_path(path: str) -> str:
    home_marker = "/home/"
    if path.startswith(home_marker):
        suffix = path[len(home_marker):]
        if "/" in suffix:
            user, rest = suffix.split("/", 1)
            return f"~/{rest}"
    return path

def _fit(model: LocalModel) -> str:
    return compute_hardware_fit(model.size_bytes) or "unknown"



def _model_label(model: LocalModel) -> str:
    """Build a display label: ``name · quant · size · provider``."""
    name = model.path.rsplit("/", 1)[-1] or model.id
    quant = _fmt_quant(model)
    size = _fmt_size(model.size_bytes)
    provider = model.hf_repo or "local"
    return f"{name} · {quant} · {size} · {provider}"


class LibraryPage(PageBase):
    """Library: local GGUF inventory loaded from :class:`LibraryStore`."""
    inspector_changed = Signal(dict)

    def __init__(
        self,
        library_store: Optional[LibraryStore] = None,
        profile_store: Optional[ProfileStore] = None,
        config_store: Optional[ConfigStore] = None,
        parent=None,
    ) -> None:
        self._library_store = library_store or LibraryStore.default()
        self._profile_store = profile_store or ProfileStore.default()
        self._config_store = config_store or ConfigStore.default()
        self._selected_model: Optional[LocalModel] = None
        self._picker_models: list[LocalModel] = []
        super().__init__(parent)

    def build(self) -> None:
        self.setProperty(
            "subtitle",
            "Local GGUF inventory, model cards, profile counts, hardware fit badges.",
        )
        self._build_header()
        self._build_picker()
        self._build_detail_card()
        self._refresh()

    # -- UI scaffolding --------------------------------------------------

    def _build_header(self) -> None:
        header = Card(self._body)
        layout = QVBoxLayout(header)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(CardTitle("Library", header))

        controls = QFrame(header)
        controls.setObjectName("InsetRaised")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(10, 6, 10, 6)
        controls_layout.setSpacing(8)

        self._search = QLineEdit(controls)
        self._search.setPlaceholderText("Filter by name, path, quant…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        controls_layout.addWidget(self._search, 1)

        self._rescan = SecondaryButton("Rescan", controls)
        self._rescan.setToolTip("Scan the configured models directory for GGUF files.")
        self._rescan.clicked.connect(self._on_rescan)
        controls_layout.addWidget(self._rescan)

        self._open_dir = SecondaryButton("Open models dir", controls)
        self._open_dir.setEnabled(False)
        self._open_dir.setToolTip("Configure the models directory in Settings first.")
        controls_layout.addWidget(self._open_dir)

        layout.addWidget(controls)

        tiles = QFrame(header)
        tiles_layout = QGridLayout(tiles)
        tiles_layout.setContentsMargins(0, 0, 0, 0)
        tiles_layout.setHorizontalSpacing(10)
        tiles_layout.setVerticalSpacing(10)
        self._tile_models = FieldTile("Models", "0", tiles)
        self._tile_profiles = FieldTile("Profiles", "0", tiles)
        self._tile_size = FieldTile("Total size", "—", tiles)
        self._tile_last = FieldTile("Last used", "—", tiles)
        for idx, tile in enumerate(
            (self._tile_models, self._tile_profiles, self._tile_size, self._tile_last)
        ):
            tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            tile.setMinimumWidth(120)
            tiles_layout.addWidget(tile, 0, idx)
        layout.addWidget(tiles)
        self._layout.addWidget(header)

    def _build_detail_card(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(CardTitle("Model detail", card))

        self._detail_title = QLabel("Select a model to view metadata and model card.", card)
        self._detail_title.setObjectName("Muted")
        self._detail_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._detail_title.setWordWrap(True)
        layout.addWidget(self._detail_title)

        self._detail_meta = QLabel("", card)
        self._detail_meta.setObjectName("Muted")
        self._detail_meta.setWordWrap(True)
        self._detail_meta.setTextFormat(Qt.TextFormat.RichText)
        self._detail_meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._detail_meta)

        self._tags_container = QWidget(card)
        self._tags_row = FlowLayout(self._tags_container, hspacing=6, vspacing=6)
        layout.addWidget(self._tags_container)

        # Action buttons
        actions = QHBoxLayout()
        actions.setSpacing(8)

        self._run_model = SuccessButton("Run", card)
        self._run_model.setEnabled(False)
        self._run_model.setToolTip("Set this model as the active selection for the Run page.")
        self._run_model.clicked.connect(self._on_run)
        actions.addWidget(self._run_model)

        self._edit_profiles_btn = SecondaryButton("Edit Profiles", card)
        self._edit_profiles_btn.setEnabled(False)
        self._edit_profiles_btn.setToolTip("Manage profiles for this model (navigate hook pending).")
        self._edit_profiles_btn.clicked.connect(self._on_edit_profiles)

        self._create_profile_btn = SecondaryButton("Create Profile", card)
        self._create_profile_btn.setEnabled(False)
        self._create_profile_btn.setToolTip("Create a profile for this model (navigation hook pending).")
        self._create_profile_btn.clicked.connect(self._on_create_profile)
        actions.addWidget(self._create_profile_btn)
        actions.addWidget(self._edit_profiles_btn)

        self._reveal_file = SecondaryButton("Reveal File", card)
        self._reveal_file.setEnabled(False)
        self._reveal_file.setToolTip("Open the file's parent directory in the file manager.")
        self._reveal_file.clicked.connect(self._reveal_selected)
        actions.addWidget(self._reveal_file)

        self._delete_meta_btn = DangerButton("Delete Metadata", card)
        self._delete_meta_btn.setEnabled(False)
        self._delete_meta_btn.clicked.connect(self._delete_selected_metadata)
        actions.addWidget(self._delete_meta_btn)
        self._open_hf_btn = SecondaryButton("Open HF page", card)
        self._open_hf_btn.setEnabled(False)
        self._open_hf_btn.setToolTip("Open the Hugging Face model page in your browser.")
        self._open_hf_btn.clicked.connect(self._open_selected_hf)
        actions.addWidget(self._open_hf_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._card_text = QTextBrowser(card)
        self._card_text.setOpenExternalLinks(True)
        self._card_text.setReadOnly(True)
        self._card_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._card_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._card_text)
        self._detail_card = card
        self._layout.addWidget(card)

    def _build_picker(self) -> None:
        card = Card(self._body)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self.model_picker = QComboBox(card)
        self.model_picker.setObjectName("ModelPicker")
        self.model_picker.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.model_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_picker.setMinimumContentsLength(24)
        self.model_picker.view().setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.model_picker.currentIndexChanged.connect(self._on_picker_changed)
        layout.addWidget(self.model_picker)

        self._layout.addWidget(card)

    # -- detail panel ----------------------------------------------------

    def _show_detail(self, model: LocalModel) -> None:
        self._selected_model = model

        name = model.path.rsplit("/", 1)[-1] or model.id
        self._detail_title.setText(name)
        self._detail_title.setObjectName("Mono")

        # Build metadata summary
        parts = [f"<b>Path:</b> {model.path}"]
        if model.size_bytes is not None:
            parts.append(f"<b>Size:</b> {_fmt_size(model.size_bytes)}")
        if model.quant:
            parts.append(f"<b>Quant:</b> {model.quant}")
        if model.hf_repo:
            hf_ref = model.hf_repo
            if model.hf_file:
                hf_ref += f" / {model.hf_file}"
            parts.append(f"<b>HF:</b> {hf_ref}")
        if model.architecture:
            parts.append(f"<b>Arch:</b> {model.architecture}")
        if model.license:
            parts.append(f"<b>License:</b> {model.license}")
        if model.base_model:
            parts.append(f"<b>Base:</b> {model.base_model}")
        parts.append(f"<b>Fit:</b> {_fit(model)}")
        profiles = self._profile_store.list_for_model(model.id)
        if profiles:
            parts.append("<b>Profiles:</b> " + ", ".join(p.name + (" (default)" if p.is_default else "") for p in profiles))
        else:
            parts.append("<b>Profiles:</b> none")
        if model.companion_paths:
            parts.append("<b>Companion files:</b> " + ", ".join(_fmt_path(p) for p in model.companion_paths))
        self._detail_meta.setText("<br>".join(parts))

        # Tags as chips
        self._clear_tags()
        if model.tags:
            for tag in model.tags:
                chip = ElidedLabel(tag, self._tags_container)
                chip.setObjectName("Chip")
                chip.setMaximumWidth(180)
                self._tags_row.addWidget(chip)
        self.inspector_changed.emit({
            "title": "Library",
            "chip_text": _fit(model),
            "chip_style": "success" if _fit(model) == "gpu-likely" else ("warning" if _fit(model) == "partial-gpu" else "muted"),
            "line1": name,
            "line2": model.hf_repo or _fmt_path(model.path),
            "command_lines": [f"quant={_fmt_quant(model)}", f"profiles={len(profiles)}", f"size={_fmt_size(model.size_bytes)}"],
        })

        # Show cached model card if available.
        card_md = read_card_cache(model.card_cache_path)
        if card_md:
            display = card_md[:4000]
            if len(card_md) > 4000:
                display += "\n\n… (truncated; see cached file for full card)"
            self._card_text.setMarkdown(display)
        else:
            self._card_text.setPlainText("")

        # Enable action buttons.
        self._run_model.setEnabled(True)
        self._edit_profiles_btn.setEnabled(True)
        self._reveal_file.setEnabled(Path(model.path).exists())
        self._open_hf_btn.setEnabled(bool(model.hf_repo))
        self._delete_meta_btn.setEnabled(True)
        self._create_profile_btn.setEnabled(True)

    def _reveal_selected(self) -> None:
        if self._selected_model:
            reveal_file(self._selected_model.path)

    def _open_selected_hf(self) -> None:
        if self._selected_model:
            open_hf(self._selected_model.hf_repo)

    def _clear_tags(self) -> None:
        """Remove all tag chip widgets from the tags row."""
        while self._tags_row.count():
            item = self._tags_row.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def _persist_model_selection(self, model_id: str, profile_id: str | None = None) -> None:
        """Write the selected model (and optional profile) into config so other pages restore it."""
        config = self._config_store.load()
        config.selected_model_id = model_id
        if profile_id is not None:
            config.selected_profile_id = profile_id
        self._config_store.save(config)

    def _on_run(self) -> None:
        """Persist selected model and navigate to Run page."""
        m = self._selected_model
        if m:
            # Pick the default profile for this model, if any.
            default_profile = next(
                (p for p in self._profile_store.list_for_model(m.id) if p.is_default),
                None,
            )
            self._persist_model_selection(m.id, default_profile.id if default_profile else None)
            name = m.path.rsplit("/", 1)[-1] or m.id
            self._detail_title.setText(f"{name}  ✓  (selected — switching to Run)")
            self.navigate_requested.emit("run")

    def _on_edit_profiles(self) -> None:
        """Persist selected model and navigate to Run page (where profiles are edited)."""
        m = self._selected_model
        if m:
            self._persist_model_selection(m.id)
            name = m.path.rsplit("/", 1)[-1] or m.id
            self._detail_title.setText(f"{name}  (switching to Run)")
            self.navigate_requested.emit("run")

    def _on_create_profile(self) -> None:
        """Persist selected model and navigate to Run page for profile creation."""
        m = self._selected_model
        if m:
            self._persist_model_selection(m.id)
            name = m.path.rsplit("/", 1)[-1] or m.id
            self._detail_title.setText(f"{name}  (switching to Run)")
            self.navigate_requested.emit("run")

    def select_model_by_path(self, path: str) -> None:
        for i in range(self.model_picker.count()):
            mid = self.model_picker.itemData(i)
            for model, _count in getattr(self, "_all_models", []):
                if model.id == mid and model.path == path:
                    self.model_picker.setCurrentIndex(i)
                    return

    # -- data refresh ----------------------------------------------------

    def _refresh(self) -> None:
        try:
            scan_models_dir(self._config_store, self._library_store)
        except Exception:
            # A scan failure should not hide already persisted models.
            pass

        try:
            models = list(self._library_store.load())
        except Exception as exc:  # surface, don't crash the shell
            self._render_error(f"Library store failed to load: {exc}")
            return

        # Filter out companion GGUFs (mmproj, text-encoder, etc.) that may
        # have been saved to the store before the scan filter was added.
        from ..services.library_scan import is_companion_gguf
        models = [m for m in models if not is_companion_gguf(Path(m.path))]

        profile_counts = self._profile_counts_for([m.id for m in models])

        self._all_models: list[tuple[LocalModel, int]] = list(zip(models, profile_counts))
        self._apply_filter(self._search.text())

    def _profile_counts_for(self, model_ids: Iterable[str]) -> list[int]:
        ids = set(model_ids)
        counts = {mid: 0 for mid in ids}
        try:
            for profile in self._profile_store.load():
                if profile.model_id in counts:
                    counts[profile.model_id] += 1
        except Exception:
            pass
        return [counts[mid] for mid in model_ids]

    def _delete_selected_metadata(self) -> None:
        m = self._selected_model
        if not m:
            return
        models = [model for model in self._library_store.load() if model.id != m.id]
        self._library_store.save(models)
        self._selected_model = None
        self._detail_title.setText("Metadata deleted for selected model.")
        self._detail_meta.setText("")
        self._card_text.setPlainText("")
        self._refresh()

    def _apply_filter(self, needle: str) -> None:
        needle = (needle or "").strip().lower()
        rows = self._all_models
        if needle:
            rows = [
                (model, count)
                for model, count in rows
                if needle in model.path.lower()
                or needle in (model.quant or "").lower()
                or needle in model.id.lower()
            ]
        self._render_picker(rows)

    def _render_picker(self, rows: list[tuple[LocalModel, int]]) -> None:
        self.model_picker.blockSignals(True)
        self.model_picker.clear()
        self._picker_models = [m for m, _ in rows]
        models = self._picker_models
        counts = [c for _, c in rows]

        total_size = sum(m.size_bytes or 0 for m in models)
        last_used = max(
            (m.last_used_at for m in models if m.last_used_at),
            default=None,
        )
        self._tile_models.set_value(str(len(models)))
        self._tile_profiles.set_value(str(sum(counts)))
        self._tile_size.set_value(_fmt_size(total_size) if models else "—")
        self._tile_last.set_value(_fmt_last_used(last_used))

        if not self._all_models:
            self.model_picker.addItem("No local models yet")
            self.model_picker.blockSignals(False)
            return
        if not rows:
            self.model_picker.addItem("No models match the current filter")
            self.model_picker.blockSignals(False)
            return

        for model, _count in rows:
            self.model_picker.addItem(_model_label(model), model.id)
        self.model_picker.blockSignals(False)

        # Auto-select first item.
        if self.model_picker.count() > 0:
            self.model_picker.setCurrentIndex(0)
            self._show_detail(models[0])

    def _on_picker_changed(self, index: int) -> None:
        if index < 0:
            return
        models = self._picker_models
        if index < len(models):
            self._show_detail(models[index])

    def _render_empty(self) -> None:
        pass  # Handled by empty combo text in _render_picker.

    def _render_filter_empty(self) -> None:
        pass  # Handled by empty combo text in _render_picker.

    def _render_error(self, message: str) -> None:
        body = QLabel(message, self._body)
        body.setObjectName("Muted")
        body.setWordWrap(True)
        self._layout.addWidget(body)


    def _on_rescan(self) -> None:
        result = scan_models_dir(self._config_store, self._library_store)
        if result.error:
            self._rescan.setText("Error")
            self._detail_title.setText(f"Scan failed: {result.error}")
        else:
            self._rescan.setText(f"+{result.added} new")
        self._refresh()
        # Reset label after a moment.
        QTimer.singleShot(2000, lambda: self._rescan.setText("Rescan"))


__all__ = ["LibraryPage"]
