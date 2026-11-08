"""Smoke test for the profile cleanup migration.

The user's saved profiles (pre-Section-6 round-trip) have many
``raw_args`` entries for every schema field, even though the user never
touched them. The migration in ``ModelProfile.from_json`` runs
``clean_raw_args`` so loading a profile produces a minimal ``raw_args``
list. The same helper is applied at ``build_argv`` time.

What the migration CAN do automatically:
- drop catalog flags (the catalog handles them via settings/user_set),
- drop ``--flag 0`` / ``--flag ""`` pairs (natural defaults),
- drop boolean catalog flags (--mmap, --mlock, --cont-batching, etc.).

What the migration CANNOT do automatically:
- tell that ``--poll 50`` came from a round-trip rather than a real
  user choice, because ``50`` is a perfectly plausible user value.
  The user has to delete the stale profile for those.

The runtime ``build_argv`` path runs the same filter, so even if the
user never re-loads a profile, the argv at start time is clean.
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
    ProfileStore,
    SettingValueMap,
    clean_raw_args,
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
        pstore = ProfileStore(paths)

        natural_default_pairs = [
            ("--cpu-mask", ""),
            ("--keep", "0"),
            ("--n-gpu-layers", "0"),
            ("--lookup-cache-stride", "0"),
            ("--draft", "0"),
            ("--draft-min", "0"),
        ]
        catalog_boolean_flags = [
            "--mmap",
            "--mlock",
            "--cont-batching",
            "--log-disable",
            "--metrics",
        ]
        real_user_args = [
            "--api-prefix",
            "--chat-template-kwargs", "replace-me",
            "-fa",
        ]
        bogus = []
        for flag, value in natural_default_pairs:
            bogus.extend([flag, value])
        bogus.extend(catalog_boolean_flags)

        pstore.upsert(ModelProfile(
            id="leaked",
            model_id="m1",
            name="Run profile",
            settings=SettingValueMap(),
            raw_args=bogus + real_user_args,
        ))

        loaded = pstore.list_for_model("m1")[0]

        # Catalog boolean flags and natural-default pairs are dropped.
        for flag, value in natural_default_pairs:
            check(flag not in loaded.raw_args,
                  f"migration dropped natural-default pair {flag} {value!r}")
        for flag in catalog_boolean_flags:
            check(flag not in loaded.raw_args,
                  f"migration dropped catalog boolean {flag}")

        # User-typed unknowns survive.
        check("--api-prefix" in loaded.raw_args, "user-typed --api-prefix survives")
        check("--chat-template-kwargs" in loaded.raw_args, "user-typed --chat-template-kwargs survives")
        check("replace-me" in loaded.raw_args, "user-typed 2048 value survives")
        check("-fa" in loaded.raw_args, "user-typed non-catalog short flag -fa survives")

        # The user's actual target command line.
        config = AppConfig(llama_server_path="/bin/sh",
                           host="0.0.0.0", port=8080)
        model = LocalModel(id="m1", path="/home/npittas/Downloads/models/foo.gguf")
        target = ModelProfile(
            id="target", model_id="m1", name="target",
            settings=SettingValueMap()
                .with_value(LLAMA_OPTION_CATALOG.get("ctx_size"), 16384)
                .with_value(LLAMA_OPTION_CATALOG.get("n_gpu_layers"), 99),
            raw_args=["--api-prefix", "--chat-template-kwargs", "replace-me", "-fa"],
            user_set={"ctx_size", "n_gpu_layers"},
        )
        argv = build_argv(config, model, target)
        check(argv[0] == "/bin/sh", "argv[0] is the binary")
        check("--host" in argv and "0.0.0.0" in argv, "argv has --host 0.0.0.0")
        check("--port" in argv and "8080" in argv, "argv has --port 8080")
        check("--ctx-size" in argv and "16384" in argv, "argv has --ctx-size 16384")
        check("--n-gpu-layers" in argv and "99" in argv, "argv has --n-gpu-layers 99")
        check("--api-prefix" in argv, "argv has --api-prefix")
        check("--chat-template-kwargs" in argv and "replace-me" in argv,
              "argv has --chat-template-kwargs 2048")
        check("-fa" in argv, "argv has -fa")
        for leak in ("--cpu-mask", "--poll", "--lookup-cache-stride",
                     "--batch-size", "--top-p", "--escape", "--mmap",
                     "--mlock", "--cont-batching", "--metrics",
                     "--threads", "--parallel", "--predict", "--keep"):
            check(leak not in argv, f"argv: {leak} not present")

        # A "stale" profile with --poll 50 etc. still produces those in
        # argv until the user deletes it. The migration can't tell.
        # Stale profile test: --threads is a catalog flag, so its value
        # is dropped at build time (the migration removes --threads from
        # raw_args and there's no value in settings). --poll is NOT in
        # the catalog, so the user must delete the profile to clean it.
        stale = ModelProfile(
            id="stale", model_id="m1", name="stale",
            settings=SettingValueMap(),
            raw_args=["--poll", "50", "--api-prefix"],
        )
        loaded_stale = pstore.list_for_model("m1")
        for prof in loaded_stale:
            if prof.id == "stale":
                stale = prof
                break
        argv_stale = build_argv(config, model, stale)
        check("--poll" in argv_stale, "stale profile: --poll=50 still in argv (user must delete)")
        check("--api-prefix" in argv_stale, "stale profile: --api-prefix survives")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
