"""Runtime option schema overlay for selected llama-server binaries."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from llama_data.llama_options import LLAMA_OPTION_CATALOG, LlamaOption, LlamaOptionCatalog
from llama_data.paths import DataPaths, default_paths
from llama_data.storage import VersionedEnvelope, current_version, load_envelope, save_envelope

from .help_parser import ParsedOption, parse_help_options
from .llama_server import LlamaServerProbe, validate_llama_server


@dataclass
class BinaryKey:
    path: str
    size_bytes: Optional[int]
    mtime_ns: Optional[int]
    version: Optional[str]

    @classmethod
    def from_path(cls, path: str, version: Optional[str]) -> "BinaryKey":
        p = Path(path).expanduser()
        try:
            st = p.stat()
            size = st.st_size
            mtime = st.st_mtime_ns
        except OSError:
            size = None
            mtime = None
        return cls(path=str(p), size_bytes=size, mtime_ns=mtime, version=version)

    def cache_id(self) -> str:
        raw = f"{self.path}|{self.size_bytes}|{self.mtime_ns}|{self.version or ''}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:24]


@dataclass
class RuntimeOption:
    id: str
    flag: str
    flags: list[str]
    label: str
    group: str
    kind: str
    description: str
    supported: bool
    curated: bool
    default: Optional[str] = None
    raw_help: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RuntimeOption":
        return cls(**data)


@dataclass
class RuntimeSchema:
    binary: BinaryKey
    options: list[RuntimeOption] = field(default_factory=list)
    parsed_count: int = 0
    curated_supported_count: int = 0
    unknown_count: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "binary": asdict(self.binary),
            "options": [o.to_json() for o in self.options],
            "parsed_count": self.parsed_count,
            "curated_supported_count": self.curated_supported_count,
            "unknown_count": self.unknown_count,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RuntimeSchema":
        return cls(
            binary=BinaryKey(**data["binary"]),
            options=[RuntimeOption.from_json(o) for o in data.get("options", [])],
            parsed_count=int(data.get("parsed_count", 0)),
            curated_supported_count=int(data.get("curated_supported_count", 0)),
            unknown_count=int(data.get("unknown_count", 0)),
        )


def _catalog_by_flag(catalog: LlamaOptionCatalog) -> dict[str, LlamaOption]:
    out: dict[str, LlamaOption] = {}
    for option in catalog:
        out[option.flag] = option
        for alias in option.aliases:
            out[alias] = option
    return out


def _runtime_from_curated(option: LlamaOption, parsed: ParsedOption) -> RuntimeOption:
    return RuntimeOption(
        id=option.id,
        flag=option.flag,
        flags=list(parsed.flags),
        label=option.label,
        group=option.group,
        kind=option.kind.value,
        description=option.help_text,
        supported=True,
        curated=True,
        default=parsed.default,
        raw_help=parsed.raw,
    )


def _runtime_from_unknown(parsed: ParsedOption) -> RuntimeOption:
    flag = parsed.canonical_flag
    return RuntimeOption(
        id=f"unknown:{flag}",
        flag=flag,
        flags=list(parsed.flags),
        label=flag,
        group=parsed.group or "advanced",
        kind=parsed.kind.value,
        description=parsed.description,
        supported=True,
        curated=False,
        default=parsed.default,
        raw_help=parsed.raw,
    )


def merge_parsed_options(parsed_options: list[ParsedOption], catalog: LlamaOptionCatalog = LLAMA_OPTION_CATALOG) -> list[RuntimeOption]:
    by_flag = _catalog_by_flag(catalog)
    seen_ids: set[str] = set()
    merged: list[RuntimeOption] = []

    for parsed in parsed_options:
        option = next((by_flag.get(flag) for flag in parsed.flags if by_flag.get(flag) is not None), None)
        if option is not None:
            if option.id in seen_ids:
                continue
            seen_ids.add(option.id)
            merged.append(_runtime_from_curated(option, parsed))
        else:
            runtime = _runtime_from_unknown(parsed)
            if runtime.id not in seen_ids:
                seen_ids.add(runtime.id)
                merged.append(runtime)

    return merged


def build_runtime_schema(path: str) -> tuple[LlamaServerProbe, RuntimeSchema]:
    probe = validate_llama_server(path)
    help_text = probe.help_probe.combined_output()
    parsed = parse_help_options(help_text)
    options = merge_parsed_options(parsed)
    binary = BinaryKey.from_path(probe.path, probe.version)
    curated_count = sum(1 for o in options if o.curated)
    unknown_count = sum(1 for o in options if not o.curated)
    return probe, RuntimeSchema(
        binary=binary,
        options=options,
        parsed_count=len(parsed),
        curated_supported_count=curated_count,
        unknown_count=unknown_count,
    )


class SchemaCache:
    def __init__(self, paths: Optional[DataPaths] = None) -> None:
        self.paths = paths or default_paths()

    @property
    def cache_dir(self) -> Path:
        return self.paths.data_dir / "schemas"

    def path_for(self, binary: BinaryKey) -> Path:
        return self.cache_dir / f"{binary.cache_id()}.json"

    def save(self, schema: RuntimeSchema) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        save_envelope(self.path_for(schema.binary), VersionedEnvelope(current_version(), schema.to_json()))

    def load(self, binary: BinaryKey) -> Optional[RuntimeSchema]:
        envelope = load_envelope(self.path_for(binary))
        if envelope is None or not isinstance(envelope.data, dict):
            return None
        return RuntimeSchema.from_json(envelope.data)


__all__ = [
    "BinaryKey",
    "RuntimeOption",
    "RuntimeSchema",
    "SchemaCache",
    "build_runtime_schema",
    "merge_parsed_options",
]
