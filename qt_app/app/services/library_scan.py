"""Library scan service: scan configured models directory for GGUF files.

Companion-file detection is **filename-based only** — we do not inspect
the GGUF header. A user who renames ``mmproj-llava.gguf`` to
``llava-q4.gguf`` will see it appear as a runnable model. The Settings
page exposes a "Show companion files" toggle for users who want to
manage these files alongside their primary models.

Adding GGUF-header inspection (reading ``general.file_type`` from the
metadata) would improve accuracy but is out of scope here.
"""

from __future__ import annotations
import logging
import os
import re
import subprocess
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from llama_data import AppConfig, ConfigStore, LibraryStore, LocalModel

logger = logging.getLogger(__name__)

# Reuse the same quant-detection regex as the HF service.
_QUANT_RE = re.compile(
    r"(?:^|[-_.])(Q\d(?:_[A-Z0-9]+)*(?:_[A-Z0-9]+)?|IQ\d_[A-Z0-9]+|F16|BF16|F32)(?:[-_.]|$)",
    re.IGNORECASE,
)


def infer_quant(filename: str) -> Optional[str]:
    """Infer quantization type from a GGUF filename."""
    match = _QUANT_RE.search(filename)
    return match.group(1).upper() if match else None


# Substrings that identify companion / non-runnable GGUF files.
_COMPANION_PREFIXES = (
    "mmproj-", "mmproj.",
    "text-encoder-", "text-encoder.",
    "vision-encoder-", "vision-encoder.",
)


def _is_companion_name(lower_name: str) -> bool:
    """True if *lower_name* (already lowercased) matches a companion GGUF pattern."""
    for prefix in _COMPANION_PREFIXES:
        if prefix in lower_name:
            return True
    # "embedding" substring, but NOT when preceded by "_".
    if "embedding" in lower_name:
        idx = 0
        while True:
            pos = lower_name.find("embedding", idx)
            if pos == -1:
                break
            if pos == 0 or lower_name[pos - 1] != "_":
                return True
            idx = pos + len("embedding")
    return False


def is_companion_gguf(path: Path) -> bool:
    """True for mmproj, text-encoder, vision-encoder, and embedding GGUFs."""
    return _is_companion_name(path.name.lower())

def _companions_for_path(path: Path) -> list[str]:
    """Return all companion GGUFs in the same directory as *path*."""
    out: list[str] = []
    try:
        for child in path.parent.iterdir():
            if child.suffix.lower() == ".gguf" and is_companion_gguf(child):
                out.append(str(child.resolve()))
    except OSError:
        pass
    out.sort()
    return out


def _mmproj_for_path(path: Path) -> Optional[str]:
    """Return the first mmproj-*.gguf file in the same directory, or None."""
    try:
        for child in path.parent.iterdir():
            if child.suffix.lower() == ".gguf" and child.name.lower().startswith("mmproj-"):
                return str(child.resolve())
    except OSError:
        pass
    return None


def _is_primary_runnable_gguf(path: Path) -> bool:
    name = path.name.lower()
    if re.search(r"-\d{5}-of-\d{5}\.gguf$", name):
        return name.endswith("-00001-of-0000" + name.split("-of-")[-1][0] + ".gguf") if False else name.endswith("-00001-of-00001.gguf") or "-00001-of-" in name
    if re.search(r"\.part\d+\.gguf$", name):
        return ".part1.gguf" in name or ".part01.gguf" in name or ".part001.gguf" in name
    if _is_companion_name(name):
        return False
    return True


@dataclass(frozen=True)
class ScanResult:
    """Summary of a library scan operation."""
    scanned_files: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    kept: int = 0
    error: Optional[str] = None

    @property
    def total_models(self) -> int:
        return self.added + self.updated + self.kept


