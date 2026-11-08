import asyncio
import json
import os
import subprocess
from pathlib import Path
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import httpx

import monitor

app = FastAPI(title="Workstation Control Deck")
STACK_DIR = Path(__file__).parent.resolve()
WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE", Path.home() / "agent-workspace")).resolve()
MODELS_DIR = STACK_DIR / "models"

@app.get("/", response_class=HTMLResponse)
@app.get("/llm", response_class=HTMLResponse)
async def get_llm_page():
    return HTMLResponse((STACK_DIR / "llm.html").read_text(encoding="utf-8"))

@app.get("/hermes", response_class=HTMLResponse)
async def get_hermes_page():
    return HTMLResponse((STACK_DIR / "hermes.html").read_text(encoding="utf-8"))

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

@app.post("/api/llama/start")
async def start_llama():
    subprocess.Popen(["sudo", "systemctl", "start", "llama-server"])
    return {"ok": True}

@app.post("/api/llama/stop")
async def stop_llama():
    subprocess.Popen(["sudo", "systemctl", "stop", "llama-server"])
    return {"ok": True}

@app.post("/api/llama/restart")
async def restart_llama():
    subprocess.Popen(["sudo", "systemctl", "restart", "llama-server"])
    return {"ok": True}

@app.get("/api/llama/logs")
async def llama_logs():
    res = subprocess.run(["sudo", "journalctl", "-u", "llama-server", "-n", "45", "--no-pager"], capture_output=True, text=True)
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
