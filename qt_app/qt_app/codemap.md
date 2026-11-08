# `qt_app/` — Native Qt Application Shell

## Responsibility

The `qt_app` package is the native PySide6 desktop client for llamaUI. It replaces the prior Tauri/Web frontend with a QWidget-based shell that hosts six functional pages, manages persistent application state, and orchestrates the llama-server runtime.

Package-level files outside `app/` and `llama_data/` provide:
- **`main.py`** — Script-mode entry point. Adds the repository root to `sys.path` so `import qt_app` resolves, then calls `create_app()` and `MainWindow()` and enters the Qt event loop.
- **`__main__.py`** — Package-mode entry point (`python -m qt_app`); delegates to `main.py`.
- **`clean_user_profiles.py`** — One-shot cleanup utility. Deletes profiles whose `raw_args` list exceeds 50 entries (a migration aid for pre-Section-6 data bloat).

## Design Patterns

**Factory + Singleton (Application Bootstrap)**
`qt_app.app.application.create_app()` constructs the single `QApplication` instance, or returns the existing one. It applies a custom dark palette and a generated QSS stylesheet before the window is created, keeping visual configuration centralized in `theme.py`.

**Model-View with Shared Stores**
`MainWindow` instantiates one `ConfigStore`, `LibraryStore`, and `ProfileStore` and injects them into the pages that need them (`LibraryPage`, `RunPage`). Pages mutate state through the stores, which handle persistence. This avoids per-page copies and keeps the source of truth on disk.

**Versioned Persistence with Migration Chains**
All JSON stores use `VersionedEnvelope` (`{version, data}`). `storage.py` defines a `MigrationChain` that advances payloads one version at a time. Each store (config, library, profile) registers its own migrations. Example: `LibraryStore` drops companion-GGUF entries on the v1→v2 migration. This lets the data layer evolve without manual user intervention.

**Strategy / Catalog for Runtime Options**
`llama_options.py` encodes every llama-server CLI flag as a typed `LlamaOption` (kind, default, range, aliases, restart-required). `LLAMA_OPTION_CATALOG` is the single registry. `SettingValueMap` wraps user overrides and can emit an `argv` list. Profile presets (`PRESET_BALANCED_GPU`, etc.) are applied by mapping preset keys into the catalog and producing a new `SettingValueMap`.

**Cross-Process Advisory Locking**
`FileLock` in `storage.py` uses `fcntl` on POSIX to serialize load-and-save cycles across concurrent llamaUI processes. On Windows it is a no-op because single-instance is assumed.

## Data & Control Flow

**Boot sequence (`main.py`)**
1. `_ensure_package_importable()` adds the repo root to `sys.path` for script-mode launches.
2. `create_app()` → `QApplication` singleton + palette + stylesheet.
3. `MainWindow()` builds the widget tree, restores splitter sizes from `QSettings`, and instantiates shared stores.
4. `MainWindow.navigate(NavItemId.RUN)` sets the initial page.
5. `app.exec()` runs the Qt event loop.

**Shell layout (`MainWindow`)**
- Horizontal `QSplitter` with sidebar (fixed stretch 0) and center column (stretch 1).
- Center column contains a header (`QLabel` title/subtitle) and a `QStackedWidget` for pages.
- Sidebar emits `navigated` → `MainWindow.navigate(item_id)`, which swaps the stack page and updates the header text.
- Pages may emit `navigate_requested(str)` (e.g., Library → Run) or `inspector_changed(dict)` (runtime status); `MainWindow` routes both back to the sidebar.

**Store lifecycle**
- `ConfigStore.default()` / `LibraryStore.default()` / `ProfileStore.default()` each resolve `DataPaths` via `default_paths()` (XDG-compliant on Linux, `%LOCALAPPDATA%` on Windows).
- `load()` reads the JSON envelope, migrates if needed, and returns domain objects (`AppConfig`, `LocalModel`, `ModelProfile`).
- `save()` re-serializes the full collection under a `threading.RLock` and an atomic `write-to-temp-then-rename` backed by `FileLock`.

**Option-to-argv pipeline**
1. User edits in `RunPage` produce a `SettingValueMap`.
2. `RunPage` (or `LlamaServerController`) calls `settings.to_argv(catalog)` to get a list of CLI tokens.
3. `LlamaServerController` (in `app/services/runtime.py`) appends the model path and spawns the llama-server process.

## Integration Points

**Upstream (what `qt_app` depends on)**
- **PySide6** — All UI is QWidget-based. `theme.py` sets the global stylesheet and palette via `QApplication.setStyleSheet` / `setPalette`. Splitter persistence uses `QSettings` with `IniFormat`.
- **`llama_data`** — Imported as a sibling package (`from llama_data import ...`). `MainWindow` injects `ConfigStore`, `LibraryStore`, and `ProfileStore` into pages. `RunPage` and `LibraryPage` depend on `LocalModel`, `ModelProfile`, `AppConfig`, and `LLAMA_OPTION_CATALOG`.
- **`app/services`** — `MainWindow` does not import services directly, but pages do:
  - `LibraryPage` → `library_scan.scan_library()`
  - `DiscoverPage` → `hugging_face.HuggingFaceSearchService`
  - `RunPage` → `runtime.LlamaServerController`, `runtime_api.LlamaServerApiClient`
  - `DiagnosticsPage` → `diagnostics.framework_diagnostics()`
  - Settings uses `llama_server.validate_llama_server()` and `option_schema.build_runtime_schema()`

**Downstream (what depends on `qt_app`)**
- No other package in the repository imports `qt_app`. It is the leaf application layer.
- The cleanup script `clean_user_profiles.py` is standalone; it hardcodes `~/.local/share/llamaUI/profiles.json` and is meant to be run manually once.

**Package-level imports**
- `qt_app.app.__init__` re-exports `create_app`, `MainWindow`, and `NavItemId` as the public shell API.
- `qt_app.llama_data.__init__` re-exports the full data layer: stores, models, option catalog, and storage primitives.
