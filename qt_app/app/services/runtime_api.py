"""HTTP API client for local llama-server health, capability, and model switching.

Uses only stdlib ``urllib`` — zero external dependencies.
All network errors (connection refused, timeout, DNS) are caught and returned
as structured results.  Callers never see bare exceptions from this module.
"""
from __future__ import annotations

import json
import os
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class HealthStatus(str, Enum):
    OK = "ok"
    LOADING = "loading"
    ERROR = "error"


@dataclass
class ApiStatus:
    """Full health + capability probe result."""
    reachable: bool
    health: Optional[str] = None          # raw status string: ok / loading / error
    model_path: Optional[str] = None
    total_slots: Optional[int] = None
    model_load_supported: bool = False
    slots_idle: Optional[int] = None
    slots_processing: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeMetrics:
    """Parsed llama-server runtime metrics from Prometheus-style /metrics text."""
    reachable: bool
    values: dict[str, float] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SwitchResult:
    """Result of a model-switch attempt."""
    switched: bool          # True when API model-load succeeded
    restart_required: bool  # True when API unavailable; caller must restart
    unreachable: bool       # True when server is not running
    message: str = ""
    new_model: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 3.0


def _is_connection_refused(error: str) -> bool:
    low = error.lower()
    return "connection refused" in low or "errno 111" in low or "econnrefused" in low

