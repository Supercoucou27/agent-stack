"""UI-independent services for the native Qt app."""

from .diagnostics import FrameworkDiagnostics, GpuVendor, available_qt_platform_plugins, detect_gpu_vendor, framework_diagnostics, nvidia_driver_version, portal_descriptors
from .download_service import DownloadError, DownloadProgress, DownloadService, DownloadStatus, HfDownloadRequest, ProgressCallback, download_file
from .help_parser import ParsedOption, ParsedValueKind, parse_help_options
from .hugging_face import ContextFit, FitRecommendation, HardwareFit, HardwareProfile, MoeFit, HfConnectivity, HfFile, HfFilter, HfRepoSummary, HfSearchOutcome, HfSearchService, HuggingFaceSearchService, NotImplementedHfSearchService, check_hf_connectivity, compute_hardware_fit, normalize_model_card_markdown, recommended_profile_settings
from .library_scan import ScanResult, infer_quant, open_hf, read_card_cache, reveal_file, scan_library, scan_models_dir
from .llama_server import CommandProbe, LlamaServerProbe, validate_llama_server
from .option_schema import BinaryKey, RuntimeOption, RuntimeSchema, SchemaCache, build_runtime_schema
from .runtime import LlamaServerController, LogLine, RuntimeStatus, ServerState, build_argv, is_port_available
from .runtime_api import ApiStatus, HealthStatus, LlamaServerApiClient, SwitchResult

__all__ = [name for name in globals() if not name.startswith("_")]
