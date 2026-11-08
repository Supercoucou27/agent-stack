# qt_app/app/widgets/

## Responsibility

Reusable, theme-aware UI primitives and shell chrome for the LlamUI Qt application.

| File | Role |
|------|------|
| `buttons.py` | Semantic button variants (`SecondaryButton`, `DangerButton`, `SuccessButton`, `FilterPill`) that encapsulate QSS `variant` property assignment. |
| `cards.py` | Card family (`Card`, `OptionCard`, `FieldTile`), status pills (`Chip`), monospaced log blocks (`MonoLog`), elided labels (`ElidedLabel`), and download queue rows (`DownloadRow`). |
| `collapsible.py` | `CollapsibleGroup` — a titled section with a caret header that toggles body visibility. |
| `flow.py` | `FlowLayout` — a custom `QLayout` that flows child widgets left-to-right with wrapping. |
| `header.py` | `Header` — a static top bar showing page title and optional subtitle. |
| `inspector.py` | `Inspector` — a right-side panel with collapse/expand toggle and a thin `_CollapsedStrip` fallback. |
| `sidebar.py` | `Sidebar` — a left navigation rail mapping `NavItemId` enum values to selectable buttons, with collapse/expand support, SVG icons in collapsed mode, a `collapse_changed` signal, and lazy icon loading via `_nav_icons()`. |
| `slider_spin.py` | `SliderSpinBox` / `SliderDoubleSpinBox` — composite widgets pairing a `QSlider` with a spinbox sharing one value. |
| `__init__.py` | Public API surface; re-exports the widget set used by pages. |

## Design Patterns

**Subclassing for QSS semantics.** `buttons._VariantButton` subclasses `QPushButton` and sets a `variant` property from a class-level `_VARIANT` string. Pages instantiate `DangerButton("Remove")` instead of manually tagging raw `QPushButton`s. Same pattern in `cards.Chip`: `set_style` swaps the `objectName` between `ChipSuccess`, `ChipWarning`, `ChipAccent`, `ChipMuted` and calls `style().polish(self)` to force QSS re-evaluation.

**Composite value widgets with signal suppression.** `SliderSpinBox` and `SliderDoubleSpinBox` synchronise a `QSlider` and a (`QSpinBox` | `QDoubleSpinBox`) under a single `valueChanged` signal. A `_suppress_emit` flag blocks the secondary widget’s signal during update to prevent feedback loops. `setValue` always emits on the composite even when suppressing the internals, preserving Qt’s "programmatic set emits" contract.

**Custom layout implementation.** `FlowLayout` implements the full `QLayout` protocol (`addItem`, `itemAt`, `takeAt`, `setGeometry`, `sizeHint`, `minimumSize`, `heightForWidth`). It measures children, wraps to new rows when `next_x` exceeds the effective rect, and supports `test_only` geometry passes for height queries.

**State-driven visibility toggles.** `Inspector` and `CollapsibleGroup` both hide/show child content rather than destroying/recreating it. `Inspector._content_hide` recursively walks the layout tree and calls `hide()` on leaf widgets, keeping the `QFrame` alive so the collapse/expand transition is instant.

**Enum-based navigation contract.** `NavItemId(str, Enum)` provides stable string keys (`library`, `discover`, `run`, `settings`, `diagnostics`) decoupling the sidebar from page classes. The sidebar emits `navigated(NavItemId)`; pages or a central router react without importing each other.

**Lazy resource loading.** `Sidebar._nav_icons()` defers SVG icon creation until a `QApplication` exists, so collapsed-mode nav icons are available without constructing Qt resources at import time.

## Data & Control Flow

**Signals (outward flow)**
- `Sidebar.navigated(NavItemId)` — emitted when a nav button is clicked. Callers call `set_active(id)` to sync the UI check state.
- `Sidebar.collapse_changed(bool)` — emitted when the navigation rail collapses or expands.
- `Inspector.toggled(bool)` — emitted on collapse/expand. `_CollapsedStrip` is shown/hidden as a side effect.
- `CollapsibleGroup.toggled(bool)` — emitted when the header strip is clicked.
- `SliderSpinBox.valueChanged(int)` / `SliderDoubleSpinBox.valueChanged(float)` — single canonical value change for the composite.
- `DownloadRow.cancelled()` — emitted when the row’s cancel button is clicked.

**Internal state mutations (inward flow)**
- `OptionCard.set_changed(bool)` toggles a red dot (`●`) in the header; used by option editors to indicate dirty state.
- `OptionCard.add_editor(QWidget)` injects the control widget into the card body; the card itself does not know widget types.
- `FieldTile.set_value(str)` and `Chip.set_style(ChipStyle)` update display only.
- `Inspector.update_details` / `set_context` and `Sidebar.update_details` accept plain strings and refresh labels directly.

**Signal blocking discipline**
- `SliderSpinBox.blockSignals(bool)` delegates to both internal widgets so callers can bulk-load values without triggering change handlers.
- Inside `_on_slider` / `_on_spin`, `_suppress_emit = True` guards the reciprocal `setValue` call before re-enabling and emitting the composite signal.

## Integration Points

**Theme system (`app.theme`)**
- `Header` imports `theme.HEADER_HEIGHT` for its fixed height.
- `Sidebar` and `Inspector` import `theme` for spacing constants and style context.
- Nearly every widget assigns an `objectName` that the application-wide QSS sheet targets (`Card`, `OptionCard`, `ChipMuted`, `FilterPill`, `CollapsibleGroupHeader`, etc.).

**Page layer (`app.pages`)**
- Pages import widgets from the package `__init__` rather than individual modules.
- `OptionCard` is the primary container for Run-page option editors; pages call `add_editor` to attach a `SliderSpinBox`, `QComboBox`, or custom control.
- `CollapsibleGroup` groups related options (e.g. "Model", "Sampling") on the Run page.
- `FlowLayout` is used in card grids (Library, Discover) where wrapping is needed.
- `DownloadRow` is instantiated by the Download page and wired to download-manager IDs.
- `Inspector.update_details` and `Sidebar.update_details` are called by pages (e.g. Run page) to reflect runtime state without the widgets owning that state.

**Main window (`app.main_window`)**
- `Sidebar` and `Inspector` are owned by `MainWindow` as persistent chrome; the sidebar’s `navigated` signal is connected to the main window’s page-switching slot, and `collapse_changed` lets the shell adjust splitter sizes and persist collapsed state.
- `Inspector.toggled` can be connected to the main window to adjust the central widget width or save the panel state.

**No upstream service dependencies.**
- The widget layer is strictly presentational. It does not import `llama_data` or download managers. State (model names, download bytes, hardware profiles) is pushed in from pages via the public `set_*` / `update_*` APIs.
