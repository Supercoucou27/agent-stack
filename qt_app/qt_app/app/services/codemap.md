# `qt_app/app/services` Codemap

## Responsibility

This folder is the **service layer** of the Qt application—UI-independent business logic that orchestrates external binaries, network I/O, filesystem scanning, and diagnostics. Each module owns a narrow vertical:

| File | Core Responsibility |
|------|---------------------|
| `runtime.py` | Local `llama-server` subprocess lifecycle: start, stop, restart, log capture, health polling, and argv construction. |
| `runtime_api.py` | Stateless HTTP client for the running server's API (`/health`, `/props`, `/model/load`, `/models`). |
| `download_service.py` | Streaming download of HuggingFace files with resume, SHA verification, progress callbacks, and a capped concurrent queue (`DownloadManager`). |
| `hugging_face.py` | HF API search, repo hydration, quant inference, hardware-fit heuristics, and connectivity probes. |
| `library_scan.py` | Scan a local directory for `.gguf` files, infer quantization, filter companion files (mmproj, encoders), and upsert into `LibraryStore`. |
| `llama_server.py` | Binary validation: check that a selected `llama-server` path is executable and extract its version string. |
| `help_parser.py` | Regex-based parser for `llama-server --help` output, producing structured `ParsedOption` rows with inferred value kinds and groups. |
| `option_schema.py` | Merge the curated `LLAMA_OPTION_CATALOG` with per-binary `--help` output to produce a `RuntimeSchema`, cached on disk by SHA. |
| `diagnostics.py` | Collect framework evidence: GPU vendor (sysfs), NVIDIA driver version, Qt platform plugins, portal descriptors, DBus reachability. |
| `dialogs.py` | Thin wrappers around `QFileDialog` for single/multi file picks, directory picks, and save dialogs. |
| `__init__.py` | Public re-export surface so pages import from `..services` rather than deep paths. |

## Design Patterns

**State machine** — `runtime.py` models the server with an explicit `ServerState` enum (`STOPPED` → `STARTING` → `RUNNING` → `HEALTHY`/`UNHEALTHY` → `STOPPING`/`EXITED`/`ERROR`). Status transitions are centralized in `LlamaServerController._sync_state()`.

**Observer / Signals** — `DownloadManager` is a `QObject` that emits Qt signals (`progress`, `status_changed`, `finished`, `queue_changed`) so pages can bind UI widgets without polling. `LlamaServerController` supports an optional `on_log` callback for real-time log streaming alongside its internal `LogBuffer`.

**Thread-per-worker with capped concurrency** — `DownloadManager` maintains a `deque` of pending jobs and spawns at most `_MAX_CONCURRENT_DOWNLOADS` (3) `_DownloadThread` (`QThread`) workers. Finished workers trigger `_drain()` to pull the next job.

**Daemon reader threads** — `LlamaServerController.start()` spawns two daemon threads that read `stdout`/`stderr` line-by-line and emit `LogLine` objects. A third daemon thread polls `/health` every `_HEALTH_INTERVAL` (1 s) and updates `ServerState`.

**Session-scoped log isolation** — Each `start()` increments a `_log_session` counter. Reader threads capture the session id at spawn; `_emit()` drops lines whose session does not match. This prevents stale output from a dying process from bleeding into a fresh log buffer after restart.

**Fresh-event health shutdown** — Instead of clearing a shared `threading.Event`, `stop()` creates a new event per health-poll session. This avoids the classic race where `set()` followed by `clear()` cancels a concurrent `stop()`.

**Protocol / Duck typing** — `HfSearchService` is a `Protocol` with a single `search(...)` method. `HuggingFaceSearchService` is the real implementation; `NotImplementedHfSearchService` is a null-object fallback.

**Immutable value objects** — Search and scan results use frozen dataclasses (`HfFilter`, `HfFile`, `HfRepoSummary`, `ScanResult`) so they can safely cross thread boundaries and be used as dict keys.

**Lazy caching** — `LlamaServerApiClient` caches the `/model/load` capability probe (`_model_load_supported`). `SchemaCache` loads/saves `RuntimeSchema` JSON from `llama_data` paths keyed by binary SHA.

