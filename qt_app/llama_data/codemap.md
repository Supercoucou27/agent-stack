# `qt_app/llama_data` — Data & persistence layer

## Responsibility

This package is the local-first persistence and schema layer for the native Qt llamaUI app. It owns:

* **On-disk layout** — `paths.py` defines `DataPaths` and resolves the per-user data directory (XDG on Linux/macOS, `%LOCALAPPDATA%` on Windows). Files: `config.json`, `profiles.json`, `library.json`, `cards/`, `schema_cache.json`.
* **Domain models** — `models.py` defines `AppConfig`, `HfTokenSource`, `LocalModel`, and `ModelProfile`, plus JSON round-trips for each.
* **Versioned JSON storage** — `storage.py` wraps every persisted payload in a `VersionedEnvelope {version, data}` and provides atomic writes (temp file + `os.replace`), POSIX advisory file locking, and migration chains.
* **Typed stores** — `stores.py` exposes `ConfigStore`, `LibraryStore`, and `ProfileStore`, each with load/save/upsert semantics and per-store `MigrationChain`s.
* **Llama-server option schema** — `llama_options.py` defines `OptionKind`, `LlamaOption`, `LlamaOptionValue`, `SettingValueMap`, `ProfilePreset`, and the static `LLAMA_OPTION_CATALOG` used by both the UI and the process launcher.

Nothing in this package depends on Qt or Tauri. It is imported by `app/services/` and `app/pages/`.

## Design Patterns

1. **Envelope + migration chains**  
   `storage.VersionedEnvelope` tags every file with a schema version. `MigrationChain.apply(start_version, data)` walks forward one version at a time. `CURRENT_SCHEMA_VERSION = 2` is global; each store registers only the migrations it needs.

2. **Store classes as thin dataclasses**  
   `ConfigStore`, `LibraryStore`, and `ProfileStore` hold only a `DataPaths` instance. They provide `load()` / `save()` / `upsert()` helpers, protect writes with `threading.RLock()`, and use `storage.FileLock` for cross-process serialization.

3. **Immutable-by-convention value objects**  
   `LlamaOption` and `LlamaOptionCatalog` are frozen dataclasses. `LlamaOptionValue` and `SettingValueMap` use `__slots__` and return new instances on mutation (`with_value`, `merge`, `without`).

4. **Defensive JSON parsing with graceful degradation**  
   Models use `isinstance` checks on every field in `from_json`. Unknown keys are ignored, missing keys get sensible defaults, and malformed list items are skipped so one bad record does not corrupt the whole store.

5. **Forward-only migrations**  
   Config and profiles have no-op v1→v2 migrations. Library uses `_library_v1_to_v2` to drop companion GGUF entries (`mmproj-*`, `text-encoder-*`, `vision-encoder-*`). The store persists the migrated payload immediately after loading so subsequent starts are free.

6. **Default suppression in argv emission**  
   `SettingValueMap.to_argv()` skips values that match catalog defaults or "natural defaults" (`False`, `0`, `0.0`, `[]`, `""`, `None`). This keeps generated command lines minimal and avoids leaking stale defaults.

## Data & Control Flow

### Write path

```
App/service calls store.save()/upsert()
        │
        ▼
RLock acquired → FileLock acquired (POSIX only)
        │
        ▼
Model.to_json()  →  list/dict payload
        │
        ▼
VersionedEnvelope(version=2, data=payload)
        │
        ▼
json.dumps(..., sort_keys=True)
        │
        ▼
NamedTemporaryFile in same dir → fsync → os.replace
```

### Read path

```
store.load()
        │
        ▼
RLock acquired
        │
        ▼
load_envelope(path) reads JSON → VersionedEnvelope
        │
        ▼
resolve_version(envelope, chain)
        │
        ▼
Model.from_json(item) for each record
        │
        ▼
If envelope.version < CURRENT_SCHEMA_VERSION:
    save_envelope(path, VersionedEnvelope(CURRENT_SCHEMA_VERSION, migrated_data))
```

### Key entities

| File | Type | Purpose |
|------|------|---------|
| `paths.py` | `DataPaths` | Resolved paths for `config.json`, `profiles.json`, `library.json`, `cards/`, `schema_cache.json` |
| `storage.py` | `VersionedEnvelope` | Wrapper `{version, data}` for every persisted file |
| `storage.py` | `MigrationChain` | Stepwise forward migration keyed by source version |
| `storage.py` | `FileLock` | POSIX `flock` sidecar lock; no-op on Windows |
| `models.py` | `AppConfig` | Server binary path, models dir, host/port, HF token source, selected model/profile |
| `models.py` | `LocalModel` | GGUF on disk: path, size, quant, architecture, tags, companion paths, mmproj |
| `models.py` | `ModelProfile` | Per-model settings (`SettingValueMap`), raw args, preset origin, `user_set` |
| `llama_options.py` | `LlamaOptionCatalog` | Static catalog of all supported llama-server CLI flags |
| `llama_options.py` | `SettingValueMap` | Runtime container for user-overridden option values |

### Notable data transformations

* `LocalModel.from_json` derives `mmproj_path` from `companion_paths` when absent.
* `ModelProfile.from_json` backfills `user_set` from non-default `settings` if the field is missing (pre-Section-6 migration).
* `ModelProfile.from_json` runs `clean_raw_args` to strip old round-tripped catalog flags from `raw_args`.
* `LibraryStore.load` writes the migrated payload back to disk immediately when the version advances.

## Integration Points

* **`app/services/`** — Reads and writes through `ConfigStore`, `LibraryStore`, and `ProfileStore`. `library_scan.py` duplicates the companion-detection prefix list defined in `stores.py`; the comment warns to keep them in sync.
* **`app/pages/`** — Reads `LLAMA_OPTION_CATALOG` and `SettingValueMap` to build forms, emits argv through `SettingValueMap.to_argv()`, and applies presets via `apply_preset_to_settings`.
* **llama-server CLI** — `SettingValueMap.to_argv(LLAMA_OPTION_CATALOG)` produces the flag/value list passed to `app.services.runtime_service` for launching the subprocess.
* **Hugging Face** — `HfTokenSource` stores the chosen token source (`none` / `env_var` / `saved`) and is serialized in `AppConfig`. The actual HF API calls live in `app/services/hugging_face.py`.
* **Tests** — `tests/smoke_*.py` construct `DataPaths` against tempdirs and exercise store round-trips, migrations, and `clean_raw_args` behavior.

The package exports its public surface through `__init__.py`; consumers should import from `qt_app.llama_data` rather than individual modules.
