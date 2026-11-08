"""On-disk layout for the llamaUI data layer.

The Qt app is local-first; everything it persists lives under a single data
directory. We honor the XDG Base Directory spec on Linux, fall back to the
platform-conventional location elsewhere, and let tests inject a tempdir so
the stores can be exercised without touching the user's real config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


APP_DIR_NAME = "llamaUI"
CONFIG_FILE = "config.json"
PROFILES_FILE = "profiles.json"
LIBRARY_FILE = "library.json"
CARDS_DIR = "cards"
SCHEMA_CACHE_FILE = "schema_cache.json"


@dataclass(frozen=True)
class DataPaths:
    """Resolved on-disk locations for the app's persistent state."""

    data_dir: Path
    config_path: Path
    profiles_path: Path
    library_path: Path
    cards_dir: Path
    schema_cache_path: Path

    def ensure(self) -> None:
        """Create all directories this path set references."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cards_dir.mkdir(parents=True, exist_ok=True)


def _platform_default_data_dir() -> Path:
    """XDG_DATA_HOME on Linux/macOS, %LOCALAPPDATA% on Windows. No Qt involved."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
        return Path.home() / "AppData" / "Local" / APP_DIR_NAME
    # POSIX: XDG applies on Linux, macOS has no formal spec but the
    # XDG_DATA_HOME env var is widely understood.
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def default_data_dir() -> Path:
    """The default data dir for the current user/platform."""
    return _platform_default_data_dir()


def default_paths(data_dir: Optional[Path] = None) -> DataPaths:
    """Build a :class:`DataPaths` rooted at ``data_dir`` (or the platform default)."""
    root = Path(data_dir) if data_dir is not None else default_data_dir()
    return DataPaths(
        data_dir=root,
        config_path=root / CONFIG_FILE,
        profiles_path=root / PROFILES_FILE,
        library_path=root / LIBRARY_FILE,
        cards_dir=root / CARDS_DIR,
        schema_cache_path=root / SCHEMA_CACHE_FILE,
    )


__all__ = [
    "APP_DIR_NAME",
    "CONFIG_FILE",
    "PROFILES_FILE",
    "LIBRARY_FILE",
    "CARDS_DIR",
    "SCHEMA_CACHE_FILE",
    "DataPaths",
    "default_data_dir",
    "default_paths",
]