**Atomic writes** — `download_file()` streams to a `.part` temp file next to the destination, then `shutil.move`s it into place. Partial downloads are preserved for resume unless SHA verification is requested.

## Data & Control Flow

### Runtime Flow (`runtime.py` + `runtime_api.py`)

1. **Build argv** — `build_argv(config, model, profile, extra_args)` merges `AppConfig`, `LocalModel`, `ModelProfile`, and user overrides into a CLI argument list, including `--models-preset` INI generation when router mode is enabled.
2. **Start** — `LlamaServerController.start(argv, host, port, ...)` validates the binary and port, bumps `_log_session`, clears `LogBuffer`, spawns `subprocess.Popen` with `start_new_session=True`, and launches stdout/stderr reader threads plus the health-poll thread.
3. **Health poll** — The background thread calls `LlamaServerApiClient.status()` (GET `/health`, then probe `/model/load`). Reachability drives transitions between `RUNNING`, `HEALTHY`, and `UNHEALTHY`.
4. **Model switch** — `switch_model(path)` tries `POST /model/load` via `LlamaServerApiClient`. If the API is absent, it returns `restart_required=True` so the caller can stop/start with the new model.
5. **Stop** — `stop()` signals the health thread, sends `SIGTERM` to the process group, waits `graceful_timeout`, escalates to `SIGKILL`, and marks `ERROR` only if the process truly survives.

### Download Flow (`download_service.py`)

1. **Enqueue** — `DownloadManager.enqueue(HfDownloadRequest)` assigns a job id, appends to `_pending`, and calls `_drain()`.
2. **Drain** — `_drain()` pops jobs while active workers < 3, starts a `_DownloadThread`, and emits `queue_changed`.
3. **Download** — `_DownloadThread.run()` calls `DownloadService.download(...)`, which delegates to `download_file()`. The function streams 64 KiB chunks via `urllib.request`, supports HTTP Range resume, optional SHA-256 verification, and polls a `cancel_check` lambda.
4. **Persist** — On completion, `DownloadService` builds a `LocalModel` from the request metadata and file stats, writes a card cache if present, and upserts into `LibraryStore`.
5. **Signals** — Progress is throttled to 0.1 s intervals via a closure inside `run()`. Completion emits back to `DownloadManager` on the main thread via `Qt.ConnectionType.QueuedConnection`.

### HF Search Flow (`hugging_face.py`)

1. **Search** — `HuggingFaceSearchService.search(query, filters)` calls the HF `/api/models` endpoint with `filter=gguf`.
2. **Hydrate** — A `ThreadPoolExecutor` (max 6) maps `_hydrate_repo` over results. Each repo fetches detail JSON and siblings, falling back to a full tree walk if size metadata is missing.
3. **Quant & Fit** — File names are parsed for quantization via regex. `compute_hardware_fit` evaluates selected GGUF files against detected RAM/VRAM/CPU, companion projectors, MTP/draft files, and KV cache estimates for 16K / 32K / 64K / 128K contexts. It returns `HardwareFit` rows with score, label, per-context tier/bytes, and optional `MoeFit` expert estimates for MoE models.
4. **Recommendations** — `recommended_profile_settings()` converts a `HardwareFit` into sensible llama-server profile values (`ctx_size`, `n_gpu_layers`, `flash_attn`, `cache_type_k/v`, batch sizes, threads, `parallel`) so Discover can offer a default profile after download.
5. **Card normalization** — `fetch_card_text()` reads raw README Markdown and runs `normalize_model_card_markdown()` so embedded HuggingFace HTML lists/tables render as text in `QTextBrowser`.
6. **Outcome** — Returns an `HfSearchOutcome` (ok / error / empty) that pages render directly without exception handling in the UI layer.

### Library Scan Flow (`library_scan.py`)

1. **Scan** — `scan_library(models_dir, library)` walks the directory for `.gguf` files.
2. **Filter companions** — Files matching `mmproj`, `text-encoder`, `vision-encoder`, or `embedding` patterns are excluded from primary runnable models but tracked as companions.
3. **Upsert** — For each primary GGUF, `infer_quant` extracts the quantization from the filename. The file is registered in `LibraryStore` with size, mtime, quant, and linked companion paths.
4. **Result** — Returns a `ScanResult` with counts for added, updated, kept, removed, and companion files.

