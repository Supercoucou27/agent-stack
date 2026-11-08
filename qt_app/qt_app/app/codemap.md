# `qt_app/app/` Codemap

## Responsibility

This package is the native Qt shell for llamaUI. It bootstraps the `QApplication`, owns the main window chrome, and composes pages, widgets, and services into a coherent desktop app.

- **`__init__.py`** — Public API: exports `create_app`, `MainWindow`, and `NavItemId`.
- **`application.py`** — Singleton `QApplication` factory. Sets HiDPI env hints (`QT_ENABLE_HIGHDPI_SCALING`, `QT_AUTO_SCREEN_SCALE_FACTOR`), application identity (`llamaUI`), and applies the global dark palette + QSS stylesheet.
- **`main_window.py`** — `MainWindow` (`QMainWindow`) is the persistent shell. It hosts a left `Sidebar`, a top header (`QLabel` title/subtitle), and a central `QStackedWidget` that swaps the five page widgets. It persists splitter sizes and sidebar collapsed state via `QSettings`, connects `Sidebar.collapse_changed`, uses the theme-configurable splitter handle width, and routes inter-page navigation requests.
- **`theme.py`** — Single source of truth for the dark theme: color tokens (`BG_APP`, `ACCENT`, `DANGER`, etc.), typography (`FontSpec`), layout constants (`SIDEBAR_WIDTH`, `SIDEBAR_COLLAPSED_WIDTH`, `SPLITTER_HANDLE_WIDTH`, `HEADER_HEIGHT`), the full QSS stylesheet (`build_stylesheet()`), and a dark `QPalette` (`apply_palette()`). Styles include wrapped tabs (`WrappedTabBtn`, `WrappedTabBar`, `WrappedTabStack`), collapsed sidebar nav items, the sidebar toggle button, and the splitter handle.

## Design Patterns

- **Singleton Application** — `create_app()` returns `QApplication.instance()` if one exists; otherwise it constructs and configures a new instance. This lets tests and CLI entry points share the same app object.
- **Shell / Page Decoupling** — `MainWindow` knows pages only as `QWidget` instances mapped by `NavItemId`. Pages are constructed once and added to a `QStackedWidget`; navigation is a stack index switch.
- **Signal-Driven Navigation** — `Sidebar.navigated` emits a `NavItemId`; `MainWindow.navigate()` switches the stack and updates the header. Pages can emit `navigate_requested(str)` to request a cross-page jump (e.g. Library → Run).
- **Collapsible Sidebar** — `Sidebar.collapse_changed(bool)` tells `MainWindow` to adjust splitter sizes; `MainWindow` persists the collapsed state in `QSettings` and restores it on startup.
- **Shared Store Injection** — `MainWindow` instantiates the three persisted stores (`ConfigStore`, `LibraryStore`, `ProfileStore`) and passes them as constructor arguments to the pages that need them. Pages do not create their own store instances.
- **Theme as Static Configuration** — `theme.py` uses module-level constants and pure functions. The stylesheet is built at call time (not import time) to avoid side effects, but the token values are immutable.
- **Compatibility No-Ops** — `set_inspector_visible` and `_set_inspector_collapsed` remain as no-op stubs so that code referencing the legacy three-pane inspector layout does not break; runtime status now lives inside the sidebar.

## Data & Control Flow

1. **Bootstrap** — Entry point (`qt_app/main.py` or `__main__.py`) calls `create_app()` to get a themed `QApplication`, then instantiates `MainWindow` and calls `show()`.
2. **Shell Construction** — `MainWindow.__init__()` builds the layout tree:
   - `QSplitter` (horizontal) → left `Sidebar` + right "center column".
   - Center column → `TopHeader` (`QLabel` title/subtitle) + `QStackedWidget`.
   - Splitter sizes are restored from `QSettings` and saved on `splitterMoved`; sidebar collapse state is restored, `Sidebar.collapse_changed` is wired, and collapse changes adjust splitter sizes.
3. **Page Lifecycle** — All seven pages (`LibraryPage`, `DiscoverPage`, `RunPage`, `SettingsPage`, `DashboardPage`, `DiagnosticsPage`) are constructed up front. Each is added to the stack. `DashboardPage` receives `RunPage`'s `LlamaServerController` via `set_controller()` so both pages share the same log buffer and health data. Optional signals are connected:
   - `navigate_requested` → `MainWindow._on_page_navigate`
   - `inspector_changed` → `MainWindow._on_inspector_changed` (forwards payload to `Sidebar.update_details`)
