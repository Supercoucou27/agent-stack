"""Smoke tests for Section 6: user_set + skip-defaults in build_argv."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
QT_ROOT = REPO_ROOT / "qt_app"
for candidate in (REPO_ROOT, QT_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from llama_data.llama_options import LLAMA_OPTION_CATALOG, LlamaOptionValue, OptionKind, SettingValueMap
from llama_data.models import AppConfig, LocalModel, ModelProfile
from app.services.runtime import build_argv


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"pass {message}")


def main() -> int:
    # --- ModelProfile.user_set round-trip via to_json / from_json ---
    settings = SettingValueMap()
    settings = settings.with_value(LLAMA_OPTION_CATALOG.get("ctx_size"), LlamaOptionValue(OptionKind.INTEGER, 8192))
    settings = settings.with_value(LLAMA_OPTION_CATALOG.get("cache_type_k"), LlamaOptionValue(OptionKind.STRING, "f16"))

    profile = ModelProfile(
        id="test1",
        model_id="model-a",
        name="Test profile",
        settings=settings,
        user_set={"ctx_size"},  # only ctx_size is user-set
    )
    blob = profile.to_json()
    check("user_set" in blob, "to_json includes user_set")
    check(blob["user_set"] == ["ctx_size"], "user_set serialized as sorted list")

    loaded = ModelProfile.from_json(blob)
    check(loaded.user_set == {"ctx_size"}, "from_json restores user_set")
    check(loaded.settings.get("ctx_size").value == 8192, "settings preserved")

    # --- Migration: old profile without user_set field ---
    old_blob = dict(blob)
    del old_blob["user_set"]
    migrated = ModelProfile.from_json(old_blob)
    # cache_type_k is "f16" which IS the catalog default → should NOT be in user_set
    # ctx_size is 8192 which differs from the default (likely 4096) → should be in user_set
    check("ctx_size" in migrated.user_set, "migration infers ctx_size as user-set")
    check("cache_type_k" not in migrated.user_set, "migration skips default-valued cache_type_k")

    # --- build_argv skips non-user-set options ---
    config = AppConfig(llama_server_path="/usr/bin/llama-server", host="127.0.0.1", port=8080)
    model = LocalModel(id="model-a", path="/models/test.gguf")

    argv = build_argv(config, model, profile)
    argv_str = " ".join(argv)

    check("--ctx-size" in argv_str, "user-set ctx_size appears in argv")
    check("8192" in argv_str, "ctx_size value 8192 in argv")
    check("--cache-type-k" not in argv_str, "non-user-set cache_type_k NOT in argv")

    # --- Empty profile: only model/host/port ---
    empty = ModelProfile(id="empty", model_id="model-a", name="Empty")
    argv_empty = build_argv(config, model, empty)
    check(argv_empty[0] == "/usr/bin/llama-server", "argv starts with binary path")
    check("--model" in argv_empty, "--model flag present")
    check("--host" in argv_empty, "--host flag present")
    check("--port" in argv_empty, "--port flag present")
    # No extra flags beyond model/host/port
    extra_flags = [a for a in argv_empty if a.startswith("--") and a not in ("--model", "--host", "--port")]
    check(len(extra_flags) == 0, f"empty profile has no extra flags, got: {extra_flags}")

    # --- Profile with user_set but value == default: skipped (defense in depth) ---
    default_opt = LLAMA_OPTION_CATALOG.get("cache_type_k")
    default_val = default_opt.default.to_json() if default_opt and default_opt.default else "f16"
    settings_default = SettingValueMap()
    settings_default = settings_default.with_value(default_opt, LlamaOptionValue(OptionKind.STRING, default_val))
    profile_default = ModelProfile(
        id="default-test",
        model_id="model-a",
        name="Default test",
        settings=settings_default,
        user_set={"cache_type_k"},  # user-set but value == default
    )
    argv_def = build_argv(config, model, profile_default)
    argv_def_str = " ".join(argv_def)
    check("--cache-type-k" not in argv_def_str, "user-set option at default value skipped (defense in depth)")

    print("\nAll Section 6 smoke tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