def scan_library(
    models_dir: Path,
    library: LibraryStore,
) -> ScanResult:
    """Scan *models_dir* for ``.gguf`` files and upsert into *library*.

    For each ``.gguf`` file found:
    - Compute a stable id from the resolved path.
    - Infer quantization from the filename.
    - Record file size.
    - If a :class:`LocalModel` already exists with that id, preserve its HF
      metadata (repo, file, sha, card_cache_path, license, base_model, tags,
      gated, private) and only update local fields (size_bytes, quant).
    - If no existing entry, create a fresh one.

    After scanning, remove any library entries whose ``path`` no longer exists
    on disk *and* whose path was under *models_dir* (don't delete manually
    added entries from other locations).

    Returns a :class:`ScanResult` summary.
    """
    models_dir = models_dir.resolve()

    if not models_dir.is_dir():
        logger.warning("Models directory does not exist: %s", models_dir)
        return ScanResult(error=f"Directory not found: {models_dir}")

    # Collect all complete .gguf files on disk.
    disk_files: dict[str, Path] = {}  # resolved path -> Path
    for gguf in models_dir.rglob("*.gguf"):
        if not _is_primary_runnable_gguf(gguf):
            continue
        resolved = gguf.resolve()
        disk_files[str(resolved)] = resolved
    # Load existing library entries.
    existing = {m.id: m for m in library.load()}

    added = 0
    updated = 0
    kept = 0

    for resolved_str, resolved_path in disk_files.items():
        size_bytes: Optional[int] = None
        try:
            size_bytes = resolved_path.stat().st_size
        except OSError:
            pass

        filename = resolved_path.name
        quant = infer_quant(filename)

        quant = infer_quant(filename)
        companions = _companions_for_path(resolved_path)
        mmproj = _mmproj_for_path(resolved_path)

        if resolved_str in existing:
            old = existing[resolved_str]
            # Preserve all HF metadata; update local fields only.
            updates: dict = {}
            if old.size_bytes != size_bytes:
                updates["size_bytes"] = size_bytes
            if old.quant != quant and quant is not None:
                updates["quant"] = quant
            if old.companion_paths != companions:
                updates["companion_paths"] = companions
            if old.mmproj_path != mmproj:
                updates["mmproj_path"] = mmproj
            if updates:
                from dataclasses import asdict
                fields = asdict(old)
                fields.update(updates)
                fields["updated_at"] = LocalModel.__dataclass_fields__["updated_at"].default_factory()
                existing[resolved_str] = LocalModel(**fields)
                updated += 1
            else:
                kept += 1
        else:
            model = LocalModel(
                id=resolved_str,
                path=str(resolved_path),
                size_bytes=size_bytes,
                quant=quant,
                companion_paths=companions,
                mmproj_path=mmproj,
            )
            existing[resolved_str] = model
            added += 1

    # Remove stale entries: those whose path is under models_dir but file is gone.
    removed = 0
    to_keep: dict[str, LocalModel] = {}
    for mid, model in existing.items():
        model_path = Path(model.path).resolve()
        model_path_resolved = str(model_path)
        try:
            under_models_dir = model_path.is_relative_to(models_dir)
        except ValueError:
            under_models_dir = False
        if under_models_dir and model_path_resolved not in disk_files:
            removed += 1
        else:
            to_keep[mid] = model

    library.save(to_keep.values())

    result = ScanResult(
        scanned_files=len(disk_files),
        added=added,
        updated=updated,
        removed=removed,
        kept=kept,
    )
    logger.info(
        "Library scan complete: %d files, %d added, %d updated, %d removed, %d kept",
        result.scanned_files, result.added, result.updated, result.removed, result.kept,
    )
    return result


def scan_models_dir(config: ConfigStore, library: LibraryStore) -> ScanResult:
    """High-level scan: read models_dir from config, run scan, return result.

    If models_dir is not configured or does not exist, returns an error result.
    """
    cfg = config.load()
    models_dir = cfg.models_dir
    if not models_dir:
        return ScanResult(error="Models directory not configured. Set it in Settings first.")
    return scan_library(Path(models_dir).expanduser(), library)


def read_card_cache(card_cache_path: Optional[str]) -> Optional[str]:
    """Read a cached model card markdown file. Returns None if missing."""
    if not card_cache_path:
        return None
    path = Path(card_cache_path)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def reveal_file(path: str) -> None:
    """Open the system file manager pointing at *path*."""
    p = Path(path)
    if not p.exists():
        return
    try:
        if os.name == "posix":
            subprocess.Popen(
                ["xdg-open", str(p.parent)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            webbrowser.open(str(p.parent))
    except OSError:
        logger.debug("Failed to reveal file: %s", path, exc_info=True)


def open_hf(hf_repo: Optional[str]) -> None:
    """Open the HuggingFace model page in the system browser."""
    if not hf_repo:
        return
    url = f"https://huggingface.co/{hf_repo}"
    try:
        webbrowser.open(url)
    except OSError:
        logger.debug("Failed to open HF page: %s", url, exc_info=True)


__all__ = [
    "ScanResult",
    "infer_quant",
    "is_companion_gguf",
    "open_hf",
    "read_card_cache",
    "reveal_file",
    "scan_library",
    "scan_models_dir",
]
