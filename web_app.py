import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import httpx

import monitor

app = FastAPI(title="Unified LLM & Hermes Deck")
STACK_DIR = Path(__file__).parent.resolve()
WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE", Path.home() / "agent-workspace")).resolve()
MODELS_DIR = STACK_DIR / "models"

@app.get("/", response_class=HTMLResponse)
@app.get("/llm", response_class=HTMLResponse)
@app.get("/hermes", response_class=HTMLResponse)
async def get_index():
    return HTMLResponse((STACK_DIR / "index.html").read_text(encoding="utf-8"))

@app.get("/api/health")
async def health(llama_url: str = "http://127.0.0.1:8080"):
    llama_ok = False
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"{llama_url}/v1/models")
            llama_ok = (r.status_code == 200)
    except Exception:
        pass
    return {"status": "ok", "llama": llama_ok}

@app.get("/api/stats")
async def stats():
    return JSONResponse(monitor.get_stats())


@app.post("/api/llama/chat")
async def llm_chat(req: Request):
    """Contrôle direct du LLM local : envoi un prompt au serveur llama-server."""
    data = await req.json()
    messages = data.get("messages")
    prompt = data.get("prompt")
    if messages is None:
        messages = [{"role": "user", "content": (prompt or "").strip()}]
    model = data.get("model") or "/home/tara/agent-stack/models/Qwen3.8-27B-IQ3_XS-3.01bpw.gguf"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": data.get("temperature", 0.7),
    }
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post("http://127.0.0.1:8080/v1/chat/completions",
                                 json=payload, headers={"Content-Type": "application/json"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    if r.status_code != 200:
        return JSONResponse({"error": r.text, "status": r.status_code}, status_code=502)
    return r.json()


GATEWAY_SOCK = Path(os.environ.get("HERMES_GATEWAY_SOCK", "/home/tara/.hermes/gateway.sock"))


def _hermes_binary():
    candidates = [
        shutil.which("hermes"),
        Path.home() / ".hermes" / "bin" / "hermes",
        Path("/home/tara/agent-stack/venv/bin/hermes"),
        Path("/home/tara/.hermes/hermes-agent/venv/bin/hermes"),
    ]
    for c in candidates:
        p = Path(c)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return str(Path(candidates[0]))


def _hermes_status():
    """Demande le statut de l'agent Hermes via le socket gateway."""
    try:
        import json as _json
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(str(GATEWAY_SOCK))
        s.sendall(json.dumps({"verb": "status", "id": 1}).encode() + b"\n")
        resp = s.recv(65536)
        s.close()
        d = json.loads(resp.decode("utf-8", "replace"))
        if d.get("ok") and "result" in d:
            return d["result"]
    except Exception:
        pass
    # Fallback: lire le fichier de statut du gateway
    gs = Path("/home/tara/.hermes/gateway_state.json")
    try:
        if gs.exists():
            return json.loads(gs.read_text()).get("gateway_state", "unknown")
    except Exception:
        pass
    return "offline"


@app.get("/api/hermes/status")
async def hermes_status():
    return JSONResponse(_hermes_status())


@app.post("/api/hermes/send")
async def hermes_send(req: Request):
    """Envoie un message direct à l'agent Hermes (via l'CLI, mode yolo)."""
    data = await req.json()
    msg = data.get("message", "").strip()
    if not msg:
        return JSONResponse({"error": "Message vide"}, status_code=400)
    hermes_bin = _hermes_binary()
    env = os.environ.copy()
    env["PATH"] = f"{Path.home()}/.local/bin:{Path('/home/tara/agent-stack/venv/bin')}:{env.get('PATH', '')}"

    async def event_generator():
        proc = await asyncio.create_subprocess_exec(
            hermes_bin, "chat", "-q", msg, "--yolo",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(Path.home() / "agent-workspace"),
            env=env,
        )
        while True:
            line = await proc.stdout.readline()
            if line is None or not line:
                break
            text = line.decode("utf-8", errors="replace")
            yield f"data: {json.dumps({'type': 'stdout', 'text': text, 'from': 'hermes'})}\n\n"

        rc = await proc.wait()
        yield f"data: {json.dumps({'type': 'exit', 'code': rc, 'from': 'hermes'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/models")
async def list_models():
    models = []
    active_model = ""
    start_sh = STACK_DIR / "start_llama_server.sh"
    if start_sh.exists():
        for line in start_sh.read_text().splitlines():
            if line.startswith("MODEL_FILE="):
                active_model = Path(line.split("=", 1)[1].strip('"')).name
                break

    if MODELS_DIR.exists():
        for f in sorted(MODELS_DIR.glob("*.gguf")):
            models.append({
                "name": f.name,
                "size_gb": round(f.stat().st_size / (1024**3), 2),
                "active": (f.name == active_model)
            })
    return {"models": models, "active": active_model}

@app.post("/api/switch_model")
async def switch_model(req: Request):
    data = await req.json()
    model_name = data.get("model_name")
    if not model_name:
        return JSONResponse({"error": "Missing model_name"}, status_code=400)

    target_model = MODELS_DIR / model_name
    if not target_model.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)

    start_sh = STACK_DIR / "start_llama_server.sh"
    script_content = f"""#!/bin/bash
set -euo pipefail

MODEL_FILE="$HOME/agent-stack/models/{model_name}"
LLAMA_BIN="$HOME/llama.cpp/build/bin/llama-server"

exec "$LLAMA_BIN" \\
  -m "$MODEL_FILE" \\
  --host 0.0.0.0 \\
  --port 8080 \\
  -c 32768 \\
  -t 6 \\
  -ngl 99 \\
  -ts 1,1 \\
  -sm layer \\
  -b 2048 \\
  -ub 512 \\
  -ctk q4_0 \\
  -ctv q4_0 \\
  --flash-attn on \\
  -np 1 \\
  --jinja \\
  --metrics
"""
    start_sh.write_text(script_content)
    subprocess.Popen(["sudo", "systemctl", "restart", "llama-server"])
    return {"ok": True, "active": model_name}

