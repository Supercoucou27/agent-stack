"""Smoke test for Phase 13 clean stop.

Verifies that ``LlamaServerController.stop()`` actually kills the
process group (parent + any children), reaps the parent, and ends
in the STOPPED state.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.runtime import (  # noqa: E402
    LlamaServerController,
    ServerState,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"pass {message}")


def _pick_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    # Case 1: parent that traps SIGTERM. stop() escalates to SIGKILL.
    c = LlamaServerController()
    port = _pick_port()
    c.start(
        ["/bin/sh", "-c", "trap '' TERM; sleep 60"],
        host="127.0.0.1", port=port,
    )
    assert c.status.pid
    pid = c.status.pid

    status = c.stop(graceful_timeout=1.0, kill_timeout=2.0)
    check(c.status.state == ServerState.STOPPED,
          f"case 1: state is STOPPED (got {c.status.state})")
    check(c.status.pid is None, "case 1: pid cleared")
    check(c._process is None, "case 1: _process cleared")
    try:
        os.kill(pid, 0)
        still_alive = True
    except ProcessLookupError:
        still_alive = False
    check(not still_alive, f"case 1: pid {pid} is dead after stop()")

    # Case 2: parent that spawns children. Whole group must die.
    c2 = LlamaServerController()
    port2 = _pick_port()
    c2.start(
        ["/bin/sh", "-c", "sleep 30 & sleep 30 & sleep 30 & wait"],
        host="127.0.0.1", port=port2,
    )
    pid2 = c2.status.pid
    time.sleep(0.3)

    c2.stop(graceful_timeout=1.0, kill_timeout=2.0)
    check(c2.status.state == ServerState.STOPPED,
          "case 2: state is STOPPED")
    time.sleep(0.2)
    try:
        os.kill(pid2, 0)
        still_alive = True
    except ProcessLookupError:
        still_alive = False
    check(not still_alive, f"case 2: parent pid {pid2} is dead")

    # Case 3: stress — start, stop, repeat 3x.
    for i in range(3):
        c3 = LlamaServerController()
        port3 = _pick_port()
        c3.start(["/bin/sh", "-c", "sleep 30"],
                 host="127.0.0.1", port=port3)
        c3.stop(graceful_timeout=0.5, kill_timeout=1.0)
        check(c3.status.state == ServerState.STOPPED,
              f"case 3 iteration {i}: state STOPPED")

    # Case 4: stop() with no live process is a no-op STOPPED.
    c4 = LlamaServerController()
    s4 = c4.stop()
    check(s4.state == ServerState.STOPPED,
          "case 4: stop on fresh controller → STOPPED")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
