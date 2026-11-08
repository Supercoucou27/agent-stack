import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse
from monitor import get_stats

AGENT_DIR = Path.home() / "agent-stack" / "my_agent" / "src"
VENV_PY = Path.home() / "agent-stack" / "venv" / "bin" / "python"
MODELS_DIR = Path.home() / "agent-stack" / "models"
START_SCRIPT = Path.home() / "agent-stack" / "start_llama_server.sh"
INDEX_PATH = Path.home() / "agent-stack" / "index.html"

MODELS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Agent Pro Workstation")
current_process = None

@app.get("/", response_class=HTMLResponse)
async def index():
    if not INDEX_PATH.exists():
        raise HTTPException(500, "index.html missing")
    return HTMLResponse(INDEX_PATH.read_text(encoding="utf-8"))

@app.get("/api/health")
async def health(llama_url: str = "http://localhost:8080", ws: str = "/home/tara/agent-workspace"):
    ws_path = Path(ws)
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            res = await client.get(f"{llama_url.rstrip('/')}/v1/models")
            ok = res.status_code == 200
    except Exception:
        ok = False
    return {"llama": ok, "workspace": ws_path.exists()}

@app.get("/api/stats")
async def stats():
    return get_stats()

@app.get("/api/models/list")
async def list_models():
    models = []
    active = ""
    if START_SCRIPT.exists():
        for line in START_SCRIPT.read_text().splitlines():
            if line.startswith("MODEL_FILE="):
                active = Path(line.split("=", 1)[1].strip('"\'')).name
                break
    for p in MODELS_DIR.glob("*.gguf"):
        models.append({
            "filename": p.name,
            "size_gb": round(p.stat().st_size / (1024**3), 2),
            "active": (p.name == active)
        })
    models.sort(key=lambda x: x["filename"].lower())
    return {"models": models, "active_model": active}

@app.post("/api/models/switch")
async def switch_model(request: Request):
    data = await request.json()
    filename = data.get("filename", "").strip()
    target = MODELS_DIR / filename
    if not target.is_file():
        raise HTTPException(404, "Model not found.")
    
    if START_SCRIPT.exists():
        content = START_SCRIPT.read_text()
        lines = [f'MODEL_FILE="$HOME/agent-stack/models/{filename}"' if l.startswith("MODEL_FILE=") else l for l in content.splitlines()]
        START_SCRIPT.write_text("\n".join(lines) + "\n")
    
    subprocess.Popen(["sudo", "systemctl", "restart", "llama-server"])
    return {"ok": True}

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

@app.get("/api/files")
async def files(ws: str = "/home/tara/agent-workspace"):
    ws_path = Path(ws)
    if not ws_path.exists():
        return {"files": []}
    res = []
    for root, _, fnames in os.walk(ws_path):
        for f in fnames:
            p = Path(root) / f
            try:
                res.append({"name": str(p.relative_to(ws_path)), "size": p.stat().st_size})
            except OSError:
                pass
    res.sort(key=lambda x: x["name"])
    return {"files": res}

@app.get("/api/file")
async def file(name: str, ws: str = "/home/tara/agent-workspace"):
    p = (Path(ws) / name).resolve()
    if not p.is_file():
        raise HTTPException(404, "File not found.")
    try:
        return {"name": name, "content": p.read_text(encoding="utf-8")}
    except UnicodeDecodeError:
        return {"name": name, "content": "(Binary file)"}

@app.post("/api/file/save")
async def save_file(request: Request):
    data = await request.json()
    p = (Path(data.get("ws", "/home/tara/agent-workspace")) / data.get("name")).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data.get("content", ""), encoding="utf-8")
    return {"ok": True}

@app.post("/api/file/delete")
async def delete_file(request: Request):
    data = await request.json()
    name = data.get("name", "").strip()
    p = (Path(data.get("ws", "/home/tara/agent-workspace")) / name).resolve()
    if p.is_file():
        p.unlink()
    return {"ok": True}

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), ws: str = "/home/tara/agent-workspace"):
    p = Path(ws) / file.filename
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as buf:
        shutil.copyfileobj(file.file, buf)
    return {"ok": True}

@app.post("/api/terminal")
async def run_terminal(request: Request):
    data = await request.json()
    cmd = data.get("command", "").strip()
    ws = data.get("ws", "/home/tara/agent-workspace")
    proc = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, cwd=ws if os.path.isdir(ws) else None, timeout=60)
    return {"output": (proc.stdout or "") + (proc.stderr or ""), "code": proc.returncode}

@app.post("/api/run")
async def run(request: Request):
    global current_process
    data = await request.json()
    env = os.environ.copy()
    
    # Core Task & Paths
    env["AGENT_TASK"] = data.get("task", "").strip()
    url = (data.get("llama_url") or "http://127.0.0.1:8080").strip().rstrip("/")
    if url.endswith("/v1"):
        env["LLAMA_URL"] = url
    else:
        env["LLAMA_URL"] = url + "/v1"
    env["AGENT_WORKSPACE"] = data.get("ws", "/home/tara/agent-workspace")
    
    # Sampling Suite
    env["CREW_TEMPERATURE"] = str(data.get("temperature", "0.2"))
    env["CREW_TOP_P"] = str(data.get("top_p", "0.95"))
    env["CREW_FREQ_PENALTY"] = str(data.get("freq_penalty", "0.0"))
    env["CREW_PRES_PENALTY"] = str(data.get("pres_penalty", "0.0"))
    env["CREW_CTX_SIZE"] = str(data.get("ctx_size", "65536"))
    env["CREW_MAX_TOKENS"] = str(data.get("max_tokens", "4096"))
    
    # Harness Parameters
    env["HARNESS_MAX_ITER"] = str(data.get("harness_iter", "35"))
    env["HARNESS_MAX_RETRY"] = str(data.get("harness_retry", "5"))
    env["HARNESS_MAX_EXEC_TIME"] = str(data.get("harness_timeout", "300"))
    
    env["PYTHONUNBUFFERED"] = "1"
    env["CREWAI_DISABLE_TELEMETRY"] = "true"

    async def stream():
        global current_process
        proc = await asyncio.create_subprocess_exec(
            str(VENV_PY), "-u", "runner.py",
            cwd=str(AGENT_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env
        )
        current_process = proc
        try:
            async for raw in proc.stdout:
                line = raw.decode(errors="replace")
                yield f"data: {json.dumps({'line': line})}\n\n"
        finally:
            await proc.wait()
            yield f"data: {json.dumps({'done': True, 'code': proc.returncode})}\n\n"
            current_process = None

    return StreamingResponse(stream(), media_type="text/event-stream")

@app.post("/api/stop")
async def stop():
    global current_process
    if current_process:
        try:
            current_process.terminate()
        except Exception:
            pass
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="warning")
