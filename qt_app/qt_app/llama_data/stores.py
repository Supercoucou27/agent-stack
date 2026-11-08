"""Versioned stores for config, library, and profiles."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional
from .models import AppConfig, LocalModel, ModelProfile, utc_now
from .paths import DataPaths, default_paths
from .storage import FileLock, Migration, MigrationChain, VersionedEnvelope, current_version, load_envelope, resolve_version, save_envelope

logger = logging.getLogger(__name__)


# -- Companion / non-runnable GGUF detection ----------------------------------
# Duplicated from app.services.library_scan so the data layer stays free of
# app-layer dependencies. Keep in sync with app/services/library_scan.py.

_COMPANION_PREFIXES = (
    "mmproj-", "mmproj.",
    "text-encoder-", "text-encoder.",
    "vision-encoder-", "vision-encoder.",
)


def _is_companion_path(path_str: str) -> bool:
    """True for mmproj, text-encoder, vision-encoder, and embedding GGUFs."""
    lower = Path(path_str).name.lower()
    for prefix in _COMPANION_PREFIXES:
        if prefix in lower:
            return True
    if "embedding" in lower:
        idx = 0
        while True:
            pos = lower.find("embedding", idx)
            if pos == -1:
                break
            if pos == 0 or lower[pos - 1] != "_":
                return True
            idx = pos + len("embedding")
    return False


# -- Per-store migration chains -----------------------------------------------
# Each store advances independently. Config and profiles have no v1→v2 work;
# the library drops companion entries that pre-Phase-11 scans admitted.

CONFIG_MIGRATIONS: dict[int, Migration] = {1: lambda payload: payload}
PROFILE_MIGRATIONS: dict[int, Migration] = {1: lambda payload: payload}


def _library_v1_to_v2(payload: Any) -> Any:
    """Drop companion GGUF entries from the library."""
    if not isinstance(payload, list):
        return payload
    kept: list = []
    dropped = 0
    for item in payload:
        if isinstance(item, dict) and isinstance(item.get("path"), str) and _is_companion_path(item["path"]):
            dropped += 1
            continue
        kept.append(item)
    if dropped:
        logger.info("library v1→v2: dropped %d companion entr%s", dropped, "y" if dropped == 1 else "ies")
    return kept


LIBRARY_MIGRATIONS: dict[int, Migration] = {1: _library_v1_to_v2}
_CONFIG_CHAIN = MigrationChain(migrations=CONFIG_MIGRATIONS, target=current_version())
_PROFILE_CHAIN = MigrationChain(migrations=PROFILE_MIGRATIONS, target=current_version())
_LIBRARY_CHAIN = MigrationChain(migrations=LIBRARY_MIGRATIONS, target=current_version())
_CONFIG_WRITE_LOCK = threading.RLock()
_LIBRARY_WRITE_LOCK = threading.RLock()
_PROFILE_WRITE_LOCK = threading.RLock()



@dataclass
class ConfigStore:
    paths: DataPaths

    @classmethod
    def default(cls) -> "ConfigStore":
        return cls(default_paths())

    def load(self) -> AppConfig:
        with _CONFIG_WRITE_LOCK:
            envelope = load_envelope(self.paths.config_path)
            if envelope is None:
                return AppConfig()
            data = resolve_version(envelope, _CONFIG_CHAIN)
            return AppConfig.from_json(data)

    def save(self, config: AppConfig) -> None:
        with _CONFIG_WRITE_LOCK, FileLock(self.paths.config_path):
            self.paths.ensure()
            save_envelope(self.paths.config_path, VersionedEnvelope(current_version(), config.to_json()))


@dataclass
class LibraryStore:
    paths: DataPaths

    @classmethod
    def default(cls) -> "LibraryStore":
        return cls(default_paths())

    def load(self) -> list[LocalModel]:
        """Load the library, applying any pending migrations.

        If a migration advances the persisted version, the migrated
        payload is written back to disk so subsequent loads are a no-op.
        This is the one-time cleanup that replaced the inline wipe in
        MainWindow.__init__.
        """
        with _LIBRARY_WRITE_LOCK:
            envelope = load_envelope(self.paths.library_path)
            if envelope is None:
                return []
            data = resolve_version(envelope, _LIBRARY_CHAIN)
            if not isinstance(data, list):
                return []
            out: list[LocalModel] = []
            for item in data:
                try:
                    out.append(LocalModel.from_json(item))
                except (TypeError, ValueError):
                    continue
            # Persist the migration if we advanced the version.
            if envelope.version < current_version():
                self.paths.ensure()
                save_envelope(
                    self.paths.library_path,
                    VersionedEnvelope(current_version(), data),
                )
            return out

    def save(self, models: Iterable[LocalModel]) -> None:
        with _LIBRARY_WRITE_LOCK:
            self.paths.ensure()
            payload = [m.to_json() for m in models]
            save_envelope(self.paths.library_path, VersionedEnvelope(current_version(), payload))

    def upsert(self, model: LocalModel) -> None:
        with _LIBRARY_WRITE_LOCK, FileLock(self.paths.library_path):
            models = {m.id: m for m in self.load()}
            existing = models.get(model.id)
            if existing is not None:
                model.created_at = existing.created_at
            model.updated_at = utc_now()
            models[model.id] = model
            self.save(models.values())


@dataclass
class ProfileStore:
    paths: DataPaths

    @classmethod
    def default(cls) -> "ProfileStore":
        return cls(default_paths())

    def load(self) -> list[ModelProfile]:
        with _PROFILE_WRITE_LOCK:
            envelope = load_envelope(self.paths.profiles_path)
            if envelope is None:
                return []
            data = resolve_version(envelope, _PROFILE_CHAIN)
            if not isinstance(data, list):
                return []
            out: list[ModelProfile] = []
            for item in data:
                try:
                    out.append(ModelProfile.from_json(item))
                except (TypeError, ValueError, KeyError):
                    continue
            return out

    def save(self, profiles: Iterable[ModelProfile]) -> None:
        with _PROFILE_WRITE_LOCK:
            self.paths.ensure()
            payload = [p.to_json() for p in profiles]
            save_envelope(self.paths.profiles_path, VersionedEnvelope(current_version(), payload))

    def list_for_model(self, model_id: str) -> list[ModelProfile]:
        return [p for p in self.load() if p.model_id == model_id]

    def get(self, profile_id: str) -> Optional[ModelProfile]:
        return next((p for p in self.load() if p.id == profile_id), None)

    def upsert(self, profile: ModelProfile) -> None:
        with _PROFILE_WRITE_LOCK, FileLock(self.paths.profiles_path):
            profiles = {p.id: p for p in self.load()}
            profile.touch()
            profiles[profile.id] = profile
            self.save(profiles.values())

    def delete(self, profile_id: str) -> None:
        with _PROFILE_WRITE_LOCK, FileLock(self.paths.profiles_path):
            self.save(p for p in self.load() if p.id != profile_id)

    def set_default(self, profile_id: str) -> None:
        with _PROFILE_WRITE_LOCK, FileLock(self.paths.profiles_path):
            profiles = {p.id: p for p in self.load()}
            target = profiles.get(profile_id)
            if target is None:
                raise LookupError(f"profile {profile_id!r} not found")
            for p in profiles.values():
                if p.model_id != target.model_id:
                    continue
                wants_default = p.id == profile_id
                if p.is_default != wants_default:
                    p.is_default = wants_default
                    p.touch()
            self.save(profiles.values())


__all__ = ["ConfigStore", "LibraryStore", "ProfileStore"]
