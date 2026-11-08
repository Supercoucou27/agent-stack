"""Versioned JSON envelope and atomic persistence.

Each on-disk JSON file written by this layer carries:

    {
        "version": <int>,
        "data": <payload>
    }

The ``Migration`` registry on every store knows how to step any older payload
forward to ``CURRENT_SCHEMA_VERSION``. Adding a field is a 0 -> 1 migration
(``_migrate_v0_to_v1``); renaming a field needs an explicit 1 -> 2 step.

Writes are atomic: the JSON is written to ``<path>.tmp`` and renamed into place
with :func:`os.replace`, so a crash mid-write never leaves a half-truncated
config on disk.
"""

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

CURRENT_SCHEMA_VERSION: int = 2


@dataclass(frozen=True)
class VersionedEnvelope:
    """A persisted payload tagged with its schema version."""

    version: int
    data: Any

    def to_dict(self) -> Dict[str, Any]:
        return {"version": self.version, "data": self.data}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "VersionedEnvelope":
        if not isinstance(payload, dict) or "version" not in payload:
            raise ValueError("envelope missing 'version' field")
        version = int(payload["version"])
        return cls(version=version, data=payload.get("data"))


#: A migration takes the previous-version payload and returns the next-version
#: payload. The registry below is keyed by the *source* version (i.e. migrators
#: advance one version at a time, oldest first).
Migration = Callable[[Any], Any]


@dataclass
class MigrationChain:
    """Ordered list of migrations; ``apply`` walks from ``start`` to ``target``."""

    migrations: Dict[int, Migration]
    target: int

    def apply(self, start_version: int, data: Any) -> Any:
        version = start_version
        current = data
        while version < self.target:
            step = self.migrations.get(version)
            if step is None:
                raise ValueError(
                    f"no migration from version {version} to {version + 1} "
                    f"(target {self.target})"
                )
            current = step(current)
            version += 1
        return current


def _read_json_object(path: Path) -> Optional[Dict[str, Any]]:
    """Read a JSON object from ``path``; return None on missing/empty/invalid file."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def load_envelope(path: Path) -> Optional[VersionedEnvelope]:
    """Load a :class:`VersionedEnvelope` from ``path``; return None if missing/invalid."""
    obj = _read_json_object(path)
    if obj is None:
        return None
    try:
        return VersionedEnvelope.from_dict(obj)
    except (ValueError, TypeError):
        return None


def save_envelope(path: Path, envelope: VersionedEnvelope) -> None:
    """Atomically write ``envelope`` to ``path``.

    The parent directory is created if needed. The file is written via a
    temporary sibling and renamed into place so concurrent readers never see
    a half-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(envelope.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
    # Use NamedTemporaryFile in the same directory so os.replace is atomic on
    # POSIX (same filesystem) and Windows (same volume). delete=False because
    # we explicitly close + rename.
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        # Best-effort cleanup; never leave tmp files lying around.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def resolve_version(
    envelope: VersionedEnvelope,
    chain: MigrationChain,
) -> Any:
    """Run the migration chain to bring ``envelope.data`` up to ``chain.target``."""
    return chain.apply(envelope.version, envelope.data)


# -- Cross-process advisory locking -------------------------------------------
# Two llamaUI processes launched simultaneously would otherwise clobber each
# other's writes. The stores hold a per-file advisory lock for the full
# load+save cycle. On Windows the lock is a no-op (single-instance assumed).
_HAS_FCNTL = sys.platform != "win32"
if _HAS_FCNTL:
    import fcntl  # type: ignore[import-not-found]


class FileLock:
    """Advisory file lock used to serialize multi-process access to a store.

    On POSIX this is an ``flock`` on a sidecar ``.lock`` file. On Windows
    it is a no-op (the app documents single-instance behavior there).
    The lock is released when the context exits, even on exceptions.
    """

    def __init__(self, path: Path) -> None:
        self._path = path.with_suffix(path.suffix + ".lock")
        self._fd: Optional[int] = None

    def __enter__(self) -> "FileLock":
        if not _HAS_FCNTL:
            return self
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
        finally:
            self._fd = None


# Module-level helpers used by the migration self-test below.
def _strip_unknown_keys(payload: Any, allowed: set) -> Any:
    """Recursively drop unknown mapping keys. Used by 0->1 migrations."""
    if isinstance(payload, dict):
        return {k: _strip_unknown_keys(v, allowed) for k, v in payload.items() if k in allowed}
    if isinstance(payload, list):
        return [_strip_unknown_keys(item, allowed) for item in payload]
    return payload


def identity_migration(payload: Any) -> Any:
    """The 0->1 migration is a no-op for fresh installs."""
    return payload


# Forward-declared migration registry per store. Each store builds its own
# ``MigrationChain`` from these. The library has a real 1→2 migration
# (drops companion entries); config and profiles use empty dicts.
EMPTY_MIGRATIONS: Dict[int, Migration] = {}


def current_version() -> int:
    return CURRENT_SCHEMA_VERSION


# ----------------------------------------------------------------------
# Self-test (only runs when executed directly)
# ----------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import sys
    from pathlib import Path as _P

    p = _P(tempfile.gettempdir()) / "_envelope_test.json"
    if p.exists():
        p.unlink()
    save_envelope(p, VersionedEnvelope(version=1, data={"hello": "world"}))
    loaded = load_envelope(p)
    assert loaded is not None and loaded.version == 1 and loaded.data == {"hello": "world"}, loaded
    chain = MigrationChain(migrations={0: identity_migration}, target=1)
    assert resolve_version(VersionedEnvelope(version=0, data={"a": 1}), chain) == {"a": 1}
    p.unlink()
    print("storage self-test ok", file=sys.stderr)
