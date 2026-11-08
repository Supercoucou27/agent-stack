"""Llama-server option catalog and typed value handling.

This module is the storage-side companion to the dynamic introspection
planned for Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple, Union


class OptionKind(str, Enum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    STRING_LIST = "string_list"


LlamaOptionId = str


@dataclass(frozen=True)
class LlamaOption:
    id: LlamaOptionId
    flag: str
    kind: OptionKind
    group: str
    label: str
    help_text: str
    default: Optional["LlamaOptionValue"] = None
    aliases: Tuple[str, ...] = ()
    env_var: Optional[str] = None
    restart_required: bool = True
    enum_values: Tuple[Tuple[str, str], ...] = ()
    importance: int = 0
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    step: Optional[float] = None

    def matches_flag(self, token: str) -> bool:
        return token == self.flag or token in self.aliases



class LlamaOptionValue:
    __slots__ = ("kind", "value")
    def __init__(self, kind: OptionKind, value: Any) -> None:
        self.kind = kind
        self.value = value
    @classmethod
    def from_raw(cls, kind: OptionKind, raw: Any) -> "LlamaOptionValue":
        if raw is None:
            return cls(kind, None)
        if kind is OptionKind.BOOLEAN:
            if not isinstance(raw, bool): raise TypeError(f"option expects bool, got {type(raw).__name__}")
            return cls(kind, raw)
        if kind is OptionKind.INTEGER:
            if isinstance(raw, bool) or not isinstance(raw, int): raise TypeError(f"option expects int, got {type(raw).__name__}")
            return cls(kind, raw)
        if kind is OptionKind.FLOAT:
            if isinstance(raw, bool): raise TypeError("option expects number, got bool")
            if isinstance(raw, int): return cls(kind, float(raw))
            if not isinstance(raw, float): raise TypeError(f"option expects float, got {type(raw).__name__}")
            return cls(kind, raw)
        if kind is OptionKind.STRING:
            if not isinstance(raw, str): raise TypeError(f"option expects str, got {type(raw).__name__}")
            return cls(kind, raw)
        if kind is OptionKind.STRING_LIST:
            if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw): raise TypeError(f"option expects list[str], got {type(raw).__name__}")
            return cls(kind, list(raw))
        raise ValueError(f"unknown option kind: {kind!r}")
    def to_argv(self, option: "LlamaOption") -> List[str]:
        if self.value is None:
            return []
        if self.kind is OptionKind.BOOLEAN:
            return [option.flag] if self.value else []
        if self.kind is OptionKind.STRING_LIST:
            if option.id == "extra_args":
                return [str(v) for v in self.value]
            out: List[str] = []
            for v in self.value:
                out.extend([option.flag, v])
            return out
        return [option.flag, str(self.value)]
    def to_json(self) -> Any:
        return self.value
    @classmethod
    def from_json(cls, kind: OptionKind, raw: Any) -> "LlamaOptionValue":
        return cls.from_raw(kind, raw)
    @property
    def is_set(self) -> bool:
        return self.value is not None


class SettingValueMap:
    __slots__ = ("_values",)
    def __init__(self, values: Optional[Dict[LlamaOptionId, LlamaOptionValue]] = None) -> None:
        self._values = dict(values or {})
    def __len__(self) -> int: return len(self._values)
    def __contains__(self, option_id: object) -> bool: return option_id in self._values
    def get(self, option_id: LlamaOptionId) -> Optional[LlamaOptionValue]: return self._values.get(option_id)
    def items(self) -> Iterable[Tuple[LlamaOptionId, LlamaOptionValue]]: return self._values.items()
    def keys(self) -> Iterable[LlamaOptionId]: return self._values.keys()
    def values(self) -> Iterable[LlamaOptionValue]: return self._values.values()
    def copy(self) -> "SettingValueMap": return SettingValueMap(dict(self._values))
    def with_value(self, option: LlamaOption, value: Union[LlamaOptionValue, Any, None]) -> "SettingValueMap":
        new_map = SettingValueMap(self._values)
        if value is None:
            new_map._values.pop(option.id, None); return new_map
        if isinstance(value, LlamaOptionValue):
            if value.kind is not option.kind: raise TypeError(f"option {option.id!r} expects {option.kind.value!r}")
            new_map._values[option.id] = value; return new_map
        new_map._values[option.id] = LlamaOptionValue.from_raw(option.kind, value)
        return new_map
    def without(self, option_id: LlamaOptionId) -> "SettingValueMap":
        new_map = SettingValueMap(self._values); new_map._values.pop(option_id, None); return new_map
    def merge(self, other: "SettingValueMap") -> "SettingValueMap":
        out = dict(self._values); out.update(other._values); return SettingValueMap(out)
    def to_json(self) -> Dict[str, Any]:
        return {opt_id: value.to_json() for opt_id, value in self._values.items()}
    @classmethod
    def from_json(cls, raw: Any, catalog: "LlamaOptionCatalog") -> "SettingValueMap":
        if raw is None: return cls()
        if not isinstance(raw, dict): raise TypeError(f"settings payload must be a dict, got {type(raw).__name__}")
        out: Dict[LlamaOptionId, LlamaOptionValue] = {}
        for opt_id, value in raw.items():
            option = catalog.get(opt_id)
            if option is None:
                out[opt_id] = LlamaOptionValue(OptionKind.STRING, str(value)); continue
            try:
                out[opt_id] = LlamaOptionValue.from_json(option.kind, value)
            except (TypeError, ValueError):
                continue
        return cls(out)
    def to_argv(self, catalog: "LlamaOptionCatalog") -> List[str]:
        """Emit ``--flag value`` pairs for every set value.

        Two rules to prevent leaking catalog defaults onto the command line:

        1. Skip values that match the catalog's explicit default.
        2. Skip "natural default" values (False / 0 / "" / [] / None) when
           the catalog option has no explicit default. The user has to
           actively set the value to a non-default for it to be emitted.
        """
        argv: List[str] = []
        for option_id, value in self._values.items():
            option = catalog.get(option_id)
            if option is None: continue
            # Rule 1: explicit catalog default.
            if option.default is not None and value.value == option.default.value:
                continue
            # Rule 2: implicit natural default.
            if option.default is None and value.value in (False, 0, 0.0, "", [], None):
                continue
            argv.extend(value.to_argv(option))
        return argv


@dataclass(frozen=True)
class ProfilePreset:
    name: str
    values: Dict[str, Any]


PRESET_CONSERVATIVE_CPU = ProfilePreset("Conservative CPU", {"n_gpu_layers": 0, "ctx_size": 4096, "threads": 8, "batch_size": 256, "ubatch_size": 128, "parallel": 1})
PRESET_BALANCED_GPU = ProfilePreset("Balanced GPU", {"n_gpu_layers": 99, "ctx_size": 8192, "batch_size": 1024, "ubatch_size": 256, "parallel": 1})
PRESET_LOW_MEMORY = ProfilePreset("Low Memory", {"n_gpu_layers": 0, "ctx_size": 2048, "batch_size": 128, "ubatch_size": 64, "parallel": 1})
PRESET_MAX_VRAM = ProfilePreset("Max VRAM offload", {"n_gpu_layers": 999, "ctx_size": 8192, "batch_size": 2048, "ubatch_size": 512})
PRESET_LONG_CONTEXT = ProfilePreset("Long context", {"ctx_size": 32768, "batch_size": 512, "ubatch_size": 128})
PRESET_FAST_PROMPT = ProfilePreset("Fast prompt processing", {"threads": 16, "batch_size": 2048, "ubatch_size": 512, "parallel": 1})
PROFILE_PRESETS = (PRESET_CONSERVATIVE_CPU, PRESET_BALANCED_GPU, PRESET_LOW_MEMORY, PRESET_MAX_VRAM, PRESET_LONG_CONTEXT, PRESET_FAST_PROMPT)


def apply_preset_to_settings(preset: ProfilePreset, catalog: "LlamaOptionCatalog" = None) -> SettingValueMap:
    catalog = catalog or LLAMA_OPTION_CATALOG
    settings = SettingValueMap()
    for option_id, raw in preset.values.items():
        option = catalog.get(option_id)
        if option is not None:
            settings = settings.with_value(option, raw)
    return settings


@dataclass(frozen=True)
class LlamaOptionCatalog:
    options_by_id: Mapping[LlamaOptionId, LlamaOption]
    groups: Tuple[str, ...] = ()
    def __post_init__(self) -> None: object.__setattr__(self, "options_by_id", dict(self.options_by_id))
    def get(self, option_id: LlamaOptionId) -> Optional[LlamaOption]: return self.options_by_id.get(option_id)
    def __iter__(self) -> Iterator[LlamaOption]: return iter(self.options_by_id.values())
    def options(self) -> Iterator[Tuple[LlamaOptionId, LlamaOption]]: return iter(self.options_by_id.items())
    def by_group(self, group: str) -> List[LlamaOption]: return [o for o in self.options_by_id.values() if o.group == group]
    def groups_in_order(self) -> List[str]:
        seen: List[str] = []
        for o in self.options_by_id.values():
            if o.group not in seen: seen.append(o.group)
        return seen


def _opt(id: str, flag: str, kind: OptionKind, group: str, label: str, help_text: str, *, default: Optional[LlamaOptionValue] = None, aliases: Tuple[str, ...] = (), env_var: Optional[str] = None, restart_required: bool = True, enum_values: Tuple[Tuple[str, str], ...] = (), importance: int = 0, min_value: Optional[float] = None, max_value: Optional[float] = None, step: Optional[float] = None) -> LlamaOption:
    return LlamaOption(id=id, flag=flag, kind=kind, group=group, label=label, help_text=help_text, default=default, aliases=aliases, env_var=env_var, restart_required=restart_required, enum_values=enum_values, importance=importance, min_value=min_value, max_value=max_value, step=step)
def _build_default_catalog() -> LlamaOptionCatalog:
    opts: List[LlamaOption] = [
        _opt("model", "--model", OptionKind.STRING, "Model loading", "Model file", "Path to the GGUF model file to load."),
        _opt("alias", "--alias", OptionKind.STRING, "Model loading", "Model alias", "Name used by the API to refer to this model."),
        _opt("ctx_size", "--ctx-size", OptionKind.INTEGER, "Context / KV cache", "Context size", "Total context length in tokens.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, 4096), importance=1, min_value=256, max_value=1_048_576, step=64),
        _opt("cache_type_k", "--cache-type-k", OptionKind.STRING, "Context / KV cache", "KV cache K type", "Data type used for the key cache.", default=LlamaOptionValue.from_raw(OptionKind.STRING, "f16"), enum_values=(("f16","f16"), ("f32","f32"), ("bf16","bf16"), ("q8_0","q8_0"), ("q4_0","q4_0"), ("q5_0","q5_0"), ("q5_1","q5_1")), importance=1),
        _opt("cache_type_v", "--cache-type-v", OptionKind.STRING, "Context / KV cache", "KV cache V type", "Data type used for the value cache.", default=LlamaOptionValue.from_raw(OptionKind.STRING, "f16"), enum_values=(("f16","f16"), ("f32","f32"), ("bf16","bf16"), ("q8_0","q8_0"), ("q4_0","q4_0"), ("q5_0","q5_0"), ("q5_1","q5_1")), importance=1),
        _opt("no_kv_offload", "--no-kv-offload", OptionKind.BOOLEAN, "Context / KV cache", "Disable KV offload", "Keep KV cache off the GPU even when layers are offloaded.", default=LlamaOptionValue.from_raw(OptionKind.BOOLEAN, False)),
        _opt("defrag_thold", "--defrag-thold", OptionKind.FLOAT, "Context / KV cache", "KV defrag threshold", "Min KV-cache fragmentation to trigger defrag. -1 disables.", default=LlamaOptionValue.from_raw(OptionKind.FLOAT, -1.0), restart_required=False, min_value=-1.0, max_value=1.0, step=0.05),
        _opt("flash_attn", "--flash-attn", OptionKind.STRING, "Context / KV cache", "Flash attention", "Flash Attention mode. 'on' enables FA, 'off' disables, 'auto' lets llama-server pick. Recommended 'on' for all modern GPUs — the single biggest perf knob.", default=LlamaOptionValue.from_raw(OptionKind.STRING, "auto"), restart_required=True, importance=1, enum_values=(("auto","auto"), ("on","on"), ("off","off"))),
        _opt("rope_freq_base", "--rope-freq-base", OptionKind.FLOAT, "Context / KV cache", "RoPE freq base", "Base frequency for rotary position embeddings. 0 = model default.", default=LlamaOptionValue.from_raw(OptionKind.FLOAT, 0.0), restart_required=True, min_value=0.0, max_value=1_000_000.0, step=1000.0),
        _opt("rope_freq_scale", "--rope-freq-scale", OptionKind.FLOAT, "Context / KV cache", "RoPE freq scale", "Scale factor for rotary position embeddings.", default=LlamaOptionValue.from_raw(OptionKind.FLOAT, 1.0), restart_required=True, min_value=0.0, max_value=100.0, step=0.1),
        _opt("rope_scaling", "--rope-scaling", OptionKind.STRING, "Context / KV cache", "RoPE scaling type", "RoPE scaling: none, linear, yarn.", default=LlamaOptionValue.from_raw(OptionKind.STRING, "none"), restart_required=True, enum_values=(("none","none"), ("linear","linear"), ("yarn","yarn"))),
        _opt("n_gpu_layers", "--n-gpu-layers", OptionKind.INTEGER, "GPU / offload", "GPU layers", "Number of transformer layers to offload to GPU.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, 0), importance=1, min_value=0, max_value=999, step=1),
        _opt("tensor_split", "--tensor-split", OptionKind.STRING, "GPU / offload", "Tensor split", "Comma-separated GPU split ratios for multi-GPU offload."),
        _opt("split_mode", "--split-mode", OptionKind.STRING, "GPU / offload", "Split mode", "How to split tensors across GPUs: none, layer, row.", default=LlamaOptionValue.from_raw(OptionKind.STRING, "none"), restart_required=True, enum_values=(("none","none"), ("layer","layer"), ("row","row"))),
        _opt("main_gpu", "--main-gpu", OptionKind.INTEGER, "GPU / offload", "Main GPU index", "Index of the main GPU for split-mode row. 0-based.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, 0), restart_required=True, min_value=0, max_value=16, step=1),
        _opt("threads", "--threads", OptionKind.INTEGER, "Performance", "CPU threads", "Number of CPU threads to use for inference and batch processing.", min_value=0, max_value=256, step=1),
        _opt("batch_size", "--batch-size", OptionKind.INTEGER, "Performance", "Batch size", "Logical batch size for prompt processing.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, 512), importance=1, min_value=1, max_value=8192, step=32),
        _opt("ubatch_size", "--ubatch-size", OptionKind.INTEGER, "Performance", "Micro-batch size", "Physical (micro) batch size.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, 128), min_value=1, max_value=8192, step=32),
        _opt("parallel", "--parallel", OptionKind.INTEGER, "Performance", "Parallel slots", "Number of parallel sequences the server can handle.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, 1), importance=1, min_value=1, max_value=32, step=1),
        _opt("models_max", "--models-max", OptionKind.INTEGER, "Performance", "Max loaded models", "Maximum number of models loaded simultaneously in router mode. LRU eviction unloads the least-recently-used model when the limit is hit.", min_value=1, max_value=32, step=1),
        _opt("mmap", "--mmap", OptionKind.BOOLEAN, "Performance", "Memory-map model", "Memory-map the model file instead of loading it into RAM.", default=LlamaOptionValue.from_raw(OptionKind.BOOLEAN, True)),
        _opt("mlock", "--mlock", OptionKind.BOOLEAN, "Performance", "Lock memory", "Lock the model into physical RAM.", default=LlamaOptionValue.from_raw(OptionKind.BOOLEAN, False)),
        _opt("host", "--host", OptionKind.STRING, "Server / API", "Bind host", "IP address or hostname the server listens on.", default=LlamaOptionValue.from_raw(OptionKind.STRING, "127.0.0.1")),
        _opt("port", "--port", OptionKind.INTEGER, "Server / API", "Bind port", "TCP port the server listens on.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, 8080), min_value=1, max_value=65535, step=1),
        _opt("api_key", "--api-key", OptionKind.STRING, "Server / API", "API key", "Require this key in the Authorization header."),
        _opt("cont_batching", "--cont-batching", OptionKind.BOOLEAN, "Server / API", "Continuous batching", "Keep batching requests as tokens continue to arrive.", default=LlamaOptionValue.from_raw(OptionKind.BOOLEAN, True), restart_required=False),
        _opt("verbose", "--verbose", OptionKind.BOOLEAN, "Debug / logging", "Verbose logging", "Print verbose llama.cpp logs to stderr.", default=LlamaOptionValue.from_raw(OptionKind.BOOLEAN, False), restart_required=False),
        _opt("temp", "--temp", OptionKind.FLOAT, "Sampling", "Temperature", "Sampling temperature.", default=LlamaOptionValue.from_raw(OptionKind.FLOAT, 0.8), restart_required=False, importance=1, min_value=0.0, max_value=2.0, step=0.05),
        _opt("top_k", "--top-k", OptionKind.INTEGER, "Sampling", "Top-K", "Limit sampling to the K most likely tokens.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, 40), restart_required=False, importance=1, min_value=0, max_value=200, step=1),
        _opt("top_p", "--top-p", OptionKind.FLOAT, "Sampling", "Top-P", "Nucleus sampling cumulative probability cutoff.", default=LlamaOptionValue.from_raw(OptionKind.FLOAT, 0.95), restart_required=False, importance=1, min_value=0.0, max_value=1.0, step=0.05),
        _opt("min_p", "--min-p", OptionKind.FLOAT, "Sampling", "Min-P", "Minimum token probability scaled by the top token.", default=LlamaOptionValue.from_raw(OptionKind.FLOAT, 0.05), restart_required=False, min_value=0.0, max_value=1.0, step=0.05),
        _opt("repeat_penalty", "--repeat-penalty", OptionKind.FLOAT, "Sampling", "Repeat penalty", "Penalize repeated tokens.", default=LlamaOptionValue.from_raw(OptionKind.FLOAT, 1.1), restart_required=False, min_value=0.0, max_value=2.0, step=0.05),
        _opt("seed", "--seed", OptionKind.INTEGER, "Sampling", "Seed", "RNG seed.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, -1), restart_required=False, min_value=-1, max_value=2_147_483_647, step=1),
        _opt("grp_attn_n", "--grp-attn-n", OptionKind.INTEGER, "Attention", "Group-attn stride", "Group-attention factor for self-extend. 1 disables.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, 1), restart_required=True, min_value=1, max_value=64, step=1),
        _opt("grp_attn_w", "--grp-attn-w", OptionKind.INTEGER, "Attention", "Group-attn width", "Group-attention context window size.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, 512), restart_required=True, min_value=1, max_value=8192, step=1),
        _opt("mmproj", "--mmproj", OptionKind.STRING, "Multimodal", "Projector file", "Path to the multimodal projector/model adapter file."),
        _opt("draft_model", "--draft-model", OptionKind.STRING, "Speculative decoding", "Draft model", "Optional draft model path for speculative decoding."),
        _opt("draft", "--draft", OptionKind.INTEGER, "Speculative decoding", "Draft tokens", "Number of tokens to draft for speculative decoding. 0 disables.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, 0), restart_required=False, min_value=0, max_value=64, step=1),
        _opt("draft_min", "--draft-min", OptionKind.INTEGER, "Speculative decoding", "Min draft tokens", "Min draft tokens below which the draft is discarded.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, 0), restart_required=False, min_value=0, max_value=64, step=1),
        _opt("lookup_cache_stride", "--lookup-cache-stride", OptionKind.INTEGER, "Speculative decoding", "Lookup cache stride", "Stride for the lookup cache in speculative decoding.", restart_required=True, min_value=0, max_value=64, step=1),
        _opt("log_disable", "--log-disable", OptionKind.BOOLEAN, "Debug / logging", "Disable logs", "Reduce or suppress verbose internal logging output.", default=LlamaOptionValue.from_raw(OptionKind.BOOLEAN, False), restart_required=False),
        _opt("log_format", "--log-format", OptionKind.STRING, "Debug / logging", "Log format", "Log output format: text or json.", default=LlamaOptionValue.from_raw(OptionKind.STRING, "text"), restart_required=False),
        _opt("log_color", "--log-color", OptionKind.BOOLEAN, "Debug / logging", "Colored log output", "Enable ANSI color codes in log output.", default=LlamaOptionValue.from_raw(OptionKind.BOOLEAN, False), restart_required=False),
        _opt("metrics", "--metrics", OptionKind.BOOLEAN, "Debug / logging", "Expose /metrics", "Enable the /metrics Prometheus-compatible endpoint.", default=LlamaOptionValue.from_raw(OptionKind.BOOLEAN, True), restart_required=False),
        _opt("jinja", "--jinja", OptionKind.BOOLEAN, "Server / API", "Use Jinja chat template", "Apply the Jinja chat template shipped with the model. Required for Qwen3, Llama-3, Gemma-2 and most modern chat-tuned models.", default=LlamaOptionValue.from_raw(OptionKind.BOOLEAN, False), restart_required=True, importance=1),
        _opt("reasoning_budget", "--reasoning-budget", OptionKind.INTEGER, "Server / API", "Reasoning budget", "Max tokens the model can spend on the reasoning/chain-of-thought trace. -1 disables. Recommended 1024-4096 for Qwen3 reasoning models.", default=LlamaOptionValue.from_raw(OptionKind.INTEGER, -1), restart_required=False, importance=1, min_value=-1, max_value=262144, step=1),
        _opt("reasoning", "--reasoning", OptionKind.STRING, "Server / API", "Enable reasoning", "Whether the model uses its reasoning / thinking trace: on, off, or auto. Recommended 'on' for Qwen3 reasoning models.", default=LlamaOptionValue.from_raw(OptionKind.STRING, "auto"), restart_required=False, importance=1, enum_values=(("on","on"), ("off","off"), ("auto","auto"))),
        _opt("reasoning_format", "--reasoning-format", OptionKind.STRING, "Server / API", "Reasoning format", "How reasoning is exposed: auto, deepseek, or none.", default=LlamaOptionValue.from_raw(OptionKind.STRING, "auto"), restart_required=False, enum_values=(("auto","auto"), ("deepseek","deepseek"), ("none","none"))),
        _opt("extra_args", "--_extra", OptionKind.STRING_LIST, "Advanced", "Extra arguments", "Raw extra arguments appended verbatim to the llama-server command line."),
    ]
    by_id: Dict[str, LlamaOption] = {o.id: o for o in opts}
    groups: List[str] = []
    for o in opts:
        if o.group not in groups: groups.append(o.group)
    return LlamaOptionCatalog(options_by_id=by_id, groups=tuple(groups))


LLAMA_OPTION_CATALOG: LlamaOptionCatalog = _build_default_catalog()



def default_settings_from_catalog(catalog: LlamaOptionCatalog = LLAMA_OPTION_CATALOG) -> SettingValueMap:
    values: Dict[LlamaOptionId, LlamaOptionValue] = {}
    for opt_id, option in catalog.options():
        if option.default is not None and option.default.is_set:
            values[opt_id] = option.default
    return SettingValueMap(values)


_NATURAL_DEFAULTS = {"", "0", "0.0", "-1", "-1.0", "[]"}

_BOOL_FLAGS = {
    # Flags that take no value and are NOT in the catalog. The catalog
    # already drops its own boolean flags. This set covers the rest
    # so the natural-default filter does not pair them with a value.
    "--escape",
    "--op-offload",
}

def clean_raw_args(raw_args: Sequence[str]) -> List[str]:
    """Migrate pre-Section-6 ``raw_args`` down to a minimal form.

    Old profiles written by ``RunSchemaImpl`` round-tripped every schema
    field as a flag/value pair in ``raw_args``. After a fresh
    ``_settings_from_form`` pass, those fields belong in ``settings`` /
    ``user_set``; leaving them in ``raw_args`` would leak catalog
    defaults onto the command line.

    This helper drops:
    - any flag that is now in the catalog (the catalog handles it),
    - any natural-default pair (e.g. ``--flag 0`` / ``--flag ""``), even
      for flags that are not in the catalog,
    - lone empty tokens.
    The result is appended to argv unchanged by ``build_argv``.
    """
    if not raw_args:
        return list(raw_args)
    out: List[str] = []
    i = 0
    while i < len(raw_args):
        token = raw_args[i]
        if not token.startswith("--"):
            out.append(token)
            i += 1
            continue
        if token in _BOOL_FLAGS:
            out.append(token)
            i += 1
            continue
        # Look for a paired value: only treat the next token as a value
        # if it does not start with "--" (otherwise it would be a
        # boolean-style standalone flag).
        has_pair = (i + 1 < len(raw_args)) and not raw_args[i + 1].startswith("--")
        value = raw_args[i + 1] if has_pair else None
        if has_pair and value in _NATURAL_DEFAULTS:
            i += 2
            continue
        # Is this flag now in the catalog? If so, drop the whole entry —
        # the catalog handles it (with its own user_set / default rules).
        # Boolean catalog flags are dropped unconditionally: they would
        # only ever appear in raw_args from a pre-Section-6 round-trip,
        # and the catalog already knows their default.
        flag_owner = next(
            (o for o in LLAMA_OPTION_CATALOG if o.flag == token or token in o.aliases),
            None,
        )
        if flag_owner is not None:
            if flag_owner.kind is OptionKind.BOOLEAN or has_pair:
                i += 2 if has_pair else 1
                continue
            # No pair, not boolean: keep token alone (rare; some flags
            # accept a value-less form).
            out.append(token)
            i += 1
            continue
        # Unknown to the catalog: keep as-is, plus its paired value.
        out.append(token)
        if has_pair:
            out.append(value)
        i += 2 if has_pair else 1
    return out

__all__ = [
    "LLAMA_OPTION_CATALOG",
    "LlamaOption",
    "LlamaOptionCatalog",
    "LlamaOptionId",
    "LlamaOptionValue",
    "OptionKind",
    "SettingValueMap",
    "clean_raw_args",
    "default_settings_from_catalog",
    "ProfilePreset",
    "PROFILE_PRESETS",
    "PRESET_CONSERVATIVE_CPU",
    "PRESET_BALANCED_GPU",
    "PRESET_LOW_MEMORY",
    "PRESET_MAX_VRAM",
    "PRESET_LONG_CONTEXT",
    "PRESET_FAST_PROMPT",
    "apply_preset_to_settings",
]
