# qt_app/app/pages — Codemap

## Responsibility

This package contains the full-screen **page widgets** that populate the central stack of the main window shell. Each page is a self-contained `QScrollArea` subclass that builds its own UI inside a `build()` override.

| File | Class | Purpose |
|------|-------|---------|
| `base.py` | `PageBase` | Abstract scrollable page with `PagePolicy` and a `build()` template method. |
| `base.py` | `PagePolicy` | Enum (`STANDARD`, `INSPECTOR_OPTIONAL`, `FULL_WIDTH`) telling the shell how to lay out sidebar / inspector. |
| `library.py` | `LibraryPage` | Local GGUF inventory: model picker, detail card, metadata badges, hardware-fit tags, profile buttons. |
| `discover.py` | `DiscoverPage` | Hugging Face search, repo file selection, split-set grouping, download queue with live progress. |
| `settings.py` | `SettingsPage` | Application configuration: binary path, models directory, host/port, router mode, global defaults, HF token. |
| `run.py` | `RunPage` | llama-server lifecycle (start / stop / restart), schema-driven option editors, profile CRUD, log streaming, router mode, and advanced option groups rendered with `_WrappedTabs` (FlowLayout buttons + `QStackedWidget`) for multi-row tab wrapping. |
| `dashboard.py` | `DashboardPage` | Real-time server metrics dashboard: rolling QPainter line/area charts (throughput, health latency, slot utilization), status card, and streaming log viewer with search/filter/auto-scroll. Shares `LlamaServerController` with `RunPage`. |
| `diagnostics.py` | `DiagnosticsPage` | Framework diagnostics, binary introspection, Hugging Face connectivity probe. |
| `placeholders.py` | `RunPlaceholderPage` | Early Run page shell used before `RunPage` was wired. |

---

## Design Patterns

### Template Method (`PageBase.build`)
`PageBase.__init__` creates the scroll viewport (`_body` + `_layout`) and then calls `self.build()`. Subclasses never override `__init__` for UI construction; they only populate `_layout` inside `build()`.

### Card-Based Scaffolding
Every page wraps logical sections in `Card` widgets (from `..widgets.cards`) with consistent margins (`16, 14, 16, 14`) and `CardTitle` headers. This gives the app a uniform "dashboard" visual density.

### Worker Threads for Blocking I/O
- `DiscoverPage` spawns `_SearchThread` and `_CardThread` (both `QThread`) so Hugging Face network calls do not block the UI event loop.
- `RunPage` spawns `_StopThread` to call `LlamaServerController.stop()` asynchronously.
- Threads emit `Signal` objects back to the page; the page updates widgets from the receiving slots.

### Store Injection with Fallback
Pages accept store instances (`ConfigStore`, `LibraryStore`, `ProfileStore`) as constructor arguments and fall back to `Store.default()` when none is provided. This makes unit testing possible without global state.

### Signal-Based Cross-Page Communication
- `navigate_requested.emit(str)` — emitted by `DiscoverPage` (on download completion) and `PageBase` (base signal) so the shell can switch the visible page.
- `inspector_changed.emit(dict)` — emitted by `LibraryPage` and `RunPage` to push contextual data to the shell’s right-hand inspector panel.

### Schema-Driven UI Generation (`RunPage`)
`RunPage` builds its option editors from two sources:
1. Static `LLAMA_OPTION_CATALOG` (curated metadata: labels, types, defaults, importance).
2. Runtime `RuntimeSchema` parsed from the actual `llama-server --help` output.

Editors are created by `_make_editor(catalog_option)` and `_make_schema_editor(runtime_option)`, producing typed widgets (`QCheckBox`, `QSpinBox`, `QDoubleSpinBox`, `QComboBox`, `QLineEdit`, `SliderSpinBox`, `SliderDoubleSpinBox`).

Advanced option groups use `_WrappedTabs` (FlowLayout tab buttons + `QStackedWidget`) instead of `QTabWidget` so tabs wrap across multiple rows at narrow widths.

### Two-Mode Runtime (`RunPage`)
`RunPage` supports **Single Model** mode (one model + one profile) and **Router** mode (`--models-dir` serving all local models). Mode switches hide/show entire cards (`_main_settings_card`, `_advanced_card`, `_router_panel`) and stop a running server before reconfiguring.

---

## Data & Control Flow

### Page Construction
1. Shell instantiates a page, optionally passing store references.
2. `PageBase.__init__` sets up the scroll area and calls `build()`.
3. `build()` adds `Card` widgets to `self._layout`, wires signals, and performs an initial data load (e.g., `_refresh()`, `_reload_models()`).

### LibraryPage
- `_refresh()` → queries `LibraryStore` for `list<LocalModel>` → populates the `QComboBox` picker.
- `_on_picker_changed()` → reads model metadata, quant, hardware fit, profile count → updates detail labels, `QTextBrowser` card text, and action buttons.
- `_apply_filter()` → filters the picker items by substring match on name / path / quant.
- `_on_rescan()` → calls `scan_models_dir()` (background-capable) then re-reads the store.
- Emits `inspector_changed` with model metadata for the shell inspector.