4. **Navigation** — `navigate(item_id)`:
   - Sets the current stack widget.
   - Highlights the sidebar item.
   - Updates header title/subtitle from page metadata.
   - Calls `_refresh()` on `RUN`, `LIBRARY`, and `DASHBOARD` if available.
   - Calls `_reload_models()` on `RUN` if available.
   - Handles a pending model path hand-off from `DiscoverPage` → `LibraryPage`.
5. **Inspector Updates** — The `RunPage` emits `inspector_changed(dict)` with keys like `title`, `chip_text`, `line1`, `line2`, `command_lines`. `MainWindow` unpacks this and forwards it to `Sidebar.update_details`, which renders runtime status in the left rail.

## Integration Points

- **Pages** (`qt_app/app/pages/`)
  - `LibraryPage` — receives `library_store`, `profile_store`, `config_store`; uses `services.library_scan` for scanning and metadata; uses `widgets.cards`, `widgets.buttons` for UI.
  - `RunPage` — receives `config_store`, `library_store`, `profile_store`; uses `services.runtime` (`LlamaServerController`, `build_argv`), `services.option_schema`, `services.runtime_api`, and many widgets (`buttons`, `cards`, `slider_spin`, `flow`). Emits `inspector_changed` to update the sidebar.
  - `DashboardPage` — receives `LlamaServerController` via `set_controller()` from `MainWindow`; uses `services.runtime`, `services.runtime_api`, pure `QPainter` charts, and `widgets.cards`/`widgets.buttons`. No store dependencies.
  - `DiscoverPage` — Hugging Face search, repo file selection, split-set grouping, download queue with live progress.
  - `SettingsPage` — Application configuration: binary path, models directory, host/port, router mode, global defaults, HF token.
  - `DiagnosticsPage` — Framework diagnostics, binary introspection, HuggingFace connectivity probe.
  - All pages inherit from `PageBase` (`pages/base.py`), which provides a scrollable `QScrollArea` scaffold, `navigate_requested` signal, and a `PagePolicy` enum.

- **Widgets** (`qt_app/app/widgets/`)
  - `Sidebar` — emits `navigated(NavItemId)` and exposes `set_active()` / `update_details()`. Rendered as a vertical button rail with a runtime-status panel at the bottom.
  - `Card`, `FieldTile`, `OptionCard`, `ElidedLabel`, `Chip`, `MonoLog` — reusable card-based layout primitives used across pages.
  - `SliderSpinBox` / `SliderDoubleSpinBox` — composite value editors used heavily in `RunPage`.
  - `CollapsibleGroup`, `FlowLayout`, `Header`, `Inspector` — additional layout and chrome helpers.

- **Services** (`qt_app/app/services/`)
  - `runtime` — `LlamaServerController` manages the `llama-server` subprocess lifecycle (start, stop, health polling, log capture). `build_argv()` constructs the CLI argument list from config + model + profile.
  - `runtime_api` — `LlamaServerApiClient` queries the running server (`/health`, slots, etc.).
  - `download_service` — `DownloadService` handles Hugging Face file downloads with progress callbacks.
  - `library_scan` — `scan_models_dir()`, `infer_quant()`, `read_card_cache()` provide GGUF discovery and metadata.
  - `hugging_face` — `HfSearchService` wraps HF Hub API searches; `check_hf_connectivity()` verifies reachability.
  - `diagnostics` — `FrameworkDiagnostics` collects GPU, platform-plugin, and portal information for the Diagnostics page.
  - `option_schema` — `build_runtime_schema()` and `SchemaCache` expose a typed view over `llama_data.llama_options.LLAMA_OPTION_CATALOG`.

- **llama_data** (`qt_app/llama_data/`)
  - `ConfigStore`, `LibraryStore`, `ProfileStore` — persisted JSON/INI stores that are injected into pages by `MainWindow`.
  - `LLAMA_OPTION_CATALOG`, `ModelProfile`, `LocalModel` — domain types consumed by `RunPage` and `LibraryPage`.
