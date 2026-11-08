"""HuggingFace GGUF search and model metadata service."""
from __future__ import annotations

import concurrent.futures
from html.parser import HTMLParser
import json
import re
import time
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Iterable, List, Optional, Protocol

HF_API = "https://huggingface.co/api"
HF_WEB = "https://huggingface.co"
_QUANT_RE = re.compile(r"(?:^|[-_.])(Q\d(?:_[A-Z0-9]+)*(?:_[A-Z0-9]+)?|IQ\d_[A-Z0-9]+|F16|BF16|F32)(?:[-_.]|$)", re.IGNORECASE)

_PARAM_RE = re.compile(r"(?:^|[-_.])(\d+(?:\.\d+)?)\s*([bm])(?:[-_.]|$)", re.IGNORECASE)
_CONTEXT_WINDOWS = (16_384, 32_768, 65_536, 131_072)
_GPU_HEADROOM = 0.88
_RAM_HEADROOM = 0.78


@dataclass(frozen=True)
class HardwareProfile:
    ram_bytes: Optional[int] = None
    vram_bytes: Optional[int] = None
    cpu_threads: int = 1


@dataclass(frozen=True)
class ContextFit:
    context_tokens: int
    kv_cache_bytes: int
    required_bytes: int
    tier: str

@dataclass(frozen=True)
class MoeFit:
    total_experts: int
    active_experts: int
    gpu_experts_estimate: int
    active_gpu_experts_estimate: int
    note: str


@dataclass(frozen=True)
class FitRecommendation:
    settings: dict[str, object]
    rationale: str


