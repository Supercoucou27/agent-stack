from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
QT_ROOT = REPO_ROOT / "qt_app"
for candidate in (REPO_ROOT, QT_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.services import (  # noqa: E402
    FrameworkDiagnostics,
    GpuVendor,
    HardwareProfile,
    HfFile,
    LlamaServerProbe,
    available_qt_platform_plugins,
    framework_diagnostics,
    compute_hardware_fit,
    normalize_model_card_markdown,
    recommended_profile_settings,
    validate_llama_server,
)
from llama_data import AppConfig, ConfigStore, LibraryStore, LocalModel, ModelProfile, ProfileStore, default_paths  # noqa: E402
from app.services.library_scan import ScanResult, infer_quant, is_companion_gguf, scan_library  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"pass {message}")


def main() -> int:
    diag = framework_diagnostics()
    check(isinstance(diag, FrameworkDiagnostics), "framework diagnostics type")
    check(isinstance(diag.gpu_vendor, GpuVendor), "gpu vendor enum")
    check("wayland" in available_qt_platform_plugins(), "Qt Wayland plugin discoverable")
    json.dumps(diag.to_dict())

    missing = validate_llama_server("/nonexistent/llama-server")
    check(isinstance(missing, LlamaServerProbe), "llama probe type")
    check(not missing.exists, "missing binary rejected")

    with tempfile.TemporaryDirectory() as td:
        paths = default_paths(Path(td))
        config_store = ConfigStore(paths)
        config_store.save(AppConfig(port=8080))
        check(config_store.load().port == 8080, "config round trip")

        library_store = LibraryStore(paths)
        library_store.upsert(LocalModel(id="model-1", path="/tmp/model.gguf"))
        check(library_store.load()[0].id == "model-1", "library round trip")

        profile_store = ProfileStore(paths)
        profile_store.upsert(ModelProfile(id="profile-1", model_id="model-1", name="Default"))
        check(profile_store.list_for_model("model-1")[0].name == "Default", "profile round trip")

        # Phase 7: library scan
        scan_dir = Path(td) / "models"
        scan_dir.mkdir()
        (scan_dir / "test-Q4_K_M.gguf").write_bytes(b"\x00" * 1024)
        (scan_dir / "other-BF16.gguf").write_bytes(b"\x00" * 2048)
        (scan_dir / "not-a-model.txt").write_text("ignore me")

        # Scan with fresh library store so only scanned files appear.
        scan_paths = default_paths(Path(td) / "scan-data")
        scan_lib = LibraryStore(scan_paths)
        result = scan_library(scan_dir, scan_lib)
        check(isinstance(result, ScanResult), "scan result type")
        check(result.scanned_files == 2, f"scanned 2 gguf files, got {result.scanned_files}")
        check(result.added == 2, f"added 2 models, got {result.added}")
        check(result.removed == 0, f"removed 0, got {result.removed}")

        models = scan_lib.load()
        check(len(models) == 2, f"library has 2 scanned models, got {len(models)}")
        scanned = [m for m in models if m.path.startswith(str(scan_dir))]
        check(len(scanned) == 2, f"2 scanned models, got {len(scanned)}")
        quants = {m.quant for m in scanned}
        check("Q4_K_M" in quants, f"Q4_K_M quant inferred, got {quants}")
        check("BF16" in quants, f"BF16 quant inferred, got {quants}")
        sizes = {m.size_bytes for m in scanned}
        check(1024 in sizes, f"1024 byte file detected, got {sizes}")
        check(2048 in sizes, f"2048 byte file detected, got {sizes}")

        # Re-scan: should keep existing, not re-add.
        result2 = scan_library(scan_dir, scan_lib)
        check(result2.added == 0, f"re-scan added 0, got {result2.added}")
        check(result2.kept == 2, f"re-scan kept 2, got {result2.kept}")

        # Delete one file and re-scan: should remove stale entry.
        (scan_dir / "other-BF16.gguf").unlink()
        result3 = scan_library(scan_dir, scan_lib)
        check(result3.removed == 1, f"post-delete removed 1, got {result3.removed}")
        check(len(scan_lib.load()) == 1, f"library has 1 scanned model after removal, got {len(scan_lib.load())}")

        # infer_quant standalone
        check(infer_quant("model-Q8_0.gguf") == "Q8_0", "infer Q8_0")
        check(infer_quant("model-IQ3_S.gguf") == "IQ3_S", "infer IQ3_S")
        check(infer_quant("plain.gguf") is None, "no quant for plain filename")

    # Phase 7a: companion GGUF filter
    with tempfile.TemporaryDirectory() as td2:
        comp_dir = Path(td2) / "models"
        comp_dir.mkdir()
        (comp_dir / "model.Q4_K_M.gguf").write_bytes(b"\x00" * 512)
        (comp_dir / "mmproj-model.Q4_K_M.gguf").write_bytes(b"\x00" * 256)
        (comp_dir / "text-encoder-model.Q4_K_M.gguf").write_bytes(b"\x00" * 256)
        comp_paths = default_paths(Path(td2) / "lib-data")
        comp_lib = LibraryStore(comp_paths)
        result = scan_library(comp_dir, comp_lib)
        check(result.scanned_files == 1, f"companion scan found 1 gguf, got {result.scanned_files}")
        check(result.added == 1, f"companion scan added 1, got {result.added}")
        models = comp_lib.load()
        check(len(models) == 1, f"companion library has 1 model, got {len(models)}")
        check(models[0].path.endswith("model.Q4_K_M.gguf"), f"kept primary model, path={models[0].path}")

    # is_companion_gguf unit checks
    check(is_companion_gguf(Path("mmproj-X.gguf")), "is_companion mmproj")
    check(is_companion_gguf(Path("text-encoder-X.gguf")), "is_companion text-encoder")
    check(is_companion_gguf(Path("vision-encoder-X.gguf")), "is_companion vision-encoder")
    check(is_companion_gguf(Path("embedding-model.gguf")), "is_companion embedding")
    check(not is_companion_gguf(Path("my_model.gguf")), "normal model not companion")
    check(not is_companion_gguf(Path("llama_embedding_model.gguf")), "_embedding not companion")

    # Step 0: mmproj_path auto-detection and round-trip
    with tempfile.TemporaryDirectory() as td3:
        paths3 = default_paths(Path(td3))
        models_dir = Path(td3) / "models"
        models_dir.mkdir()
        (models_dir / "model.Q4.gguf").write_bytes(b"x")
        (models_dir / "mmproj-model.fp16.gguf").write_bytes(b"y")
        lib3 = LibraryStore(paths3)
        result3 = scan_library(models_dir, lib3)
        check(result3.added == 1, "scan added model with mmproj")
        models3 = lib3.load()
        check(len(models3) == 1, "library has one model with mmproj")
        check(models3[0].mmproj_path is not None, "mmproj_path is set")
        check(models3[0].mmproj_path.endswith("mmproj-model.fp16.gguf"), f"mmproj_path correct: {models3[0].mmproj_path}")

    with tempfile.TemporaryDirectory() as td4:
        paths4 = default_paths(Path(td4))
        models_dir = Path(td4) / "models"
        models_dir.mkdir()
        (models_dir / "model.Q4.gguf").write_bytes(b"x")
        lib4 = LibraryStore(paths4)
        result4 = scan_library(models_dir, lib4)
        check(result4.added == 1, "scan added model without mmproj")
        models4 = lib4.load()
        check(len(models4) == 1, "library has one model without mmproj")
        check(models4[0].mmproj_path is None, "mmproj_path is None when no companion")

    # Round-trip
    m = LocalModel(id="a", path="a.gguf", mmproj_path="mmproj.gguf")
    check(m.to_json().get("mmproj_path") == "mmproj.gguf", "to_json writes mmproj_path")
    m2 = LocalModel(id="b", path="b.gguf")
    check("mmproj_path" not in m2.to_json(), "to_json omits None mmproj_path")
    m3 = LocalModel.from_json({"id": "c", "path": "c.gguf", "companion_paths": ["mmproj-c.gguf"]})
    check(m3.mmproj_path == "mmproj-c.gguf", "from_json infers mmproj_path from companion_paths")
    m4 = LocalModel.from_json({"id": "d", "path": "d.gguf"})
    check(m4.mmproj_path is None, "from_json leaves mmproj_path None when no companion")

    # Discovery hardware fit: weights + projector + MTP/draft + long-context KV cache.
    fit = compute_hardware_fit(
        [
            HfFile("Qwen2.5-7B-Instruct-Q4_K_M.gguf", size_bytes=4_700_000_000, quantization="Q4_K_M"),
            HfFile("mmproj-Qwen2.5-7B-f16.gguf", size_bytes=700_000_000, is_multimodal_projector=True),
            HfFile("Qwen2.5-7B-draft-Q4_K_M.gguf", size_bytes=900_000_000, quantization="Q4_K_M"),
        ],
        HardwareProfile(ram_bytes=64 * 1024**3, vram_bytes=24 * 1024**3, cpu_threads=16),
    )
    check(fit is not None, "hardware fit computed")
    check(fit.projector_bytes == 700_000_000, "projector bytes included")
    check(fit.mtp_bytes == 900_000_000, "mtp/draft bytes included")
    check(len(fit.contexts) == 4 and fit.contexts[-1].context_tokens == 131_072, "16K-128K contexts estimated")
    check(fit.quantization == "Q4_K_M", "selected quant retained")
    check("Q4_K_M" in fit.summary(), "fit summary shows quant")
    check("quant Q4_K_M" in fit.detail(), "fit detail shows quant")
    check(fit.contexts[-1].kv_cache_bytes > fit.contexts[0].kv_cache_bytes, "kv cache scales with context")
    check("128K ctx" in fit.detail(), "fit detail shows long context")
    moe_fit = compute_hardware_fit(
        [HfFile("Mixtral-8x7B-Instruct-Q4_K_M.gguf", size_bytes=26_000_000_000, quantization="Q4_K_M")],
        HardwareProfile(ram_bytes=96 * 1024**3, vram_bytes=24 * 1024**3, cpu_threads=16),
    )
    check(moe_fit is not None and moe_fit.moe is not None, "moe metadata inferred")
    check(moe_fit.moe.total_experts == 8 and moe_fit.moe.active_experts == 2, "mixtral expert counts inferred")
    check("MoE:" in moe_fit.detail(), "moe detail shown")
    rec = recommended_profile_settings(fit, HardwareProfile(ram_bytes=64 * 1024**3, vram_bytes=24 * 1024**3, cpu_threads=16))
    check(rec.settings["ctx_size"] in {16_384, 32_768, 65_536, 131_072}, "recommendation context from fit table")
    check(rec.settings["flash_attn"] in {"on", "auto"}, "recommendation flash attention set")
    check("cache_type_k" in rec.settings and "cache_type_v" in rec.settings, "recommendation kv cache settings set")
    rendered = normalize_model_card_markdown(
        "# Card\n\n<ul><li>First point</li><li><b>Second</b> point</li></ul>\n"
        "<table><tr><th>Name</th><th>Value</th></tr><tr><td>Context</td><td>32768</td></tr></table>"
    )
    check("- First point" in rendered, "html bullet text preserved")
    check("**Second** point" in rendered, "html inline emphasis preserved")
    check("| Name | Value |" in rendered and "| Context | 32768 |" in rendered, "html table text preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