def _trace_local_router_call(method: str, url: str) -> None:
    """Append a short stack trace for local router HTTP calls.

    This is a targeted diagnostic for unexpected requests hitting the local
    llama-server router.  It only logs loopback/0.0.0.0 traffic to port 8080
    and writes to the user's llamaUI data dir.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or 80
        if host not in {"127.0.0.1", "0.0.0.0", "localhost"} or port != 8080:
            return
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        log_path = data_home / "llamaUI" / "router-http-debug.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stack = "".join(traceback.format_stack(limit=12)[:-1])
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {method} {url} ===\n")
            fh.write(stack)
    except Exception:
        pass



def _get_json(url: str, timeout: float = _DEFAULT_TIMEOUT) -> tuple[Optional[dict], Optional[str]]:
    """GET *url*, return ``(parsed_json, None)`` or ``(None, error_string)``."""
    try:
        _trace_local_router_call("GET", url)
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw), None
    except (urllib.error.URLError, OSError) as exc:
        return None, str(exc)
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON: {exc}"


def _get_text(url: str, timeout: float = _DEFAULT_TIMEOUT) -> tuple[Optional[str], Optional[str]]:
    """GET *url*, return ``(text, None)`` or ``(None, error_string)``."""
    try:
        _trace_local_router_call("GET", url)
        req = urllib.request.Request(url, method="GET", headers={"Accept": "text/plain"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace"), None
    except (urllib.error.URLError, OSError) as exc:
        return None, str(exc)


def _parse_prometheus_metrics(text: str) -> dict[str, float]:
    """Parse numeric samples from Prometheus text exposition."""
    values: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name_part, sep, value_part = line.partition(" ")
        if not sep:
            continue
        metric_name = name_part.split("{", 1)[0].replace(":", "_")
        try:
            value = float(value_part.split(None, 1)[0])
        except (IndexError, ValueError):
            continue
        values[metric_name] = values.get(metric_name, 0.0) + value
    return values


def _post_json(
    url: str,
    body: dict[str, Any],
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[Optional[dict], Optional[int], Optional[str]]:
    """POST JSON to *url*. Returns ``(parsed, status_code, error)``."""
    try:
        _trace_local_router_call("POST", url)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw), resp.status, None
            except json.JSONDecodeError:
                return None, resp.status, None
    except urllib.error.HTTPError as exc:
        err_text = ""
        try:
            err_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return None, exc.code, err_text or str(exc)
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ConnectionRefusedError):
            return None, None, f"Connection refused: {exc}"
        return None, None, str(exc)


# ---------------------------------------------------------------------------
# Public client
# ---------------------------------------------------------------------------


class LlamaServerApiClient:
    """Stateless client for a local llama-server's HTTP API."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        timeout: float = _DEFAULT_TIMEOUT,
        router_mode: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.router_mode = router_mode
        # Cache the /model/load capability probe so the health thread
        # does not POST a dummy request every second. Invalidated by
        # constructing a new client (the controller does this on every
        # ``start``).
        self._model_load_supported: Optional[bool] = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # -- health / status -----------------------------------------------------

    def check_health(self) -> ApiStatus:
        """GET /health — returns structured status, never raises."""
        body, err = _get_json(f"{self.base_url}/health", timeout=self.timeout)
        if err is not None:
            return ApiStatus(reachable=False, error=err)

        status_str = body.get("status", "error") if isinstance(body, dict) else "error"
        return ApiStatus(
            reachable=True,
            health=status_str,
            model_path=body.get("model_path") if isinstance(body, dict) else None,
            total_slots=body.get("total_slots") if isinstance(body, dict) else None,
            slots_idle=body.get("slots_idle") if isinstance(body, dict) else None,
            slots_processing=body.get("slots_processing") if isinstance(body, dict) else None,
        )

    def fetch_props(self, model: str | None = None) -> ApiStatus:
        """GET /props — enriches ApiStatus with server properties.

        In router mode, pass *model* to get per-model props via
        ``/props?model=<id>``.
        """
        url = f"{self.base_url}/props"
        if model:
            url += f"?model={urllib.parse.quote(model, safe='')}"
        body, err = _get_json(url, timeout=self.timeout)
        if err is not None:
            return ApiStatus(reachable=False, error=err)

        return ApiStatus(
            reachable=True,
            health="ok",
            model_path=body.get("model_path") if isinstance(body, dict) else None,
            total_slots=body.get("total_slots") if isinstance(body, dict) else None,
        )

    def fetch_metrics(self, model: str | None = None) -> RuntimeMetrics:
        """GET /metrics — return parsed Prometheus counters/gauges.

        In router mode, pass *model* to query via ``/metrics?model=<id>``.
        """
        url = f"{self.base_url}/metrics"
        if model:
            url += f"?model={urllib.parse.quote(model, safe='')}"
        text, err = _get_text(url, timeout=self.timeout)
        if err is not None:
            return RuntimeMetrics(reachable=False, error=err)
        return RuntimeMetrics(reachable=True, values=_parse_prometheus_metrics(text or ""))

    # -- model load detection ------------------------------------------------

    def detect_model_load_support(self) -> bool:
        """Probe whether POST /model/load exists (newer llama-server builds).

        The result is cached on the client so the health thread does not
        POST a dummy request every second. The cache is invalidated
        when a new client is constructed (the controller does this on
        every ``start``).
        """
        if self._model_load_supported is not None:
            return self._model_load_supported
        _, code, err = _post_json(
            f"{self.base_url}/model/load",
            {"model": ""},
            timeout=min(self.timeout, 2.0),
        )
        if code is not None:
            self._model_load_supported = code != 404
        else:
            # Connection refused / network error → unknown, conservatively False.
            self._model_load_supported = False
        return self._model_load_supported

    # -- status (combined probe) ---------------------------------------------

    def status(self) -> ApiStatus:
        """Combined health + model-load capability probe."""
        health = self.check_health()
        if not health.reachable:
            return health

        # In router mode, the POST /model/load probe triggers spurious
        # model loads and LRU eviction loops.  The router manages models
        # natively — skip the probe entirely.
        if not self.router_mode:
            health.model_load_supported = self.detect_model_load_support()
        return health

    # -- model switching -----------------------------------------------------

    def switch_model(self, model_path: str) -> SwitchResult:
        """Attempt to hot-switch the model on a running llama-server.

        Strategy:
        1. Check server is reachable.
        2. Probe ``/model/load`` for API support.
        3. POST model path if supported.
        4. Return ``restart_required`` if API not available.
        5. Return ``unreachable`` if server is down.

        Never raises — all errors are structured in the result.
        """
        # Check reachability first.
        health = self.check_health()
        if not health.reachable:
            return SwitchResult(
                switched=False,
                restart_required=False,
                unreachable=True,
                message=f"Server unreachable at {self.host}:{self.port}: {health.error}",
            )

        # Detect API support.
        if not self.detect_model_load_support():
            return SwitchResult(
                switched=False,
                restart_required=True,
                unreachable=False,
                message="Server does not support /model/load API. "
                        "Stop and restart llama-server with the new model.",
            )

        # Attempt the switch.
        body, code, err = _post_json(
            f"{self.base_url}/model/load",
            {"model": model_path},
            timeout=max(self.timeout, 10.0),
        )

        if code is not None and 200 <= code < 300:
            loaded = body.get("model") if isinstance(body, dict) else None
            return SwitchResult(
                switched=True,
                restart_required=False,
                unreachable=False,
                message="Model switched successfully.",
                new_model=loaded or model_path,
            )

        error_detail = err or f"HTTP {code}"
        if isinstance(body, dict):
            inner = body.get("error")
            if isinstance(inner, dict):
                error_detail = inner.get("message", error_detail)
            elif isinstance(inner, str):
                error_detail = inner
        return SwitchResult(
            switched=False,
            restart_required=False,
            unreachable=False,
            message=f"Model switch failed: {error_detail}",
        )

    def fetch_slots(self, model: str | None = None) -> list[dict]:
        """GET /slots — return slot state rows when llama-server exposes them.

        In router mode, pass *model* to query via ``/slots?model=<id>``.
        """
        url = f"{self.base_url}/slots"
        if model:
            url += f"?model={urllib.parse.quote(model, safe='')}"
        body, err = _get_json(url, timeout=self.timeout)
        if err is not None:
            return []
        if isinstance(body, list):
            return [slot for slot in body if isinstance(slot, dict)]
        if isinstance(body, dict):
            slots = body.get("slots")
            if isinstance(slots, list):
                return [slot for slot in slots if isinstance(slot, dict)]
        return []

    # -- router mode model management ----------------------------------------

    def list_loaded_models(self) -> list[dict]:
        """GET /models — return list of model dicts from router mode.

        Each dict has at least 'id' (str) and may have 'state' ('loaded'|'loading'|'unloaded').
        Returns empty list on any error.
        """
        body, err = _get_json(f"{self.base_url}/models", timeout=self.timeout)
        if err is not None or not isinstance(body, dict):
            return []
        data = body.get("data")
        if not isinstance(data, list):
            return []
        return [m for m in data if isinstance(m, dict)]

    def unload_model(self, model_name: str) -> tuple[bool, str]:
        """POST /models/unload — unload a model to free VRAM.

        Returns (success, message).
        """
        body, code, err = _post_json(
            f"{self.base_url}/models/unload",
            {"model": model_name},
            timeout=max(self.timeout, 10.0),
        )
        if code is not None and 200 <= code < 300:
            return True, f"Unloaded {model_name}"
        msg = err or f"HTTP {code}"
        if isinstance(body, dict):
            inner = body.get("error")
            if isinstance(inner, dict):
                msg = inner.get("message", msg)
            elif isinstance(inner, str):
                msg = inner
        return False, msg

    def load_model(self, model_name: str) -> tuple[bool, str]:
        """POST /models/load — load a model on demand.

        Returns (success, message).
        """
        body, code, err = _post_json(
            f"{self.base_url}/models/load",
            {"model": model_name},
            timeout=max(self.timeout, 30.0),
        )
        if code is not None and 200 <= code < 300:
            return True, f"Loaded {model_name}"
        msg = err or f"HTTP {code}"
        if isinstance(body, dict):
            inner = body.get("error")
            if isinstance(inner, dict):
                msg = inner.get("message", msg)
            elif isinstance(inner, str):
                msg = inner
        return False, msg


__all__ = ["ApiStatus", "HealthStatus", "LlamaServerApiClient", "RuntimeMetrics", "SwitchResult"]
