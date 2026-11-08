"""Framework diagnostics service.

UI-independent. Mirrors the Phase 1 evidence collected for the framework
decision (session type, GPU vendor, Wayland display, portal state, available
Qt platform plugins) so the Diagnostics page can surface it directly.

The Qt platform name is read from a live ``QGuiApplication`` if one exists;
otherwise it is derived from the ``QT_QPA_PLATFORM`` environment variable.
This keeps the module importable and testable without spinning up a Qt
event loop, while still reporting the real value at runtime.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class GpuVendor(str, Enum):
    NVIDIA = "Nvidia"
    AMD = "Amd"
    INTEL = "Intel"
    UNKNOWN = "Unknown"


# PCI vendor IDs from http://pcisig.com/membership/member-companies.
_PCI_VENDOR_NVIDIA = "0x10de"
_PCI_VENDOR_AMD = ("0x1002", "0x1022")
_PCI_VENDOR_INTEL = "0x8086"


@dataclass
class WorkaroundInputs:
    """Inputs that drove any past framework-workaround decision.

    Tauri/WebKitGTK required ``__NV_DISABLE_EXPLICIT_SYNC`` on KDE Wayland +
    NVIDIA. The Qt shell has no such requirement, but the diagnostics page
    surfaces the equivalent env state so users can see why a workaround was
    ever needed.
    """

    xdg_session_type: Optional[str] = None
    nvidia_driver_present: bool = False
    env_already_set: bool = False
    set_by_us: bool = False


@dataclass
class FrameworkDiagnostics:
    framework: str = "qt-python"
    dialog_backend: str = "qt-native"
    xdg_session_type: Optional[str] = None
    xdg_current_desktop: Optional[str] = None
    desktop_session: Optional[str] = None
    gdk_backend: Optional[str] = None
    wayland_display: Optional[str] = None
    display: Optional[str] = None
    qt_qpa_platform: Optional[str] = None
    qt_platform_name: Optional[str] = None  # live QGuiApplication value
    qt_platform_plugin_path: Optional[str] = None
    qt_available_platforms: list[str] = field(default_factory=list)
    portal_descriptors: list[str] = field(default_factory=list)
    active_portal_name: Optional[str] = None
    portal_dbus_reachable: bool = False
    gpu_vendor: GpuVendor = GpuVendor.UNKNOWN
    gpu_driver_version: Optional[str] = None
    nvidia_driver_present: bool = False
    explicit_sync_disabled: bool = False
    workaround_applied: bool = False
    workaround_inputs: WorkaroundInputs = field(default_factory=WorkaroundInputs)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["gpu_vendor"] = self.gpu_vendor.value
        return d


# ---------------------------------------------------------------------------
# Helpers (small, focused, side-effect free where possible)
# ---------------------------------------------------------------------------


def _env(name: str) -> Optional[str]:
    """Return trimmed env var or None if missing/empty."""
    val = os.environ.get(name)
    if val is None:
        return None
    val = val.strip()
    return val or None


def _read_trimmed(path: Path) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return text or None


def portal_descriptors() -> list[str]:
    """List ``.portal`` descriptor files in the system portal directory."""
    portal_dir = Path("/usr/share/xdg-desktop-portal/portals")
    if not portal_dir.is_dir():
        return []
    out: list[str] = []
    for entry in portal_dir.iterdir():
        if entry.is_file() and entry.suffix == ".portal":
            out.append(entry.name)
    out.sort()
    return out


def detect_gpu_vendor(drm_root: Path = Path("/sys/class/drm")) -> GpuVendor:
    """Detect primary GPU vendor from sysfs.

    Walks ``/sys/class/drm/*/device/vendor`` and returns the first recognized
    PCI vendor. NVIDIA wins ties because that's the one this app needs to
    warn about on Wayland.
    """
    if not drm_root.is_dir():
        return GpuVendor.UNKNOWN
    found_amd = False
    found_intel = False
    for entry in drm_root.iterdir():
        vendor_path = entry / "device" / "vendor"
        if not vendor_path.is_file():
            continue
        vendor = _read_trimmed(vendor_path)
        if vendor is None:
            continue
        if vendor == _PCI_VENDOR_NVIDIA:
            return GpuVendor.NVIDIA
        if vendor in _PCI_VENDOR_AMD:
            found_amd = True
        elif vendor == _PCI_VENDOR_INTEL:
            found_intel = True
    if found_amd:
        return GpuVendor.AMD
    if found_intel:
        return GpuVendor.INTEL
    return GpuVendor.UNKNOWN


def nvidia_driver_version() -> Optional[str]:
    """Return the first line of ``/proc/driver/nvidia/version`` if present."""
    return _read_trimmed(Path("/proc/driver/nvidia/version"))


# Qt platform plugin search paths. Mirrors the resolution order documented at
# https://doc.qt.io/qt-6/qpa.html#qpa-plugins — env var first, then the
# install-time default baked into QtCore.
_DEFAULT_QT_PLUGIN_PATHS = (
    "/usr/lib64/qt6/plugins/platforms",
    "/usr/lib/qt6/plugins/platforms",
    "/usr/lib/x86_64-linux-gnu/qt6/plugins/platforms",
    "/usr/local/lib/qt6/plugins/platforms",
)


def _qt_plugin_search_paths() -> list[Path]:
    paths: list[Path] = []
    env_path = _env("QT_QPA_PLATFORM_PLUGIN_PATH")
    if env_path:
        paths.append(Path(env_path))
    qt_dir = _env("QT_PLUGIN_PATH")
    if qt_dir:
        # Platform plugins live under the "platforms" subdir of each plugin path.
        for p in qt_dir.split(":"):
            if p:
                paths.append(Path(p) / "platforms")
    for default in _DEFAULT_QT_PLUGIN_PATHS:
        paths.append(Path(default))
    # De-dup while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def available_qt_platform_plugins() -> list[str]:
    """Return sorted names of Qt platform plugins discoverable on disk.

    Names are derived from ``libq<name>.so`` filenames. Only the basename
    is reported, which matches what ``QGuiApplication.platformName()`` and
    ``QT_QPA_PLATFORM`` accept.
    """
    out: set[str] = set()
    for directory in _qt_plugin_search_paths():
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            if not (name.startswith("libq") and name.endswith(".so")):
                continue
            stem = name[len("libq") : -len(".so")]
            if stem:
                out.add(stem)
    return sorted(out)


def _live_qt_platform_name() -> Optional[str]:
    """Read platform name from a live ``QGuiApplication`` if one exists.

    Importing QtWidgets is deferred so this module is safe to import from a
    non-Qt context (tests, smoke scripts). Returns ``None`` if Qt is not
    importable or no application has been created yet.
    """
    try:
        from PySide6.QtGui import QGuiApplication  # type: ignore
    except Exception:
        return None
    app = QGuiApplication.instance()
    if app is None:
        return None
    name = app.platformName()
    return name or None


def _portal_dbus_reachable(timeout: float = 1.0) -> bool:
    """Best-effort portal DBus reachability check via ``dbus-send``."""
    if shutil.which("dbus-send") is None:
        return False
    try:
        result = subprocess.run(
            [
                "dbus-send",
                "--session",
                "--print-reply",
                "--dest=org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.DBus.Peer.Ping",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _active_portal_name(
    descriptors: list[str], desktop: Optional[str]
) -> Optional[str]:
    desktop_lc = (desktop or "").lower()
    if "kde" in desktop_lc and "kde.portal" in descriptors:
        return "kde.portal"
    if "gnome" in desktop_lc and "gnome.portal" in descriptors:
        return "gnome.portal"
    for name in descriptors:
        if name.endswith(".portal"):
            return name
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def framework_diagnostics() -> FrameworkDiagnostics:
    """Collect framework diagnostics equivalent to Phase 1 evidence.

    Safe to call from any thread; performs only filesystem reads, one optional
    ``dbus-send`` call, and at most one Qt lookup (which short-circuits to
    ``None`` when no ``QGuiApplication`` is alive).
    """
    xdg_session_type = _env("XDG_SESSION_TYPE")
    xdg_current_desktop = _env("XDG_CURRENT_DESKTOP")
    desktop_session = _env("DESKTOP_SESSION")
    gdk_backend = _env("GDK_BACKEND")
    wayland_display = _env("WAYLAND_DISPLAY")
    display = _env("DISPLAY")
    qt_qpa_platform = _env("QT_QPA_PLATFORM")
    explicit_sync_disabled = _env("__NV_DISABLE_EXPLICIT_SYNC") is not None
    nvidia_present = Path("/proc/driver/nvidia/version").exists()

    descriptors = portal_descriptors()
    qt_platform_name = _live_qt_platform_name()

    return FrameworkDiagnostics(
        framework="qt-python",
        dialog_backend="qt-native",
        xdg_session_type=xdg_session_type,
        xdg_current_desktop=xdg_current_desktop,
        desktop_session=desktop_session,
        gdk_backend=gdk_backend,
        wayland_display=wayland_display,
        display=display,
        qt_qpa_platform=qt_qpa_platform,
        qt_platform_name=qt_platform_name,
        qt_platform_plugin_path=_env("QT_QPA_PLATFORM_PLUGIN_PATH"),
        qt_available_platforms=available_qt_platform_plugins(),
        portal_descriptors=descriptors,
        active_portal_name=_active_portal_name(descriptors, xdg_current_desktop),
        portal_dbus_reachable=_portal_dbus_reachable(),
        gpu_vendor=detect_gpu_vendor(),
        gpu_driver_version=nvidia_driver_version(),
        nvidia_driver_present=nvidia_present,
        explicit_sync_disabled=explicit_sync_disabled,
        # Qt has no equivalent of Tauri's NVIDIA workaround; track the
        # equivalent state for parity with the Phase 1 evidence so users can
        # see what the previous framework was reacting to.
        workaround_applied=explicit_sync_disabled and nvidia_present,
        workaround_inputs=WorkaroundInputs(
            xdg_session_type=xdg_session_type,
            nvidia_driver_present=nvidia_present,
            env_already_set=explicit_sync_disabled,
            # Qt never sets this; recorded for parity.
            set_by_us=False,
        ),
    )
