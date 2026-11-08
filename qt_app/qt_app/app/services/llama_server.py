"""llama-server binary validation and safe introspection."""
from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class CommandProbe:
    argv: list[str]
    exit_code: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool

    def combined_output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part).strip()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LlamaServerProbe:
    path: str
    exists: bool
    is_file: bool
    is_executable: bool
    size_bytes: Optional[int]
    version: Optional[str]
    version_probe: CommandProbe
    help_probe: CommandProbe
    looks_like_llama_cpp: bool
    probed_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def _empty_probe(path: str, args: list[str]) -> CommandProbe:
    return CommandProbe(argv=[path, *args], exit_code=None, stdout="", stderr="", timed_out=False)


def _run_probe(path: str, args: list[str], timeout: float = 5.0) -> CommandProbe:
    argv = [path, *args]
    try:
        result = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return CommandProbe(
            argv=argv,
            exit_code=result.returncode,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            timed_out=False,
        )
    except subprocess.TimeoutExpired as e:
        return CommandProbe(
            argv=argv,
            exit_code=None,
            stdout=(e.stdout or "").strip() if isinstance(e.stdout, str) else "",
            stderr=(e.stderr or "").strip() if isinstance(e.stderr, str) else "",
            timed_out=True,
        )
    except OSError as e:
        return CommandProbe(argv=argv, exit_code=None, stdout="", stderr=str(e), timed_out=False)


def _is_executable_file(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) and os.access(path, os.X_OK)


def _extract_version(output: str) -> Optional[str]:
    for line in output.splitlines():
        clean = line.strip()
        if not clean:
            continue
        lower = clean.lower()
        if "llama" in lower or "ggml" in lower or "version" in lower or "build" in lower:
            return clean[:240]
    first = output.strip().splitlines()
    return first[0][:240] if first else None


def validate_llama_server(path: str, timeout: float = 5.0) -> LlamaServerProbe:
    """Validate and lightly introspect a user-selected llama-server binary."""
    raw_path = str(path or "").strip()
    p = Path(raw_path).expanduser()
    exists = p.exists()
    is_file = p.is_file()
    is_executable = _is_executable_file(p)
    size_bytes: Optional[int] = None
    if exists:
        try:
            size_bytes = p.stat().st_size
        except OSError:
            size_bytes = None

    if exists and is_file and is_executable:
        version_probe = _run_probe(str(p), ["--version"], timeout=timeout)
        help_probe = _run_probe(str(p), ["--help"], timeout=timeout)
    else:
        version_probe = _empty_probe(str(p), ["--version"])
        help_probe = _empty_probe(str(p), ["--help"])

    combined = "\n".join(
        part
        for part in (
            version_probe.combined_output(),
            help_probe.combined_output(),
            p.name,
        )
        if part
    )
    lower = combined.lower()
    looks_like = "llama" in lower or "ggml" in lower or "gguf" in lower
    version = _extract_version(version_probe.combined_output()) or _extract_version(help_probe.combined_output())

    return LlamaServerProbe(
        path=str(p),
        exists=exists,
        is_file=is_file,
        is_executable=is_executable,
        size_bytes=size_bytes,
        version=version,
        version_probe=version_probe,
        help_probe=help_probe,
        looks_like_llama_cpp=looks_like,
        probed_at=datetime.now(timezone.utc).isoformat(),
    )


__all__ = ["CommandProbe", "LlamaServerProbe", "validate_llama_server"]
