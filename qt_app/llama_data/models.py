"""Core persisted domain models for the native Qt rebuild."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .llama_options import LLAMA_OPTION_CATALOG, SettingValueMap, clean_raw_args


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HfTokenSource:
    kind: str = "none"  # none | env_var | saved
    token: Optional[str] = None

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "token": self.token}

    @classmethod
    def from_json(cls, data: Any) -> "HfTokenSource":
        if not isinstance(data, dict):
            return cls()
        kind = str(data.get("kind") or "none")
        token = data.get("token") if isinstance(data.get("token"), str) else None
        if kind not in {"none", "env_var", "saved"}:
            kind = "none"
        return cls(kind=kind, token=token)


@dataclass
class AppConfig:
    llama_server_path: Optional[str] = None
    models_dir: Optional[str] = None
    host: str = "127.0.0.1"
    port: int = 8080
    hf_token_source: HfTokenSource = field(default_factory=HfTokenSource)
    global_settings: SettingValueMap = field(default_factory=SettingValueMap)
    router_mode: bool = False
    selected_model_id: Optional[str] = None
    selected_profile_id: Optional[str] = None
    remote_monitor_enabled: bool = False
    remote_monitor_host: str = "127.0.0.1"
    remote_monitor_port: int = 8080

    def to_json(self) -> dict[str, Any]:
        return {
            "llama_server_path": self.llama_server_path,
            "models_dir": self.models_dir,
            "host": self.host,
            "port": self.port,
            "hf_token_source": self.hf_token_source.to_json(),
            "router_mode": self.router_mode,
            "selected_model_id": self.selected_model_id,
            "selected_profile_id": self.selected_profile_id,
            "global_settings": self.global_settings.to_json(),
            "remote_monitor_enabled": self.remote_monitor_enabled,
            "remote_monitor_host": self.remote_monitor_host,
            "remote_monitor_port": self.remote_monitor_port,
        }

    @classmethod
    def from_json(cls, data: Any) -> "AppConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            llama_server_path=data.get("llama_server_path") if isinstance(data.get("llama_server_path"), str) else None,
            models_dir=data.get("models_dir") if isinstance(data.get("models_dir"), str) else None,
            host=str(data.get("host") or "0.0.0.0"),
            port=int(data.get("port") or 8080),
            hf_token_source=HfTokenSource.from_json(data.get("hf_token_source")),
            global_settings=SettingValueMap.from_json(data.get("global_settings") or {}, LLAMA_OPTION_CATALOG),
            router_mode=bool(data.get("router_mode", False)),
            selected_model_id=data.get("selected_model_id") if isinstance(data.get("selected_model_id"), str) else None,
            selected_profile_id=data.get("selected_profile_id") if isinstance(data.get("selected_profile_id"), str) else None,
            remote_monitor_enabled=bool(data.get("remote_monitor_enabled", False)),
            remote_monitor_host=str(data.get("remote_monitor_host") or "127.0.0.1"),
            remote_monitor_port=int(data.get("remote_monitor_port") or 8080),
        )


def _first_mmproj(companion_paths: list[str]) -> Optional[str]:
    for p in companion_paths:
        if Path(p).name.lower().startswith("mmproj-"):
            return p
    return None

@dataclass
class LocalModel:
    id: str
    path: str
    size_bytes: Optional[int] = None
    hf_repo: Optional[str] = None
    hf_file: Optional[str] = None
    sha: Optional[str] = None
    quant: Optional[str] = None
    architecture: Optional[str] = None
    card_cache_path: Optional[str] = None
    license: Optional[str] = None
    base_model: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    gated: bool = False
    private: bool = False
    companion_paths: list[str] = field(default_factory=list)
    mmproj_path: Optional[str] = None
    last_used_at: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_path(cls, path: str) -> "LocalModel":
        p = Path(path)
        size = p.stat().st_size if p.exists() and p.is_file() else None
        return cls(id=str(p.resolve()), path=str(p), size_bytes=size)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("mmproj_path") is None:
            d.pop("mmproj_path", None)
        return d
    @classmethod
    def from_json(cls, data: Any) -> "LocalModel":
        if not isinstance(data, dict):
            raise ValueError("invalid LocalModel")
        allowed = set(cls.__dataclass_fields__.keys())
        cleaned = {k: v for k, v in data.items() if k in allowed}
        cleaned.setdefault("tags", [])
        cleaned.setdefault("companion_paths", [])
        if "mmproj_path" not in cleaned or cleaned.get("mmproj_path") is None:
            cleaned["mmproj_path"] = _first_mmproj(cleaned.get("companion_paths", []))
        return cls(**cleaned)

@dataclass
class ModelProfile:
    id: str
    model_id: str
    name: str
    settings: SettingValueMap = field(default_factory=SettingValueMap)
    raw_args: list[str] = field(default_factory=list)
    preset_origin: Optional[str] = None
    schema_version: Optional[str] = None
    is_default: bool = False
    last_used_at: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    user_set: set[str] = field(default_factory=set)

    def touch(self) -> None:
        self.updated_at = utc_now()

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model_id": self.model_id,
            "name": self.name,
            "settings": self.settings.to_json(),
            "raw_args": list(self.raw_args),
            "preset_origin": self.preset_origin,
            "schema_version": self.schema_version,
            "is_default": self.is_default,
            "last_used_at": self.last_used_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "user_set": sorted(self.user_set),
        }

    @classmethod
    def from_json(cls, data: Any) -> "ModelProfile":
        if not isinstance(data, dict):
            raise ValueError("invalid ModelProfile")
        settings = SettingValueMap.from_json(data.get("settings") or {}, LLAMA_OPTION_CATALOG)
        raw_user_set = data.get("user_set")
        if raw_user_set is not None:
            user_set = set(raw_user_set)
        else:
            # Migration: old profile without user_set — infer from settings
            user_set: set[str] = set()
            for opt_id, value in settings.items():
                opt = LLAMA_OPTION_CATALOG.get(opt_id)
                if opt is None:
                    continue
                if opt.default is not None and value.value == opt.default.value:
                    continue
                user_set.add(opt_id)
        # Migration: also clean raw_args of pre-Section-6 round-trip noise.
        raw_args = clean_raw_args(list(data.get("raw_args") or []))
        return cls(
            id=str(data["id"]),
            model_id=str(data["model_id"]),
            name=str(data.get("name") or "Default"),
            settings=settings,
            raw_args=raw_args,
            preset_origin=data.get("preset_origin"),
            schema_version=data.get("schema_version"),
            is_default=bool(data.get("is_default", False)),
            last_used_at=data.get("last_used_at"),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
            user_set=user_set,
        )

__all__ = ["AppConfig", "HfTokenSource", "LocalModel", "ModelProfile", "utc_now"]