### Option Schema Flow (`option_schema.py` + `help_parser.py`)

1. **Validate binary** — `build_runtime_schema(path)` calls `validate_llama_server()` to run `--help` and `--version` probes.
2. **Parse help** — `parse_help_options()` uses regex to extract flags, value names, defaults, and section headers from the help text.
3. **Merge** — `merge_parsed_options()` overlays parsed options onto the curated `LLAMA_OPTION_CATALOG` by canonical flag. Known flags enrich curated metadata; unknown flags become `RuntimeOption` entries with basic defaults.
4. **Cache** — `SchemaCache` stores the merged `RuntimeSchema` JSON keyed by binary SHA so repeated picks of the same binary skip re-parsing.

### Diagnostics Flow (`diagnostics.py`)

1. **GPU** — `detect_gpu_vendor()` reads `/sys/class/drm/card*/device/vendor` sysfs files to map PCI vendor IDs to `GpuVendor`.
2. **Qt platform** — `_live_qt_platform_name()` reads the active `QGuiApplication` instance; `available_qt_platform_plugins()` scans `QT_PLUGIN_PATH` directories.
3. **Portal** — `portal_descriptors()` lists `.portal` files; `_portal_dbus_reachable()` shells out to `dbus-send` for a best-effort check.
4. **Assembly** — `framework_diagnostics()` collates everything into a single `FrameworkDiagnostics` dataclass, including a parity `workaround_applied` flag for the previous framework's NVIDIA explicit-sync workaround.

## Integration Points

**Pages** (`qt_app/app/pages/*.py`) import services directly and bind signals to widgets:

- **`RunPage`** (`run.py`) owns a `LlamaServerController` instance, calls `build_argv()` with the selected model/profile, pipes `on_log` to a `QPlainTextEdit`, and binds `ServerState` to start/stop button states. It uses `RuntimeSchema` / `SchemaCache` to render per-binary option cards.
- **`DiscoverPage`** (`discover.py`) instantiates `HuggingFaceSearchService`, passes `HfFilter` objects from UI filter pills, and renders `HfRepoSummary` results. It enqueues downloads via `DownloadManager` and connects `progress`/`finished` signals to download rows.
- **`LibraryPage`** (`library.py`) calls `scan_models_dir()` on mount and refresh, uses `infer_quant()` for display labels, and uses `reveal_file()` / `open_hf()` for context-menu actions.
- **`SettingsPage`** (`settings.py`) uses `pick_directory()` and `pick_file()` for path selection, calls `build_runtime_schema()` to validate a user-selected `llama-server` binary, and calls `check_hf_connectivity()` when saving an HF token.
- **`DiagnosticsPage`** (`diagnostics.py`) renders `FrameworkDiagnostics` fields in read-only cards and runs `check_hf_connectivity()` on demand.

**`llama_data` package** — All services read/write canonical data through `llama_data`:
- `AppConfig`, `ConfigStore` — settings and paths.
- `LibraryStore`, `LocalModel` — local model registry.
- `ModelProfile`, `OptionKind`, `LLAMA_OPTION_CATALOG` — curated option definitions shared with the Tauri build.
- `DataPaths`, `default_data_dir()` — filesystem layout.

**Qt framework** — Services remain UI-agnostic except where Qt is required:
- `DownloadManager` and `_DownloadThread` use `QObject`, `QThread`, `Signal`, `Slot` for thread-safe main-thread delivery.
- `dialogs.py` imports `QFileDialog` lazily so the module can be imported outside a Qt event loop.
- `diagnostics.py` optionally queries `QGuiApplication.instance()` for the live platform name.

**External binaries / APIs**:
- `llama-server` — subprocess target for runtime; `--help`/`--version` source for schema.
- `nvidia-smi` — VRAM detection in hardware-fit heuristics.
- `dbus-send` — portal reachability probe.
- `huggingface.co/api` and `huggingface.co` — search, model detail, file tree, and raw README cards.
