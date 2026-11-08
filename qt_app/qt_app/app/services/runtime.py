"""Local llama-server process lifecycle controller.

Builds argv from ``AppConfig`` + ``ModelProfile``, manages start/stop/restart,
captures stdout/stderr with timestamps, tracks health via the API client, and
detects port conflicts before launch.

This module is UI-independent. Qt signals / UI wiring live in the app layer.
"""
from __future__ import annotations

import json

import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Deque, List, Optional, Sequence

# Health poll interval (seconds) and per-request timeout (seconds).
# These are used by the background health-polling thread.
_HEALTH_INTERVAL = 1.0
import os as _os

_HEALTH_TIMEOUT = 2.0

from llama_data import AppConfig, LLAMA_OPTION_CATALOG, LocalModel, ModelProfile, OptionKind, clean_raw_args, default_data_dir

from .runtime_api import ApiStatus, LlamaServerApiClient


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class ServerState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"
    EXITED = "exited"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Log capture
# ---------------------------------------------------------------------------


@dataclass
class LogLine:
    source: str          # "stdout" | "stderr"
    text: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class LogBuffer:
    """Thread-safe ring buffer that accumulates :class:`LogLine` entries.

    Provides ``search`` (case-insensitive substring match) and ``clear``.
    """

    def __init__(self, maxlen: int = 10_000) -> None:
        self._lines: Deque[LogLine] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, line: LogLine) -> None:
        with self._lock:
            self._lines.append(line)

    def lines(self) -> List[LogLine]:
        with self._lock:
            return list(self._lines)

    def search(self, query: str, source: Optional[str] = None) -> List[LogLine]:
        q = query.lower()
        with self._lock:
            return [
                ln
                for ln in self._lines
                if q in ln.text.lower()
                and (source is None or ln.source == source)
            ]

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._lines)

    def extend(self, lines: Sequence[LogLine]) -> None:
        with self._lock:
            self._lines.extend(lines)


LogCallback = Callable[[LogLine], None]


# ---------------------------------------------------------------------------
# Status snapshot
# ---------------------------------------------------------------------------


