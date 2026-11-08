"""Core data and storage layer for the native Qt llamaUI app."""

from .llama_options import (
    LLAMA_OPTION_CATALOG,
    LlamaOption,
    LlamaOptionId,
    LlamaOptionValue,
    OptionKind,
    ProfilePreset,
    PROFILE_PRESETS,
    PRESET_BALANCED_GPU,
    PRESET_CONSERVATIVE_CPU,
    PRESET_LOW_MEMORY,
    SettingValueMap,
    apply_preset_to_settings,
    clean_raw_args,
    default_settings_from_catalog,
)
from .models import AppConfig, HfTokenSource, LocalModel, ModelProfile
from .paths import DataPaths, default_data_dir, default_paths
from .storage import CURRENT_SCHEMA_VERSION, Migration, VersionedEnvelope, load_envelope, save_envelope
from .stores import ConfigStore, LibraryStore, ProfileStore

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "Migration",
    "VersionedEnvelope",
    "load_envelope",
    "save_envelope",
    "DataPaths",
    "default_data_dir",
    "default_paths",
    "LLAMA_OPTION_CATALOG",
    "LlamaOption",
    "LlamaOptionId",
    "LlamaOptionValue",
    "OptionKind",
    "ProfilePreset",
    "PROFILE_PRESETS",
    "PRESET_BALANCED_GPU",
    "PRESET_CONSERVATIVE_CPU",
    "PRESET_LOW_MEMORY",
    "SettingValueMap",
    "apply_preset_to_settings",
    "clean_raw_args",
    "default_settings_from_catalog",
    "ConfigStore",
    "HfTokenSource",
    "LocalModel",
    "LibraryStore",
    "ModelProfile",
    "ProfileStore",
]