### DiscoverPage
- `_search()` → builds `HfFilter` list from `FilterPill` states → starts `_SearchThread`.
- `_SearchThread.finished` → receives `list[HfRepoSummary]` → sorts repos by `HardwareFit.score` then downloads/likes and populates the resizable results table.
- `_select_repo()` → groups files into `_Selectable` quant/split entries (split-set detection via regex), populates the file combo, refreshes the quant-fit comparison table, and starts `_CardThread` for normalized model card text.
- `_refresh_selected_fit()` / `_refresh_quant_fit_table()` → recompute `compute_hardware_fit()` for the currently selected quant and for every selectable quant; columns show fit tier plus total memory at 16K / 32K / 64K / 128K contexts.
- `_download_selected()` → creates `HfDownloadRequest` entries for the selected model plus companions, keeps the selected `HardwareFit` for the primary job, and hands jobs to `DownloadManager`.
- `_on_manager_finished()` → updates queue rows, optionally asks the user to create a recommended default `ModelProfile` from `recommended_profile_settings()`, and navigates to Library when the queue empties after a successful download.
- `DownloadManager` signals (`progress`, `status_changed`, `finished`, `queue_changed`) → update `DownloadRow` widgets and queue status label.

### SettingsPage
- `_load_config()` → `ConfigStore.load()` → caches in `self._config`.
- Each card builder creates input widgets pre-filled from `AppConfig`.
- `_on_save()` → reads all widgets → builds new `AppConfig` → `ConfigStore.save()` → shows ephemeral "Saved" feedback via `QTimer`.
- `_browse_server()` / `_browse_models_dir()` → `pick_directory()` / `pick_file()` dialog services.
- `_validate()` → `build_runtime_schema(path)` → populates introspection card with parsed/curated/unknown counts.

### RunPage
- `_load_schema()` → `build_runtime_schema(binary_path)` → caches in `SchemaCache` (disk-backed) → builds `_schema_options_by_id`.
- `_build_main_settings()` / `_build_advanced_groups()` → creates `OptionCard` + editor widgets for each option.
- `_start()` → reads editor values via `_settings_from_form()` → `clean_raw_args()` → `build_argv()` → `controller.start(argv, env)`.
- `controller.logged` (signal) → `_append_log()` → `QPlainTextEdit` log buffer (max 10,000 blocks).
- `QTimer` (2 s interval) → `_poll_status()` → `controller.status` + `LlamaServerApiClient.health()` → updates state/PID/endpoint tiles and inspector.
- Router mode: `_poll_router_models()` → API `list_loaded_models()` → dynamic unload buttons.
- Profile CRUD: `_save_profile()`, `_save_profile_as()`, `_duplicate_profile()`, `_reset_form_to_profile()` → `ProfileStore`.

### DiagnosticsPage
- `_refresh()` called once in `build()`.
- Framework: `framework_diagnostics()` → `QLabel` summary + `QPlainTextEdit` detail.
- Binary: `build_runtime_schema(path)` → version, parsed counts, probe metadata.
- HF: `_resolve_hf_token()` (env-var > saved) → `check_hf_connectivity(token)` → reachability chip + latency.

### DashboardPage
- `set_controller()` — called by `MainWindow` after construction to inject `RunPage`'s `LlamaServerController`.
- `QTimer` (2 s interval) → `_poll()` → reads `controller.status` + `LlamaServerApiClient.fetch_props()` → updates status card tiles, pushes metrics to three `_RollingChart` widgets (throughput, health latency, slot utilization), and re-renders filtered logs from `controller.log_buffer`.
- `_RollingChart` — pure `QPainter` rolling line/area chart with grid, axis labels, and current-value readout. `deque(maxlen=120)` for 2-minute rolling window.
- Log viewer mirrors `RunPage`'s pattern: reads `log_buffer.lines()`, applies search + source filter, renders to `QPlainTextEdit` with auto-scroll toggle.

---

## Integration Points

### Upstream (shell / main window)
- `PagePolicy` is read by the shell to decide sidebar/inspector visibility.
- `navigate_requested` is caught by the shell to swap the central stacked widget.
- `inspector_changed` is caught by the shell to update the right-hand inspector panel.

### Downstream (`llama_data` stores)
- `ConfigStore` — read/written by `SettingsPage`, `RunPage`, `DiscoverPage`, `DiagnosticsPage`.
- `LibraryStore` — read by `LibraryPage` and `DiscoverPage` (for download destination).
- `ProfileStore` — read/written by `LibraryPage` and `RunPage`.

### Services (`app/services`)
- `runtime.LlamaServerController` / `runtime_api.LlamaServerApiClient` — `RunPage` process control and health polling.
- `download_service.DownloadManager` — `DiscoverPage` queue orchestration.
- `hugging_face.HuggingFaceSearchService` — `DiscoverPage` search + card fetching.
- `option_schema.build_runtime_schema` — `RunPage` and `DiagnosticsPage` binary introspection.
- `diagnostics.framework_diagnostics` — `DiagnosticsPage` system probe.
- `library_scan.scan_models_dir` — `LibraryPage` rescan trigger.
- `dialogs.pick_directory` / `pick_file` — `SettingsPage` browse buttons.

### Widgets (`app/widgets`)
- `cards.Card`, `CardTitle`, `FieldTile`, `Chip`, `DownloadRow`, `OptionCard`, `MonoLog`, `ElidedLabel` — layout primitives reused across all pages.
- `buttons.SuccessButton`, `SecondaryButton`, `DangerButton`, `FilterPill` — action buttons.
- `flow.FlowLayout` — `RunPage` action row wrapping.
- `slider_spin.SliderSpinBox`, `SliderDoubleSpinBox` — numeric editors with slider + spin box.

### External Libraries
- `PySide6.QtCore` / `QtWidgets` — all UI is Qt6.
- `llama_data.llama_options.LLAMA_OPTION_CATALOG` — static option metadata consumed by `RunPage`.
- `llama_data.models.AppConfig`, `HfTokenSource`, `LocalModel`, `ModelProfile` — data models.
