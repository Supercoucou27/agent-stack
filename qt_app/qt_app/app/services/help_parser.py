"""Parser for `llama-server --help` option text."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ParsedValueKind(str, Enum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    STRING_LIST = "string_list"


@dataclass
class ParsedOption:
    flags: list[str]
    value_name: Optional[str]
    kind: ParsedValueKind
    description: str
    group: str = "advanced"
    default: Optional[str] = None
    raw: str = ""

    @property
    def canonical_flag(self) -> str:
        long_flags = [f for f in self.flags if f.startswith("--")]
        return long_flags[0] if long_flags else self.flags[0]


def _infer_kind(value_name: Optional[str], description: str, flag: str) -> ParsedValueKind:
    if value_name is None:
        return ParsedValueKind.BOOLEAN
    token = value_name.lower()
    text = f"{token} {description.lower()} {flag.lower()}"
    if token in {"n", "count", "num", "port", "seed", "size", "n_threads", "n_gpu_layers"}:
        return ParsedValueKind.INTEGER
    if token in {"int", "integer", "number", "bytes", "mb"}:
        return ParsedValueKind.INTEGER
    if any(marker in text for marker in ("float", "temperature", "probability", "top-p", "min-p", "penalty")):
        return ParsedValueKind.FLOAT
    if re.search(r"\blist\b", text) or token.endswith("[]"):
        return ParsedValueKind.STRING_LIST
    return ParsedValueKind.STRING


def _infer_group(flag: str, description: str, current_section: Optional[str] = None) -> str:
    text = f"{flag} {description}".lower()
    flag_l = flag.lower()
    if flag_l in {"--no-mmap", "--mmap", "--mlock", "--numa"}:
        return "performance"
    if any(k in text for k in ("thread", "batch", "ubatch", "mmap", "mlock", "numa")):
        return "performance"
    if any(k in text for k in ("host", "port", "api", "cors", "ssl", "endpoint", "slot")):
        return "server_api"
    if any(k in text for k in ("temp", "top-p", "top-k", "min-p", "repeat", "penalty", "sampl")):
        return "sampling"
    if any(k in text for k in ("gpu", "cuda", "vulkan", "split", "offload")):
        return "gpu_offload"
    if any(k in text for k in ("ctx", "context", "kv", "cache")):
        return "context_kv"
    if any(k in text for k in ("model", "lora", "mmproj", "projector")):
        return "model_loading"
    if any(k in text for k in ("draft", "speculative", "lookup")):
        return "speculative"
    if any(k in text for k in ("verbose", "log", "debug", "dump")):
        return "debug"
    if current_section is not None:
        return _section_to_group(current_section)
    return "advanced"


# Section headers in ``llama-server --help`` are usually short labels like
# ``General options:`` or ``Sampling parameters``. Map the well-known ones
# to curated group ids; fall back to a slugified version for anything else.
_SECTION_MAP: dict[str, str] = {
    "general": "model_loading",
    "general options": "model_loading",
    "model loading": "model_loading",
    "sampling": "sampling",
    "sampling parameters": "sampling",
    "sampling defaults": "sampling",
    "server": "server_api",
    "server api": "server_api",
    "server / api": "server_api",
    "network": "server_api",
    "http server": "server_api",
    "performance": "performance",
    "gpu": "gpu_offload",
    "gpu offload": "gpu_offload",
    "context": "context_kv",
    "context size": "context_kv",
    "context / kv cache": "context_kv",
    "speculative": "speculative",
    "speculative decoding": "speculative",
    "multimodal": "model_loading",
    "logging": "debug",
    "log": "debug",
    "debug": "debug",
    "advanced": "advanced",
    "raw": "advanced",
}


def _section_to_group(section: str) -> str:
    key = section.strip().rstrip(":").lower()
    if key in _SECTION_MAP:
        return _SECTION_MAP[key]
    slug = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    return slug or "advanced"


def _extract_default(description: str) -> Optional[str]:
    match = re.search(r"\bdefault\s*[:=]\s*([^,;)\]]+)", description, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    bracket = re.search(r"\(default\s+([^)]*)\)", description, re.IGNORECASE)
    if bracket:
        return bracket.group(1).strip()
    return None


def _parse_option_head(head: str) -> tuple[list[str], Optional[str]]:
    chunks = [c.strip() for c in re.split(r",\s*", head) if c.strip()]
    flags: list[str] = []
    value_name: Optional[str] = None
    for chunk in chunks:
        parts = chunk.split()
        if not parts:
            continue
        flag = parts[0]
        if flag.startswith("-"):
            flags.append(flag)
            if len(parts) > 1 and value_name is None:
                value_name = parts[1].strip("<>[]")
    return flags, value_name


_OPTION_RE = re.compile(
    r"^\s{0,8}((?:-[\w?],\s*)?--?[\w][\w-]*(?:\s+[<\[]?[A-Za-z0-9_./:|\-]+[>\]]?)?(?:,\s*--?[\w][\w-]*(?:\s+[<\[]?[A-Za-z0-9_./:|\-]+[>\]]?)?)*)\s{2,}(.*)$"
)


_SECTION_HEADER_RE = re.compile(r"^[A-Z][\w/&()\-,:.' +]*[:#]?$")


def _looks_like_section_header(line: str) -> bool:
    """True if ``line`` is a top-level help section header like
    ``General options:`` or ``Sampling parameters`` (no leading whitespace,
    no flag token, ends with an optional colon).
    """
    stripped = line.strip()
    if not stripped:
        return False
    if line.startswith((" ", "\t")):
        return False
    if _OPTION_RE.match(stripped):
        return False
    if any(c in stripped for c in "{}[]<>|\\"):
        return False
    return bool(_SECTION_HEADER_RE.match(stripped))


def parse_help_options(help_text: str) -> list[ParsedOption]:
    """Parse option rows from llama-server help text.

    Handles the common shapes emitted by ``llama-server --help``:

    * short + long flag pairs: ``-m, --model FNAME        model path``
    * long-only options: ``      --no-mmap               disable mmap``
    * value placeholders: ``FNAME``, ``N``, ``HOST``, ``PORT``, ``F``
    * multi-line descriptions where the second and subsequent lines are
      indented past the description column
    * section headers (``General options:``) that label the following rows
      — the parser uses the header to disambiguate the ``group`` of options
      that don't carry an obvious keyword signal in their own text
    * ANSI colour codes (stripped before parsing)

    Each emitted :class:`ParsedOption` carries the flag aliases it was
    declared with, the placeholder token (or ``None`` for booleans), the
    inferred value kind, the joined description, the group, and the raw
    default string if one was declared inline.
    """
    options: list[ParsedOption] = []
    current: Optional[ParsedOption] = None
    current_section: Optional[str] = None

    for raw_line in help_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if _looks_like_section_header(line):
            current_section = line.strip()
            current = None
            continue
        match = _OPTION_RE.match(line)
        if match and "-" in match.group(1):
            flags, value_name = _parse_option_head(match.group(1))
            if not flags:
                continue
            desc = match.group(2).strip()
            canonical = next((f for f in flags if f.startswith("--")), flags[0])
            current = ParsedOption(
                flags=flags,
                value_name=value_name,
                kind=_infer_kind(value_name, desc, canonical),
                description=desc,
                group=_infer_group(canonical, desc, current_section),
                default=_extract_default(desc),
                raw=line.strip(),
            )
            options.append(current)
            continue

        if current is not None and (raw_line.startswith(" ") or raw_line.startswith("\t")):
            continuation = line.strip()
            if continuation and not continuation.startswith("-"):
                current.description = f"{current.description} {continuation}".strip()
                current.default = current.default or _extract_default(current.description)
                current.raw = f"{current.raw}\n{line}"

    return options


__all__ = ["ParsedOption", "ParsedValueKind", "parse_help_options"]