def _wait_llama_state(target_state: str, timeout: int = 60) -> tuple[bool, str, Optional[str]]:
    """Attends que llama-server atteigne l'état souhaité (active/inactive)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            res = subprocess.run(
                ["sudo", "systemctl", "is-active", "llama-server"],
                capture_output=True, text=True, timeout=10
            )
            state = res.stdout.strip()
            if state == target_state:
                return True, state, None
        except Exception:
            pass
        time.sleep(0.5)
    res = subprocess.run(["sudo", "systemctl", "is-active", "llama-server"], capture_output=True, text=True, timeout=10)
    return False, res.stdout.strip(), res.stderr.strip()


@app.post("/api/llama/start")
async def start_llama():
    subprocess.run(["sudo", "systemctl", "daemon-reload"], capture_output=True, timeout=30)
    subprocess.run(["sudo", "systemctl", "start", "llama-server"], capture_output=True, timeout=60)
    ok, state, err = _wait_llama_state("active", timeout=90)
    if not ok:
        return JSONResponse({"ok": False, "state": state, "error": err or "llama-server n'est pas passé 'active'"}, status_code=500)
    return {"ok": True, "state": state}


@app.post("/api/llama/stop")
async def stop_llama():
    subprocess.run(["sudo", "systemctl", "stop", "llama-server"], capture_output=True, timeout=60)
    ok, state, err = _wait_llama_state("inactive", timeout=60)
    if not ok:
        return JSONResponse({"ok": False, "state": state, "error": err or "llama-server n'est pas passé 'inactive'"}, status_code=500)
    return {"ok": True, "state": state}


@app.post("/api/llama/restart")
async def restart_llama():
    subprocess.run(["sudo", "systemctl", "restart", "llama-server"], capture_output=True, timeout=60)
    ok, state, err = _wait_llama_state("active", timeout=90)
    if not ok:
        return JSONResponse({"ok": False, "state": state, "error": err or "llama-server n'est pas passé 'active'"}, status_code=500)
    return {"ok": True, "state": state}

@app.get("/api/llama/logs")
async def llama_logs():
    res = subprocess.run(["sudo", "journalctl", "-u", "llama-server", "-n", "35", "--no-pager"], capture_output=True, text=True)
    return {"logs": res.stdout}

@app.get("/api/files")
async def list_files():
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(WORKSPACE.rglob("*")):
        if p.is_file() and not any(part.startswith(".") for part in p.parts):
            items.append({
                "rel": str(p.relative_to(WORKSPACE)),
                "size_kb": round(p.stat().st_size / 1024, 1)
            })
    return {"files": items[:250]}

@app.post("/api/run")
async def run_task(req: Request):
    data = await req.json()
    task = data.get("task", "").strip()
    if not task:
        return JSONResponse({"error": "Empty task"}, status_code=400)

    env = os.environ.copy()
    env["AGENT_TASK"] = task
    env["CREW_TEMPERATURE"] = str(data.get("temperature", 0.2))
    env["HARNESS_MAX_ITER"] = str(data.get("max_iter", 35))
    env["AGENT_WORKSPACE"] = str(WORKSPACE)
    env["LLAMA_URL"] = data.get("llama_url", "http://127.0.0.1:8080")

    runner_script = STACK_DIR / "my_agent" / "src" / "runner.py"

    async def event_generator():
        proc = await asyncio.create_subprocess_exec(
            str(STACK_DIR / "venv" / "bin" / "python"),
            str(runner_script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(STACK_DIR / "my_agent" / "src"),
            env=env
        )

        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            yield f"data: {json.dumps({'type': 'stdout', 'text': text})}\n\n"

        rc = await proc.wait()
        yield f"data: {json.dumps({'type': 'exit', 'code': rc})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8082, log_level="info")