@dataclass
class RuntimeStatus:
    state: ServerState
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    last_error: Optional[str] = None
    command: list[str] = field(default_factory=list)
    host: str = "127.0.0.1"
    port: int = 8080
    model_path: Optional[str] = None
    profile_name: Optional[str] = None
    api_status: Optional[ApiStatus] = None

    @property
    def is_running(self) -> bool:
        return self.state in {ServerState.RUNNING, ServerState.HEALTHY, ServerState.UNHEALTHY}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_port_available(host: str, port: int) -> bool:
    """Return *True* when nothing is listening on *host*:*port*."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0



def generate_models_preset(
    library_models: Sequence[LocalModel],
    profile_defaults: dict[str, ModelProfile],
    models_dir: str,
) -> str:
    """Write a ``--models-preset`` INI listing every runnable model.

    Companion GGUFs (mmproj, text-encoder, etc.) are excluded so they
    never appear as selectable models.  Every real model gets a section
    with at least ``model = /path`` and ``mmproj = /path`` (if detected).
    Profile settings are overlaid on top when available.

    Because every model is explicitly listed, llama-server does not need
    ``--models-dir`` — the preset alone defines the full model catalogue.
    """
    from ..services.library_scan import is_companion_gguf

    sections: list[str] = []
    models_dir_resolved = Path(models_dir).resolve()

    for model in library_models:
        model_path = Path(model.path).resolve()
        try:
            model_path.relative_to(models_dir_resolved)
        except ValueError:
            continue

        # Skip companion GGUFs (mmproj, text-encoder, etc.).
        if is_companion_gguf(model_path):
            continue

        profile = profile_defaults.get(model.id)
        entries: list[str] = [f"model = {model.path}"]
        mmproj_written = False

        if profile is not None:
            user_set = getattr(profile, "user_set", None) or set()
            skip_ids = {"model", "host", "port", "extra_args", "metrics"}
            for option_id, value in profile.settings.items():
                if option_id in skip_ids or option_id not in user_set:
                    continue
                option = LLAMA_OPTION_CATALOG.get(option_id)
                if option is None:
                    continue
                if option.default is not None and value.value == option.default.value:
                    continue
                if option.default is None and value.value in (False, 0, 0.0, "", [], None):
                    continue
                ini_key = option.flag.lstrip("-")
                if option_id == "mmproj":
                    mmproj_written = True
                if value.kind is OptionKind.BOOLEAN:
                    ini_val = "true" if value.value else "false"
                else:
                    ini_val = str(value.value)
                entries.append(f"{ini_key} = {ini_val}")

            raw = clean_raw_args(profile.raw_args)
            i = 0
            while i < len(raw):
                tok = raw[i]
                if tok.startswith("--"):
                    ini_key = tok.lstrip("-")
                    if ini_key == "mmproj":
                        mmproj_written = True
                    # Skip metrics — we force it at the end.
                    if ini_key == "metrics":
                        if i + 1 < len(raw) and not raw[i + 1].startswith("--"):
                            i += 2
                        else:
                            i += 1
                        continue
                    if i + 1 < len(raw) and not raw[i + 1].startswith("--"):
                        entries.append(f"{ini_key} = {raw[i + 1]}")
                        i += 2
                    else:
                        entries.append(f"{ini_key} = true")
                        i += 1
                else:
                    i += 1

        # Auto-attach mmproj from the library record if not already set.
        if not mmproj_written and model.mmproj_path:
            entries.append(f"mmproj = {model.mmproj_path}")

        # Enable /metrics on child instances so loaded models expose
        # Prometheus-style metrics.  Children are regular llama-servers,
        # not routers — they don't self-poll.
        entries.append("metrics = true")

        section_name = model_path.stem
        section = f"[{section_name}]\n" + "\n".join(entries)
        sections.append(section)

    ini_path = default_data_dir() / "models-preset.ini"
    ini_path.parent.mkdir(parents=True, exist_ok=True)
    ini_path.write_text("\n\n".join(sections) + "\n" if sections else "",
                        encoding="utf-8")
    return str(ini_path)


def build_argv(
    config: AppConfig,
    model: LocalModel,
    profile: Optional[ModelProfile] = None,
    *,
    models_preset_path: Optional[str] = None,
    models_max: int = 0,
) -> list[str]:
    """Build the full argv for llama-server from config, model, and profile.
    Precedence: profile override > Settings default (config) > llama.cpp catalog default.
    Only inject a flag when the effective value differs from the catalog default.
    """
    if not config.llama_server_path:
        raise ValueError("llama-server path is not configured")
    argv = [config.llama_server_path]
    # Determine effective host/port: profile > config > catalog
    host_opt = LLAMA_OPTION_CATALOG.get("host")
    port_opt = LLAMA_OPTION_CATALOG.get("port")
    host = config.host
    port = config.port
    if profile is not None:
        profile_host = profile.settings.get("host")
        if profile_host is not None and profile_host.value is not None:
            host = str(profile_host.value)
        profile_port = profile.settings.get("port")
        if profile_port is not None and profile_port.value is not None:
            port = int(profile_port.value)
    # Only inject host if it differs from catalog default
    catalog_host = str(host_opt.default.value) if host_opt and host_opt.default else "127.0.0.1"
    if host != catalog_host:
        argv.extend(["--host", host])
    # Only inject port if it differs from catalog default
    catalog_port = int(port_opt.default.value) if port_opt and port_opt.default else 8080
    if port != catalog_port:
        argv.extend(["--port", str(port)])
    # Router mode: models are defined by the preset INI (no --models-dir).
    # Single-model mode: serve one specific .gguf file.
    if not config.router_mode:
        argv.extend(["--model", model.path])
    # Per-model preset for router mode.
    if models_preset_path:
        argv.extend(["--models-preset", models_preset_path])
    # Router mode: max simultaneously loaded models. This must have exactly
    # one source of truth. Do not let a saved global/default ``models_max``
    # override the Run page router control later in argv.
    if models_max > 0:
        argv.extend(["--models-max", str(models_max)])
    global_settings = config.global_settings
    if config.router_mode:
        global_settings = global_settings.without("models_max")
    argv.extend(global_settings.to_argv(LLAMA_OPTION_CATALOG))
    # Profile settings overlay — only include user-explicitly-set values
    # that differ from catalog defaults.  Keep host/port in skip_ids so
    # they are not double-emitted.
    if profile is not None:
        skip_ids = {"model", "host", "port"}
        user_set = getattr(profile, "user_set", None) or set()
        for option_id, value in profile.settings.items():
            if option_id in skip_ids:
                continue
            if option_id not in user_set:
                continue
            option = LLAMA_OPTION_CATALOG.get(option_id)
            if option is None:
                continue
            # Defense in depth: skip if value matches the catalog's explicit
            # default OR a natural default (False/0/0.0/""/[]/None) when the
            # option has no catalog default. This catches profiles saved by
            # pre-Section-6 code paths that baked every field into user_set.
            if option.default is not None and value.value == option.default.value:
                continue
            if option.default is None and value.value in (False, 0, 0.0, "", [], None):
                continue
            argv.extend(value.to_argv(option))
        # raw_args may still contain pre-Section-6 noise from the binary
        # schema. clean_raw_args (same code path as the load-time
        # migration) drops catalog flags and natural-default pairs.
        argv.extend(clean_raw_args(profile.raw_args))
    # Always enable /metrics so the dashboard can fetch metrics.
    # Force-inject to override existing profiles with metrics=false.
    argv = [a for a in argv if a not in ("--metrics", "--no-metrics")]
    argv.append("--metrics")
    # Router mode: disable autoload to prevent the router's internal
    # self-poll loop from triggering model loads and LRU eviction.
    # The user manages models through the Loaded Models panel.
    if config.router_mode:
        argv = [a for a in argv if a not in ("--no-models-autoload",)]
        argv.append("--no-models-autoload")
    return argv
class LlamaServerController:
    """Owns a local llama-server subprocess.

    Callers provide an optional ``on_log`` callback for real-time log
    streaming and/or use the built-in :attr:`log_buffer` for batch access.
    """

    def __init__(self, on_log: Optional[LogCallback] = None) -> None:
        self.on_log = on_log
        self.router_mode = False
        self.log_buffer = LogBuffer()
        self._process: Optional[subprocess.Popen[str]] = None
        self._status = RuntimeStatus(state=ServerState.STOPPED)
        self._lock = threading.Lock()
        self._api_client: Optional[LlamaServerApiClient] = None
        self._health_thread: Optional[threading.Thread] = None
        self._stop_health = threading.Event()
        # Each ``start`` increments ``_log_session``. Reader threads capture
        # the session id at spawn time; ``_emit`` drops lines whose
        # session does not match the current one, so stale log output
        # from a previous process can never bleed into the new log
        # buffer after a restart.
        self._log_session = 0
        self._log_path = default_data_dir() / "runtime_logs.jsonl"
        self._raw_log_path = default_data_dir() / "llama-server.log"
        self._log_file_handle = None


    def _status_copy_unlocked(self) -> RuntimeStatus:
        return RuntimeStatus(**self._status.__dict__)
    # -- public interface ---------------------------------------------------

    @property
    def status(self) -> RuntimeStatus:
        """Snapshot of the current process state (thread-safe copy)."""
        with self._lock:
            self._sync_state()
            return RuntimeStatus(**self._status.__dict__)

    def try_attach(self, host: str, port: int, router_mode: bool = False) -> bool:
        """Try to attach to an already-running llama-server on *host*:*port*.
        In router mode this must be non-invasive: even /health can be proxied
        to a model and trigger lazy loads/LRU eviction, so a TCP connect is the
        only probe.
        """
        self.router_mode = router_mode
        client = LlamaServerApiClient(host=host, port=port, timeout=2.0, router_mode=router_mode)
        if router_mode:
            try:
                connect_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
                with socket.create_connection((connect_host, port), timeout=1.0):
                    pass
            except OSError:
                return False
            api_status = ApiStatus(reachable=True, health="router")
        else:
            api_status = client.status()
            if not api_status.reachable:
                return False

        with self._lock:
            self._log_session += 1
            self.log_buffer.clear()
            self._load_persisted_logs()
            self._api_client = client
            self._status = RuntimeStatus(
                state=ServerState.HEALTHY,
                command=[],
                host=host,
                port=port,
                model_path=None,
                profile_name=None,
                api_status=api_status,
            )
            if self._raw_log_path.is_file():
                self._start_tail_reader(self._raw_log_path, self._log_session, start_at_end=True)
            self._start_health_poll(host, port)
        return True

    def start(
        self,
        argv: list[str],
        host: str = "127.0.0.1",
        port: int = 8080,
        model_path: Optional[str] = None,
        profile_name: Optional[str] = None,
        router_mode: bool = False,
    ) -> RuntimeStatus:
        """Launch llama-server with *argv*.
        Raises on pre-condition failure (already running, binary missing,
        port occupied).
        """
        with self._lock:
            self.router_mode = router_mode
            if self._process and self._process.poll() is None:
                raise RuntimeError("llama-server is already running")
            if not Path(argv[0]).is_file():
                raise FileNotFoundError(argv[0])
            if not is_port_available(host, port):
                raise RuntimeError(f"port {host}:{port} is already in use")

            # Bump the log session so any in-flight reader threads from a
            # previous process stop contributing to the (just-cleared)
            # log buffer. See _start_reader / _emit.
            self._log_session += 1
            self.log_buffer.clear()
            self._truncate_persisted_logs()
            self._raw_log_path.parent.mkdir(parents=True, exist_ok=True)
            self._raw_log_path.write_text("", encoding="utf-8")
            self._log_file_handle = self._raw_log_path.open("a", encoding="utf-8", buffering=1)
            self._status = RuntimeStatus(
                state=ServerState.STARTING,
                command=list(argv),
                host=host,
                port=port,
                model_path=model_path,
                profile_name=profile_name,
            )
            try:
                self._process = subprocess.Popen(
                    argv,
                    stdout=self._log_file_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    # Run in its own session so we can kill the
                    # whole process group (parent + any workers
                    # llama-server forks) cleanly. No-op on
                    # Windows, but harmless.
                    start_new_session=(sys.platform != "win32"),
                )
            except Exception as exc:
                self._status.state = ServerState.ERROR
                self._status.last_error = str(exc)
                raise

            self._status.state = ServerState.RUNNING
            self._status.pid = self._process.pid
            self._start_tail_reader(self._raw_log_path, self._log_session)
            self._start_health_poll(host, port)
            return self._status_copy_unlocked()
    def stop(
        self,
        graceful_timeout: float = 5.0,
        kill_timeout: float = 3.0,
    ) -> RuntimeStatus:
        """Stop the running process group.

        Sequence:
        1. ``SIGTERM`` to the whole process group (parent + workers).
        2. Wait up to ``graceful_timeout`` seconds for the parent
           to exit. If it does, we are done.
        3. ``SIGKILL`` to the whole process group. Wait up to
           ``kill_timeout`` seconds.
        4. If still alive, log a clear error and leave the state
           as ``ERROR`` (not ``STOPPED``) so the user knows to
           investigate. The next ``start()`` will fail on the
           port-already-in-use check, which is the desired
           fail-fast behavior.
        """
        with self._lock:
            stop_event = self._stop_health
            proc = self._process
            host = self._status.host
            port = self._status.port
            # Attached to an external server (no _process) — find its PID.
            if proc is None and self._status.state in (
                ServerState.RUNNING, ServerState.HEALTHY, ServerState.UNHEALTHY,
            ):
                stop_event.set()
                pid = self._find_pid_for_port(host, port)
                if pid is None:
                    self._status.state = ServerState.STOPPED
                    self._api_client = None
                    return self._status_copy_unlocked()
                # Kill the external process.
                self._status.state = ServerState.STOPPING
                self._kill_external(pid, graceful_timeout, kill_timeout)
                self._status.state = ServerState.STOPPED
                self._status.pid = None
                self._api_client = None
                return self._status_copy_unlocked()
            if not proc or proc.poll() is not None:
                stop_event.set()
                self._status.state = ServerState.STOPPED
                self._status.exit_code = proc.returncode if proc else None
                self._status.pid = None
                self._process = None
                self._api_client = None
                return self._status_copy_unlocked()
            self._status.state = ServerState.STOPPING
        # Signal the health-poll thread to stop. ``stop_event`` is the
        # per-session event captured above under the lock, so a
        # concurrent ``_start_health_poll`` cannot replace it from
        # under us.
        stop_event.set()
        pid = proc.pid
        # ``os.getpgid(pid)`` may raise if the process is already
        # gone; fall back to the pid as a group id.
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            pgid = pid
        with self._lock:
            self._status.state = ServerState.STOPPING
        # Helper that sends a signal to the whole process group
        # on POSIX, or falls back to a single-process kill on
        # Windows (where ``os.killpg`` is not available).
        def _kill_group(sig: int) -> None:
            if sys.platform == "win32":
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
            else:
                try:
                    os.killpg(pgid, sig)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # Race: pid was reaped by another owner. Best
                    # we can do is signal the leader directly.
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass

        # 1) Graceful: SIGTERM
        _kill_group(signal.SIGTERM)
        try:
            proc.wait(timeout=graceful_timeout)
        except subprocess.TimeoutExpired:
            pass

        # 2) Forceful: SIGKILL
        if proc.poll() is None:
            _kill_group(signal.SIGKILL)
            try:
                proc.wait(timeout=kill_timeout)
            except subprocess.TimeoutExpired:
                pass

        # 3) Final backstop: one more SIGKILL in case the
        #    previous signals were lost (rare, but possible on
        #    a heavily loaded kernel).
        if proc.poll() is None:
            _kill_group(signal.SIGKILL)
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass

        with self._lock:
            if proc.poll() is None:
                # Truly stuck. Mark ERROR and leave _process set
                # so the user can inspect; do NOT clear it.
                self._status.state = ServerState.ERROR
                self._status.last_error = (
                    f"Process {pid} (pgid {pgid}) did not exit after "
                    "SIGKILL. Check `ps -p {pid}` and `nvidia-smi` "
                    "for orphans; you may need to kill manually."
                ).format(pid=pid, pgid=pgid)
                return self._status_copy_unlocked()
            self._status.state = ServerState.STOPPED
            self._status.exit_code = proc.returncode
            self._status.pid = None
            self._process = None
            if self._log_file_handle is not None:
                self._log_file_handle.close()
                self._log_file_handle = None
            self._api_client = None
            return self._status_copy_unlocked()

    def restart(
        self,
        argv: list[str],
        host: str = "127.0.0.1",
        port: int = 8080,
        model_path: Optional[str] = None,
        profile_name: Optional[str] = None,
    ) -> RuntimeStatus:
        """Stop the current server and start a fresh one."""
        self.stop()
        return self.start(argv, host, port, model_path, profile_name)

    def poll_health(self) -> ApiStatus:
        """One-shot health check against the running server's API."""
        with self._lock:
            client = self._api_client
            process = self._process
            if client is None:
                return ApiStatus(reachable=False, error="no running server")
        if process is not None and process.poll() is not None:
            return ApiStatus(reachable=False, error="server process exited")

        status = client.status()
        with self._lock:
            if client is not self._api_client:
                return status
            self._status.api_status = status
            if self._status.state in {ServerState.RUNNING, ServerState.HEALTHY, ServerState.UNHEALTHY}:
                self._status.state = (
                    ServerState.HEALTHY if status.reachable else ServerState.UNHEALTHY
                )
        return status

    def switch_model(self, model_path: str) -> "SwitchResult":
        """Try to switch the running model via the API.

        Returns a :class:`SwitchResult` indicating whether the API path was
        used or a clean restart is required.
        """
        from .runtime_api import SwitchResult

        with self._lock:
            client = self._api_client
        if client is None:
            return SwitchResult(
                switched=False,
                restart_required=True,
                unreachable=False,
                message="no running server; start first",
            )
        return client.switch_model(model_path)

    def note_model_switched(self, model_path: str, profile_name: Optional[str] = None) -> None:
        with self._lock:
            self._status.model_path = model_path
            if profile_name is not None:
                self._status.profile_name = profile_name

    # -- internal -----------------------------------------------------------

    @staticmethod
    def _find_pid_for_port(host: str, port: int) -> Optional[int]:
        """Find the PID of the process listening on *host*:*port*.

        Uses ``ss`` (available on all modern Linux) or falls back to
        ``lsof``.  Returns None if nothing is found.
        """
        port_str = str(port)
        # Try ss first (fast, common on Linux).
        try:
            out = subprocess.run(
                ["ss", "-tlnp", f"sport = :{port_str}"],
                capture_output=True, text=True, timeout=5,
            )
            for line in out.stdout.splitlines():
                # ss output: "... pid=1234 ..."
                m = re.search(r"pid=(\d+)", line)
                if m:
                    return int(m.group(1))
        except Exception:
            pass
        # Fallback: lsof.
        try:
            out = subprocess.run(
                ["lsof", "-i", f":{port_str}", "-t", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=5,
            )
            for line in out.stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    return int(line)
        except Exception:
            pass
        return None

    @staticmethod
    def _kill_external(pid: int, graceful: float = 5.0, force: float = 3.0) -> None:
        """Kill an external process by PID with SIGTERM → SIGKILL fallback."""
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return

        def _sig(sig: int) -> None:
            if sys.platform == "win32":
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
            else:
                try:
                    os.killpg(pgid, sig)
                except (ProcessLookupError, PermissionError):
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass

        _sig(signal.SIGTERM)
        # Poll for exit.
        import time
        deadline = time.monotonic() + graceful
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.3)

        _sig(signal.SIGKILL)
        deadline = time.monotonic() + force
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.3)

    def _sync_state(self) -> None:
        """Reflect process exit into status (caller holds lock)."""
        if (
            self._process
            and self._process.poll() is not None
            and self._status.state
            in {ServerState.RUNNING, ServerState.STARTING, ServerState.HEALTHY, ServerState.UNHEALTHY}
        ):
            self._status.state = ServerState.EXITED
            self._status.exit_code = self._process.returncode
            self._status.pid = None

    def _load_persisted_logs(self) -> None:
        try:
            if not self._log_path.is_file():
                return
            loaded: list[LogLine] = []
            with self._log_path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    try:
                        item = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    source = item.get("source")
                    text = item.get("text")
                    timestamp = item.get("timestamp")
                    if source in {"stdout", "stderr"} and isinstance(text, str) and isinstance(timestamp, str):
                        loaded.append(LogLine(source=source, text=text, timestamp=timestamp))
            self.log_buffer.extend(loaded[-10_000:])
        except OSError:
            return

    def _truncate_persisted_logs(self) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_path.write_text("", encoding="utf-8")
        except OSError:
            return

    def _persist_log(self, line: LogLine) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line.__dict__, ensure_ascii=False) + "\n")
        except OSError:
            return

    def _emit(self, line: LogLine, session: int) -> None:
        # Stale lines from a previous process (reader thread still
        # draining the old pipe) are dropped here. Without this guard
        # they would be appended to the new log buffer that ``start``
        # just cleared.
        if session != self._log_session:
            return
        self.log_buffer.append(line)
        self._persist_log(line)
        if self.on_log:
            self.on_log(line)

    def _start_tail_reader(self, path: Path, session: int, start_at_end: bool = False) -> None:
        def run() -> None:
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    if start_at_end:
                        handle.seek(0, os.SEEK_END)
                    while session == self._log_session:
                        raw = handle.readline()
                        if raw:
                            self._emit(LogLine(source="stdout", text=raw.rstrip()), session)
                            continue
                        with self._lock:
                            proc = self._process
                            running = self._status.state in {ServerState.RUNNING, ServerState.HEALTHY, ServerState.UNHEALTHY}
                        if proc is not None and proc.poll() is not None:
                            return
                        if proc is None and not running:
                            return
                        time.sleep(0.2)
            except OSError:
                return

        t = threading.Thread(target=run, name="llama-server-log-tail", daemon=True)
        t.start()

    def _start_reader(self, source: str, pipe, session: int) -> None:
        if pipe is None:
            return

        def run() -> None:
            for raw in pipe:
                self._emit(LogLine(source=source, text=raw.rstrip()), session)

        t = threading.Thread(target=run, name=f"llama-server-{source}", daemon=True)
        t.start()

    def _start_health_poll(self, host: str, port: int) -> None:
        # Use a fresh event per health-poll session instead of clearing a
        # shared one. ``set()`` followed by ``clear()`` on the same event
        # is non-atomic: if ``stop()`` fires between them, the old
        # thread might see the cleared event and continue running
        # against a stale client. A new event has no such race.
        old_stop = self._stop_health
        new_stop = threading.Event()
        self._stop_health = new_stop
        if old_stop is not None:
            old_stop.set()
        self._api_client = LlamaServerApiClient(
            host=host, port=port, timeout=_HEALTH_TIMEOUT,
            router_mode=self.router_mode,
        )

        if self.router_mode:
            # Router mode endpoints are active: /health and /models can trigger
            # lazy model loads and LRU eviction. Keep the API client for manual
            # actions, but do not start an automatic HTTP polling thread.
            self._health_thread = None
            return

        def loop() -> None:
            while not new_stop.wait(_HEALTH_INTERVAL):
                if self._process and self._process.poll() is not None:
                    return
                self.poll_health()

        t = threading.Thread(target=loop, name="llama-server-health", daemon=True)
        t.start()
        self._health_thread = t


__all__ = [
    "ServerState",
    "LogLine",
    "LogBuffer",
    "RuntimeStatus",
    "LogCallback",
    "LlamaServerController",
    "build_argv",
    "generate_models_preset",
]