@dataclass(frozen=True)
class HardwareFit:
    label: str
    score: int
    model_bytes: int
    projector_bytes: int
    mtp_bytes: int
    quantization: Optional[str]
    parameter_count: Optional[int]
    moe: Optional[MoeFit]
    contexts: tuple[ContextFit, ...]

    def summary(self) -> str:
        return f"{self.label} ({self.quantization})" if self.quantization else self.label

    def detail(self) -> str:
        lines = [
            f"Hardware fit: {self.summary()}",
            f"Weights: {_format_bytes(self.model_bytes)}"
            + (f" · quant {self.quantization}" if self.quantization else "")
            + (f" + projector {_format_bytes(self.projector_bytes)}" if self.projector_bytes else "")
            + (f" + MTP/draft {_format_bytes(self.mtp_bytes)}" if self.mtp_bytes else ""),
        ]
        if self.parameter_count:
            lines.append(f"Estimated params: {_format_params(self.parameter_count)}")
        if self.moe:
            lines.append(
                f"MoE: {self.moe.total_experts} experts / {self.moe.active_experts} active · "
                f"~{self.moe.gpu_experts_estimate} experts resident on GPU "
                f"(~{self.moe.active_gpu_experts_estimate} active) · {self.moe.note}"
            )
        for ctx in self.contexts:
            lines.append(
                f"{ctx.context_tokens // 1024}K ctx: {ctx.tier} · "
                f"KV {_format_bytes(ctx.kv_cache_bytes)} · total {_format_bytes(ctx.required_bytes)}"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class HfFilter:
    key: str
    label: str
    values: List[str] = field(default_factory=list)

    def is_active(self) -> bool:
        return bool(self.values)


@dataclass(frozen=True)
class HfFile:
    name: str
    size_bytes: Optional[int] = None
    quantization: Optional[str] = None
    context_length: Optional[int] = None
    is_multimodal_projector: bool = False
    is_split: bool = False
    download_url: Optional[str] = None


@dataclass(frozen=True)
class HfRepoSummary:
    repo_id: str
    author: str
    downloads: int = 0
    likes: int = 0
    tags: List[str] = field(default_factory=list)
    gated: bool = False
    private: bool = False
    license: Optional[str] = None
    architecture: Optional[str] = None
    base_model: Optional[str] = None
    files: List[HfFile] = field(default_factory=list)
    hardware_fit: Optional[HardwareFit] = None
    card_text: Optional[str] = None

    def total_size_bytes(self) -> int:
        return sum(f.size_bytes or 0 for f in self.files)


@dataclass(frozen=True)
class HfSearchOutcome:
    status: str
    repos: List[HfRepoSummary] = field(default_factory=list)
    message: Optional[str] = None

    @classmethod
    def empty(cls, message: str) -> "HfSearchOutcome":
        return cls(status="empty", message=message)

    @classmethod
    def error(cls, message: str) -> "HfSearchOutcome":
        return cls(status="error", message=message)

    @classmethod
    def ok(cls, repos: Iterable[HfRepoSummary]) -> "HfSearchOutcome":
        repos_list = list(repos)
        if not repos_list:
            return cls.empty("Search returned no GGUF models.")
        return cls(status="ok", repos=repos_list)


class HfSearchService(Protocol):
    def search(self, query: str, filters: Iterable[HfFilter]) -> HfSearchOutcome: ...


class HuggingFaceSearchService:
    def __init__(self, token: Optional[str] = None, timeout: float = 20.0, limit: int = 25):
        self.token = token
        self.timeout = timeout
        self.limit = limit

    def search(self, query: str, filters: Iterable[HfFilter] = ()) -> HfSearchOutcome:
        query = (query or "").strip()
        if not query:
            return HfSearchOutcome.empty("Enter a model search query.")
        try:
            repos = self._search_repos(query)
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                hydrated = list(pool.map(self._hydrate_repo, repos))
            return HfSearchOutcome.ok(repo for repo in hydrated if repo.files)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return HfSearchOutcome.error("HuggingFace authentication required or token rejected. Save a valid token in Settings.")
            if exc.code == 429:
                return HfSearchOutcome.error("HuggingFace rate limit reached. Try again later.")
            return HfSearchOutcome.error(f"HuggingFace HTTP {exc.code}: {exc.reason}")
        except (urllib.error.URLError, OSError) as exc:
            # Avoid echoing the full URL (which contains the search query
            # and any repo IDs the user typed) back into the UI.
            return HfSearchOutcome.error(f"HuggingFace request failed: {exc.reason if hasattr(exc, 'reason') and exc.reason else 'network error'}")
        except Exception as exc:
            return HfSearchOutcome.error(f"HuggingFace search failed: {type(exc).__name__}")

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "llamaUI-qt/0.1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _json(self, url: str):
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _search_repos(self, query: str) -> list[dict]:
        params = urllib.parse.urlencode({
            "filter": "gguf",
            "search": query,
            "sort": "downloads",
            "direction": "-1",
            "limit": str(self.limit),
            "full": "true",
        })
        data = self._json(f"{HF_API}/models?{params}")
        return data if isinstance(data, list) else []

    def _detail(self, repo_id: str) -> dict:
        safe = urllib.parse.quote(repo_id, safe="/")
        data = self._json(f"{HF_API}/models/{safe}")
        return data if isinstance(data, dict) else {}

    def _tree(self, repo_id: str) -> list[dict]:
        safe = urllib.parse.quote(repo_id, safe="/")
        try:
            data = self._json(f"{HF_API}/models/{safe}/tree/main?recursive=true")
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def _hydrate_repo(self, raw: dict) -> HfRepoSummary:
        repo_id = str(raw.get("modelId") or raw.get("id") or "")
        detail = raw
        siblings = raw.get("siblings") or []
        if not siblings or not any(str(s.get("rfilename", "")).lower().endswith(".gguf") for s in siblings):
            detail = {**raw, **self._detail(repo_id)}
        revision = str(detail.get("defaultBranch") or detail.get("default_branch") or "main")
        files = self._files(repo_id, detail, revision)
        if any(f.size_bytes is None for f in files):
            by_name = {str(item.get("path") or item.get("rfilename") or ""): item for item in self._tree(repo_id)}
            files = [self._with_tree_size(file, by_name.get(file.name)) for file in files]
        card_data = detail.get("cardData") or detail.get("card_data") or {}
        tags = [str(t) for t in detail.get("tags") or []]
        license_name = card_data.get("license") if isinstance(card_data, dict) else None
        base_model = card_data.get("base_model") if isinstance(card_data, dict) else None
        if isinstance(base_model, list):
            base_model = next((str(x) for x in base_model if x), None)
        architecture = None
        config = detail.get("config")
        if isinstance(config, dict):
            arch = config.get("architectures")
            architecture = config.get("model_type") or (arch[0] if isinstance(arch, list) and arch else None)
        return HfRepoSummary(
            repo_id=repo_id,
            author=str(detail.get("author") or repo_id.split("/", 1)[0]),
            downloads=int(detail.get("downloads") or 0),
            likes=int(detail.get("likes") or 0),
            tags=tags,
            gated=bool(detail.get("gated")),
            private=bool(detail.get("private")),
            license=str(license_name) if license_name else None,
            architecture=str(architecture) if architecture else None,
            base_model=str(base_model) if base_model else None,
            files=files,
            hardware_fit=self._hardware_fit(files),
            card_text=None,
        )

    def _files(self, repo_id: str, detail: dict, revision: str) -> list[HfFile]:
        out: list[HfFile] = []
        for sibling in detail.get("siblings") or []:
            name = str(sibling.get("rfilename") or sibling.get("path") or "")
            lower = name.lower()
            if not lower.endswith(".gguf") and "mmproj" not in lower:
                continue
            lfs = sibling.get("lfs") if isinstance(sibling.get("lfs"), dict) else {}
            size = sibling.get("size") or lfs.get("size")
            out.append(HfFile(
                name=name,
                size_bytes=int(size) if isinstance(size, int) else None,
                quantization=_quant(name),
                is_multimodal_projector="mmproj" in lower,
                is_split=_is_split(name),
                download_url=f"{HF_WEB}/{urllib.parse.quote(repo_id, safe='/')}/resolve/{urllib.parse.quote(revision, safe='')}/{urllib.parse.quote(name, safe='/')}",
            ))
        out.sort(key=lambda f: (f.is_multimodal_projector, f.name.lower()))
        return out

    def _with_tree_size(self, file: HfFile, tree_item: Optional[dict]) -> HfFile:
        if not tree_item:
            return file
        lfs = tree_item.get("lfs") if isinstance(tree_item.get("lfs"), dict) else {}
        size = tree_item.get("size") or lfs.get("size")
        return replace(file, size_bytes=size) if isinstance(size, int) else file

    def fetch_card_text(self, repo_id: str) -> Optional[str]:
        safe = urllib.parse.quote(repo_id, safe="/")
        try:
            req = urllib.request.Request(f"{HF_WEB}/{safe}/raw/main/README.md", headers=self._headers())
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return normalize_model_card_markdown(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            return None


    def _hardware_fit(self, files: list[HfFile]) -> Optional[HardwareFit]:
        return compute_hardware_fit(files)

class _MarkdownHtmlNormalizer(HTMLParser):
    """Convert README HTML islands into Markdown QTextBrowser renders reliably."""

    _BLOCK_TAGS = {"div", "section", "article", "details", "summary", "p", "br", "tr", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._list_stack: list[str] = []
        self._table_rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self._in_table = False

    def result(self) -> str:
        return _collapse_blank_lines("".join(self._out)).strip()

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            return
        if tag in {"ul", "ol"}:
            self._newline()
            self._list_stack.append(tag)
        elif tag == "li":
            self._newline()
            self._append("  " * max(0, len(self._list_stack) - 1) + "- ")
        elif tag == "table":
            self._newline()
            self._in_table = True
            self._table_rows = []
        elif tag == "tr" and self._in_table:
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = []
        elif tag in {"strong", "b"}:
            self._append("**")
        elif tag in {"em", "i"}:
            self._append("*")
        elif tag == "code":
            self._append("`")
        elif tag in self._BLOCK_TAGS:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            self._current_row.append(_cell_text("".join(self._current_cell)))
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self._table_rows.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._in_table:
            self._emit_table()
            self._in_table = False
        elif tag in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            self._newline()
        elif tag == "li":
            self._newline()
        elif tag in {"strong", "b"}:
            self._append("**")
        elif tag in {"em", "i"}:
            self._append("*")
        elif tag == "code":
            self._append("`")
        elif tag in self._BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            text = " ".join(data.split())
            if text:
                self._current_cell.append(text + " ")
        else:
            self._append(data)

    def _append(self, text: str) -> None:
        self._out.append(text)

    def _newline(self) -> None:
        if self._out and not self._out[-1].endswith("\n"):
            self._out.append("\n")

    def _emit_table(self) -> None:
        rows = [row for row in self._table_rows if row]
        if not rows:
            self._newline()
            return
        width = max(len(row) for row in rows)
        padded = [row + [""] * (width - len(row)) for row in rows]
        self._newline()
        self._out.append("| " + " | ".join(padded[0]) + " |\n")
        self._out.append("| " + " | ".join("---" for _ in range(width)) + " |\n")
        for row in padded[1:]:
            self._out.append("| " + " | ".join(row) + " |\n")
        self._newline()


def normalize_model_card_markdown(text: str) -> str:
    if "<" not in text or ">" not in text:
        return text
    parser = _MarkdownHtmlNormalizer()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return text
    normalized = parser.result()
    return normalized if normalized else text


def _cell_text(text: str) -> str:
    return text.replace("|", "\\|").strip()


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)




def compute_hardware_fit(files_or_size: list[HfFile] | int | None, profile: HardwareProfile | None = None) -> Optional[HardwareFit]:
    files = _fit_files(files_or_size)
    model_files = [f for f in files if f.size_bytes and not f.is_multimodal_projector and not _is_mtp_file(f.name)]
    if not model_files:
        return None
    model = min(model_files, key=lambda f: f.size_bytes or 0)
    model_bytes = int(model.size_bytes or 0)
    if model_bytes <= 0:
        return None
    profile = profile or _detect_hardware_profile()
    projector_bytes = sum(int(f.size_bytes or 0) for f in files if f.is_multimodal_projector)
    mtp_bytes = sum(int(f.size_bytes or 0) for f in files if _is_mtp_file(f.name))
    params = _infer_params(model.name, model_bytes, model.quantization)
    moe = _infer_moe(model.name, files, model_bytes, profile)
    contexts = tuple(
        _context_fit(ctx, model_bytes, projector_bytes, mtp_bytes, params, profile)
        for ctx in _CONTEXT_WINDOWS
    )
    label, score = _rank_contexts(contexts, profile)
    return HardwareFit(
        label=label,
        score=score,
        model_bytes=model_bytes,
        projector_bytes=projector_bytes,
        mtp_bytes=mtp_bytes,
        quantization=model.quantization,
        parameter_count=params,
        moe=moe,
        contexts=contexts,
    )

def recommended_profile_settings(fit: HardwareFit, profile: HardwareProfile | None = None) -> FitRecommendation:
    profile = profile or _detect_hardware_profile()
    chosen = _recommended_context(fit)
    gpu_capable = any(ctx.tier == "GPU" for ctx in fit.contexts)
    partial = any(ctx.tier == "partial offload" for ctx in fit.contexts)
    long_context = chosen.context_tokens >= 65_536
    settings: dict[str, object] = {
        "ctx_size": chosen.context_tokens,
        "parallel": 1,
        "threads": min(max(profile.cpu_threads, 1), 16),
        "batch_size": 1024 if gpu_capable else 512,
        "ubatch_size": 256 if gpu_capable else 128,
        "flash_attn": "on" if (gpu_capable or partial) else "auto",
        "cache_type_k": "q8_0" if long_context else "f16",
        "cache_type_v": "q8_0" if long_context else "f16",
        "n_gpu_layers": 999 if gpu_capable else (99 if partial else 0),
    }
    if fit.moe and partial:
        settings["batch_size"] = 512
        settings["ubatch_size"] = 128
    rationale = (
        f"{chosen.context_tokens // 1024}K context selected from fit table; "
        f"{chosen.tier} memory tier; "
        + ("MoE partial offload tuned conservatively. " if fit.moe else "")
        + ("KV cache quantized to q8_0 for long context." if long_context else "KV cache kept f16.")
    )
    return FitRecommendation(settings=settings, rationale=rationale)


def _recommended_context(fit: HardwareFit) -> ContextFit:
    for preferred in ("GPU", "partial offload", "RAM/CPU"):
        candidates = [ctx for ctx in fit.contexts if ctx.tier == preferred]
        if candidates:
            return candidates[-1]
    return fit.contexts[0]


def _infer_moe(name: str, files: list[HfFile], model_bytes: int, profile: HardwareProfile) -> Optional[MoeFit]:
    haystack = " ".join([name, *(f.name for f in files)]).lower()
    if not any(token in haystack for token in ("moe", "mixtral", "deepseek", "experts", "a3b", "a22b")):
        return None
    total, active = _moe_expert_counts(haystack)
    shared_fraction = 0.25
    expert_pool = int(model_bytes * (1.0 - shared_fraction))
    expert_bytes = max(1, expert_pool // total)
    available = max(0, int((profile.vram_bytes or 0) * _GPU_HEADROOM) - int(model_bytes * shared_fraction))
    gpu_experts = min(total, max(0, available // expert_bytes))
    active_gpu = min(active, gpu_experts)
    note = "active expert compute mostly GPU" if active_gpu >= active else "some active experts may execute on CPU"
    return MoeFit(
        total_experts=total,
        active_experts=active,
        gpu_experts_estimate=gpu_experts,
        active_gpu_experts_estimate=active_gpu,
        note=note,
    )


def _moe_expert_counts(text: str) -> tuple[int, int]:
    match = re.search(r"(\d+)\s*[x×]\s*(\d+)[bB]", text)
    if match:
        return max(1, int(match.group(1))), min(2, max(1, int(match.group(1))))
    match = re.search(r"(\d+)\s*experts?.{0,24}?(\d+)\s*active", text)
    if match:
        return max(1, int(match.group(1))), max(1, int(match.group(2)))
    if "mixtral" in text:
        return 8, 2
    if "deepseek" in text:
        return 64, 8
    return 16, 2

    
def _fit_files(files_or_size: list[HfFile] | int | None) -> list[HfFile]:
    if files_or_size is None:
        return []
    if isinstance(files_or_size, int):
        return [HfFile(name="model.gguf", size_bytes=files_or_size)]
    return files_or_size


def _detect_hardware_profile() -> HardwareProfile:
    return HardwareProfile(
        ram_bytes=_detect_ram_bytes(),
        vram_bytes=_detect_vram_bytes(),
        cpu_threads=_detect_cpu_threads(),
    )


def _detect_cpu_threads() -> int:
    try:
        import os

        return max(1, int(os.cpu_count() or 1))
    except Exception:
        return 1


def _context_fit(
    context_tokens: int,
    model_bytes: int,
    projector_bytes: int,
    mtp_bytes: int,
    parameter_count: Optional[int],
    profile: HardwareProfile,
) -> ContextFit:
    kv_bytes = _estimate_kv_cache_bytes(context_tokens, parameter_count, model_bytes)
    required = model_bytes + projector_bytes + mtp_bytes + kv_bytes + max(512 * 1024 * 1024, model_bytes // 20)
    tier = _tier(required, profile)
    return ContextFit(context_tokens=context_tokens, kv_cache_bytes=kv_bytes, required_bytes=required, tier=tier)


def _tier(required_bytes: int, profile: HardwareProfile) -> str:
    if profile.vram_bytes and required_bytes <= int(profile.vram_bytes * _GPU_HEADROOM):
        return "GPU"
    if profile.vram_bytes and profile.ram_bytes:
        gpu_cap = int(profile.vram_bytes * _GPU_HEADROOM)
        ram_cap = int(profile.ram_bytes * _RAM_HEADROOM)
        if required_bytes <= gpu_cap + ram_cap:
            return "partial offload"
    if profile.ram_bytes and required_bytes <= int(profile.ram_bytes * _RAM_HEADROOM):
        return "RAM/CPU"
    return "too large"


def _rank_contexts(contexts: tuple[ContextFit, ...], profile: HardwareProfile) -> tuple[str, int]:
    gpu = sum(1 for c in contexts if c.tier == "GPU")
    partial = sum(1 for c in contexts if c.tier == "partial offload")
    ram = sum(1 for c in contexts if c.tier == "RAM/CPU")
    if gpu == len(contexts):
        return "excellent GPU fit through 128K", 500
    if gpu >= 3:
        return "GPU fit through 64K", 440
    if gpu >= 2:
        return "GPU fit through 32K", 390
    if gpu >= 1:
        return "GPU fit at 16K", 340
    if partial:
        return "partial GPU offload", 260 + partial * 10
    if ram:
        cpu_penalty = 40 if profile.cpu_threads < 8 else 0
        return "RAM/CPU only", 180 - cpu_penalty
    return "likely too large", 40


def _estimate_kv_cache_bytes(context_tokens: int, parameter_count: Optional[int], model_bytes: int) -> int:
    params_b = (parameter_count or _infer_params("", model_bytes, None) or 7_000_000_000) / 1_000_000_000
    layers = _estimate_layers(params_b)
    hidden = _estimate_hidden(params_b)
    kv_heads_ratio = 0.25 if params_b >= 30 else 0.5
    # llama.cpp defaults KV cache to f16: K + V = 4 bytes per token per effective hidden width per layer.
    return int(context_tokens * layers * hidden * kv_heads_ratio * 4)


def _estimate_layers(params_b: float) -> int:
    if params_b <= 4:
        return 32
    if params_b <= 9:
        return 32
    if params_b <= 15:
        return 40
    if params_b <= 35:
        return 48
    if params_b <= 80:
        return 80
    return 96


def _estimate_hidden(params_b: float) -> int:
    if params_b <= 4:
        return 3072
    if params_b <= 9:
        return 4096
    if params_b <= 15:
        return 5120
    if params_b <= 35:
        return 6656
    if params_b <= 80:
        return 8192
    return 12288


def _infer_params(name: str, size_bytes: int, quantization: Optional[str]) -> Optional[int]:
    match = _PARAM_RE.search(name)
    if match:
        value = float(match.group(1))
        scale = 1_000_000_000 if match.group(2).lower() == "b" else 1_000_000
        return int(value * scale)
    bytes_per_param = _quant_bytes_per_param(quantization)
    if bytes_per_param:
        return int(size_bytes / bytes_per_param)
    return None


def _quant_bytes_per_param(quantization: Optional[str]) -> Optional[float]:
    if not quantization:
        return None
    q = quantization.upper()
    if q in {"F32"}:
        return 4.0
    if q in {"F16", "BF16"}:
        return 2.0
    if q.startswith("IQ2") or q.startswith("Q2"):
        return 0.35
    if q.startswith("IQ3") or q.startswith("Q3"):
        return 0.45
    if q.startswith("IQ4") or q.startswith("Q4"):
        return 0.58
    if q.startswith("Q5"):
        return 0.70
    if q.startswith("Q6"):
        return 0.82
    if q.startswith("Q8"):
        return 1.05
    return None


def _is_mtp_file(name: str) -> bool:
    lower = name.lower()
    return "mtp" in lower or "draft" in lower


def _format_bytes(size: int) -> str:
    value = float(size)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    return f"{value:.1f} {units[idx]}" if idx else f"{int(value)} B"


def _format_params(params: int) -> str:
    if params >= 1_000_000_000:
        return f"{params / 1_000_000_000:.1f}B"
    return f"{params / 1_000_000:.0f}M"

class NotImplementedHfSearchService:
    def search(self, query: str, filters: Iterable[HfFilter] = ()) -> HfSearchOutcome:
        return HfSearchOutcome(status="not-implemented", message="HuggingFace search service is not configured.")


def _detect_ram_bytes() -> Optional[int]:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def _detect_vram_bytes() -> Optional[int]:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    values = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            values.append(int(line) * 1024 * 1024)
    return max(values) if values else None


def _quant(name: str) -> Optional[str]:
    match = _QUANT_RE.search(name)
    return match.group(1).upper() if match else None


def _is_split(name: str) -> bool:
    lower = name.lower()
    return bool(re.search(r"-\d{5}-of-\d{5}\.gguf$", lower) or re.search(r"\.part\d+\.gguf$", lower))





@dataclass(frozen=True)
class HfConnectivity:
    """Result of a lightweight HuggingFace API reachability probe."""
    reachable: bool
    latency_ms: Optional[float] = None
    status_detail: str = ""


def check_hf_connectivity(token: Optional[str] = None, timeout: float = 8.0) -> HfConnectivity:
    """Lightweight HEAD probe to ``huggingface.co/api/models``.

    Returns reachability, round-trip latency, and a human-readable detail string.
    """
    import time

    headers = {"User-Agent": "llamaUI-qt/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{HF_API}/models?limit=1"
    try:
        req = urllib.request.Request(url, headers=headers, method="HEAD")
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.monotonic() - t0) * 1000
        return HfConnectivity(
            reachable=True,
            latency_ms=round(elapsed, 1),
            status_detail=f"HTTP {resp.status}",
        )
    except urllib.error.HTTPError as exc:
        return HfConnectivity(
            reachable=False,
            status_detail=f"HTTP {exc.code} {exc.reason}",
        )
    except Exception as exc:
        return HfConnectivity(reachable=False, status_detail=str(exc)[:120])

__all__ = [
    "ContextFit",
    "FitRecommendation",
    "HardwareFit",
    "HardwareProfile",
    "MoeFit",
    "HfConnectivity",
    "HfFilter",
    "HfFile",
    "HfRepoSummary",
    "HfSearchOutcome",
    "HfSearchService",
    "normalize_model_card_markdown",
    "HuggingFaceSearchService",
    "NotImplementedHfSearchService",
    "check_hf_connectivity",
    "compute_hardware_fit",
    "recommended_profile_settings",
]
