"""Smoke tests for runtime_api — llama-server HTTP API client.

Uses ``http.server`` to stand up a tiny fake llama-server so that
connection-refused, happy-path, and endpoint-detection logic all get
exercised without a real llama-server binary.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
QT_ROOT = REPO_ROOT / "qt_app"
for candidate in (REPO_ROOT, QT_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.services.runtime_api import (  # noqa: E402
    ApiStatus,
    HealthStatus,
    LlamaServerApiClient,
    RuntimeMetrics,
    SwitchResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  pass {message}")


class _FakeHandler(BaseHTTPRequestHandler):
    """Minimal fake llama-server supporting /health, /props, /model/load."""

    # Class-level config the test can mutate before starting the server.
    supports_model_load: bool = True
    loaded_model: str | None = None

    def log_message(self, format, *args):
        pass  # silence request logs

    def _send_json(self, code: int, obj: dict[str, Any]) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            health_obj: dict[str, Any] = {"status": "ok", "slots_idle": 2, "slots_processing": 1}
            if _FakeHandler.loaded_model:
                health_obj["model_path"] = _FakeHandler.loaded_model
            self._send_json(200, health_obj)
        elif self.path == "/props":
            props_obj: dict[str, Any] = {
                "total_slots": 4,
                "n_ctx": 4096,
            }
            if _FakeHandler.loaded_model:
                props_obj["model_path"] = _FakeHandler.loaded_model
            self._send_json(200, props_obj)
        elif self.path == "/metrics":
            self._send_text(
                200,
                "\n".join(
                    (
                        "# HELP llamacpp:tokens_predicted_total Predicted tokens",
                        "llamacpp:tokens_predicted_total 42",
                        'llamacpp:tokens_evaluated_total{model="test"} 12',
                        'llamacpp:slot_state{slot_id="0"} 1',
                        'llamacpp:slot_state{slot_id="1"} 0',
                    )
                ),
            )
        elif self.path == "/slots":
            self._send_json(200, [{"id": 0, "is_processing": True}, {"id": 1, "is_processing": False}])
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}

        if self.path == "/model/load":
            if not _FakeHandler.supports_model_load:
                self._send_json(404, {"error": "not found"})
                return
            model = data.get("model", "")
            if not model:
                # Empty probe — respond 200 to indicate endpoint exists.
                self._send_json(200, {"status": "ok"})
                return
            _FakeHandler.loaded_model = model
            self._send_json(200, {"model": model, "status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})


def _start_server(port: int = 0) -> tuple[HTTPServer, int]:
    server = HTTPServer(("127.0.0.1", port), _FakeHandler)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, actual_port


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_connection_refused() -> None:
    """Client handles unreachable server gracefully."""
    print("test_connection_refused:")
    # Use a port that's almost certainly not listening.
    client = LlamaServerApiClient(host="127.0.0.1", port=19999, timeout=0.5)

    health = client.check_health()
    check(not health.reachable, "unreachable server → reachable=False")
    check(health.error is not None, "error message present")
    check("connection refused" in health.error.lower() or "refused" in health.error.lower(),
          "error mentions connection refused")

    result = client.switch_model("/fake/model.gguf")
    check(result.unreachable, "switch_model → unreachable=True")
    check(not result.switched, "not switched")
    check(not result.restart_required, "not restart_required (server down)")
    print()


def test_health_and_props() -> None:
    """Happy-path health and props queries against fake server."""
    print("test_health_and_props:")
    _FakeHandler.loaded_model = "/data/test-model.gguf"
    server, port = _start_server()
    try:
        client = LlamaServerApiClient(port=port, timeout=2.0)

        health = client.check_health()
        check(health.reachable, "server reachable")
        check(health.health == "ok", "health is ok")
        check(health.model_path == "/data/test-model.gguf", "model_path present")

        props = client.fetch_props()
        check(props.reachable, "props reachable")
        check(props.total_slots == 4, "total_slots parsed")
        check(props.model_path == "/data/test-model.gguf", "model_path from props")
    finally:
        server.shutdown()
    print()


def test_switch_model_api_supported() -> None:
    """Model switch succeeds when /model/load endpoint exists."""
    print("test_switch_model_api_supported:")
    _FakeHandler.supports_model_load = True
    _FakeHandler.loaded_model = "/old.gguf"
    server, port = _start_server()
    try:
        client = LlamaServerApiClient(port=port, timeout=2.0)

        # Verify detection.
        supported = client.detect_model_load_support()
        check(supported, "model-load API detected")

        # Switch model.
        result = client.switch_model("/new/model.gguf")
        check(result.switched, "switched=True")
        check(not result.restart_required, "restart_required=False")
        check(not result.unreachable, "unreachable=False")
        check(result.new_model == "/new/model.gguf", "new_model set")
    finally:
        server.shutdown()
    print()


def test_switch_model_api_unsupported() -> None:
    """Model switch returns restart_required when API is absent."""
    print("test_switch_model_api_unsupported:")
    _FakeHandler.supports_model_load = False
    _FakeHandler.loaded_model = "/old.gguf"
    server, port = _start_server()
    try:
        client = LlamaServerApiClient(port=port, timeout=2.0)

        result = client.switch_model("/other.gguf")
        check(not result.switched, "not switched")
        check(result.restart_required, "restart_required=True")
        check(not result.unreachable, "unreachable=False")
        check("restart" in result.message.lower(), "message mentions restart")
    finally:
        server.shutdown()
    print()


def test_status_combined_probe() -> None:
    """Combined status probe returns reachable + model_load_supported."""
    print("test_status_combined_probe:")
    _FakeHandler.supports_model_load = True
    _FakeHandler.loaded_model = None
    server, port = _start_server()
    try:
        client = LlamaServerApiClient(port=port, timeout=2.0)
        s = client.status()
        check(s.reachable, "status reachable")
        check(s.model_load_supported, "model_load_supported=True")
    finally:
        server.shutdown()
    print()


def test_metrics() -> None:
    """Prometheus /metrics text and /slots rows are parsed."""
    print("test_metrics:")
    server, port = _start_server()
    try:
        client = LlamaServerApiClient(port=port, timeout=2.0)
        metrics = client.fetch_metrics()
        check(metrics.reachable, "metrics reachable")
        check(metrics.values["llamacpp_tokens_predicted_total"] == 42.0, "colon metric name normalized")
        check(metrics.values["llamacpp_tokens_evaluated_total"] == 12.0, "labels ignored")
        check(metrics.values["llamacpp_slot_state"] == 1.0, "duplicate labelled metrics summed")
        slots = client.fetch_slots()
        check(len(slots) == 2, "slots parsed")
        check(slots[0]["is_processing"] is True, "slot active state parsed")
        health = client.check_health()
        check(health.slots_idle == 2, "health slots_idle parsed")
        check(health.slots_processing == 1, "health slots_processing parsed")
    finally:
        server.shutdown()
    print()


def test_dataclass_serialization() -> None:
    """ApiStatus, RuntimeMetrics and SwitchResult have working to_dict."""
    print("test_dataclass_serialization:")
    s = ApiStatus(reachable=True, health="ok")
    d = s.to_dict()
    check(isinstance(d, dict), "ApiStatus.to_dict → dict")
    check(d["reachable"] is True, "reachable in dict")

    m = RuntimeMetrics(reachable=True, values={"tokens": 3.0})
    md = m.to_dict()
    check(isinstance(md, dict), "RuntimeMetrics.to_dict → dict")
    check(md["values"]["tokens"] == 3.0, "metrics values in dict")

    r = SwitchResult(switched=True, restart_required=False, unreachable=False, message="ok")
    rd = r.to_dict()
    check(isinstance(rd, dict), "SwitchResult.to_dict → dict")
    check(rd["switched"] is True, "switched in dict")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    test_connection_refused()
    test_health_and_props()
    test_switch_model_api_supported()
    test_switch_model_api_unsupported()
    test_status_combined_probe()
    test_metrics()
    test_dataclass_serialization()
    print("All runtime_api smoke tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
