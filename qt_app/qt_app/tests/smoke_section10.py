"""Smoke test for the default-leak fix.

A pre-Section-6 config or profile can have values that match the catalog
default but were baked in by old code. ``SettingValueMap.to_argv``,
``build_argv``, and the ``raw_args`` filter must all skip those values
so they do not leak onto the command line.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llama_data import (  # noqa: E402
    AppConfig,
    LLAMA_OPTION_CATALOG,
    LocalModel,
    ModelProfile,
    SettingValueMap,
    default_paths,
)
from app.services.runtime import build_argv  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"pass {message}")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        paths.ensure()

        opt = LLAMA_OPTION_CATALOG.get("lookup_cache_stride")
        check(opt is not None, "catalog has lookup_cache_stride")
        check(opt.flag == "--lookup-cache-stride", "flag is --lookup-cache-stride")
        check(opt.default is None, "lookup_cache_stride has no catalog default")

        # Simulate a leaked entry: an old config saved `lookup_cache_stride: 0`.
        global_settings = SettingValueMap().with_value(opt, 0)
        check(opt.id in global_settings, "global_settings has lookup_cache_stride (simulating old config)")

        config = AppConfig(
            llama_server_path="/bin/true",
            host="127.0.0.1",
            port=18080,
            global_settings=global_settings,
        )
        model = LocalModel(id="m1", path="/tmp/model.gguf")
        profile = ModelProfile(id="p1", model_id="m1", name="default")

        argv = build_argv(config, model, profile)
        check("--model" in argv, "argv has --model")
        check("--host" in argv, "argv has --host")
        check("--port" in argv, "argv has --port")
        check("--lookup-cache-stride" not in argv,
              "lookup_cache_stride=0 is NOT emitted (was the bug)")
        check("--cache-type-k" not in argv, "cache_type_k at default is NOT emitted")
        check("--cache-type-v" not in argv, "cache_type_v at default is NOT emitted")
        check("--draft-min" not in argv, "draft_min at default is NOT emitted")

        # Sanity: when the user *does* explicitly set a value, it is emitted.
        settings = SettingValueMap()
        for opt_id, val in {"ctx_size": 8192, "cache_type_k": "q8_0"}.items():
            settings = settings.with_value(LLAMA_OPTION_CATALOG.get(opt_id), val)
        user_profile = ModelProfile(
            id="p2", model_id="m1", name="custom",
            settings=settings, user_set={"ctx_size", "cache_type_k"},
        )
        argv2 = build_argv(config, model, user_profile)
        check("--ctx-size" in argv2, "user-set ctx_size is emitted")
        check("8192" in argv2, "8192 is in argv")
        check("--cache-type-k" in argv2, "user-set cache_type_k is emitted")
        check("q8_0" in argv2, "q8_0 is in argv")
        check("--lookup-cache-stride" not in argv2,
              "lookup_cache_stride still not emitted when other user-set values are present")

        # Sanity: when a value is explicitly set in global_settings (not the
        # default), it IS emitted.
        explicit_global = SettingValueMap().with_value(opt, 64)
        config2 = AppConfig(
            llama_server_path="/bin/true",
            host="127.0.0.1",
            port=18080,
            global_settings=explicit_global,
        )
        argv3 = build_argv(config2, model, profile)
        check("--lookup-cache-stride" in argv3,
              "lookup_cache_stride=64 (non-default) IS emitted")
        check("64" in argv3, "64 is in argv")

        # Reproduce the user's real failure (catalog): a profile that was
        # saved by pre-Section-6 code, with every catalog field baked into
        # settings+user_set. After the runtime.py defense-in-depth fix,
        # only the values that the user *actually* changed (or non-default
        # values) should appear in argv.
        leaked_settings = SettingValueMap()
        leaked_user_set = set()
        for o in LLAMA_OPTION_CATALOG:
            if o.kind.value == "boolean":
                continue
            if o.default is None:
                if o.kind.value == "integer":
                    v = 0
                elif o.kind.value == "float":
                    v = 0.0
                elif o.kind.value == "string":
                    v = ""
                else:
                    v = []
            else:
                v = o.default.value
            leaked_settings = leaked_settings.with_value(o, v)
            leaked_user_set.add(o.id)
        leaked_profile = ModelProfile(
            id="p3", model_id="m1", name="leaked",
            settings=leaked_settings, user_set=leaked_user_set,
        )
        argv4 = build_argv(
            AppConfig(llama_server_path="/bin/true", host="127.0.0.1", port=8080),
            model, leaked_profile,
        )
        check(len(argv4) <= 8,
              f"leaked profile produces short argv (got {len(argv4)} elements)")
        check("--lookup-cache-stride" not in argv4,
              "leaked profile: --lookup-cache-stride not in argv")
        check("--threads" not in argv4,
              "leaked profile: --threads=0 not in argv")
        check("--poll" not in argv4,
              "leaked profile: --poll=50 not in argv")

        # Reproduce the second user failure (raw_args): a profile whose
        # raw_args round-trip every schema field. After the _filter_raw_args
        # pass, only non-natural-default values and boolean flags survive.
        bogus_raw_args = [
            "--cpu-mask", "",
            "--cpu-mask-batch", "same as --cpu-mask",
            "--poll", "0",
            "--predict", "-1",
            "--keep", "0",
            "--escape",
            "--yarn-ext-factor", "0",
            "--hf-token", "value from HF_TOKEN environment",
            "--samplers", "penalties",
            "--op-offload",
        ]
        raw_profile = ModelProfile(
            id="p4", model_id="m1", name="raw",
            settings=SettingValueMap(), user_set=set(),
            raw_args=bogus_raw_args,
        )
        argv5 = build_argv(
            AppConfig(llama_server_path="/bin/true", host="127.0.0.1", port=8080),
            model, raw_profile,
        )
        check("--cpu-mask" not in argv5,
              "raw_args: --cpu-mask \"\" pair is dropped")
        check("--poll" not in argv5, "raw_args: --poll 0 is dropped")
        check("--predict" not in argv5, "raw_args: --predict -1 is dropped")
        check("--keep" not in argv5, "raw_args: --keep 0 is dropped")
        check("--yarn-ext-factor" not in argv5,
              "raw_args: --yarn-ext-factor 0 is dropped")
        check("--escape" in argv5, "raw_args: --escape (boolean) survives")
        check("--op-offload" in argv5, "raw_args: --op-offload (boolean) survives")
        check("--samplers" in argv5, "raw_args: --samplers penalties survives")
        check("--hf-token" in argv5,
              "raw_args: --hf-token with non-default value survives")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
