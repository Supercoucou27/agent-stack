import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
import re
import threading
import pty
import fcntl
import termios
import select
import queue
import yaml
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import httpx
import psutil

app = FastAPI(title="Unified LLM, Hermes & Cluster Deck")
STACK_DIR = Path(__file__).parent.resolve()
WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE", Path.home() / "agent-workspace")).resolve()
MODELS_DIR = STACK_DIR / "models"
GATEWAY_SOCK = Path(os.environ.get("HERMES_GATEWAY_SOCK", "/home/tara/.hermes/gateway.sock"))


def get_cpu_ram():
    gpus = []
    try:
        import pynvml
        pynvml.nvmlInit()
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode()
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            try:
                temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                temp = None
            gpus.append({"index": i, "name": name,
                         "vram_used_mb": round(mem.used / 1024 / 1024),
                         "vram_total_mb": round(mem.total / 1024 / 1024),
                         "util": util.gpu, "temp": temp})
    except Exception:
        pass
    return {"cpu_percent": round(psutil.cpu_percent(interval=0.3), 1),
            "ram": {"total_gb": round(psutil.virtual_memory().total / 1e9, 1),
                    "used_gb": round(psutil.virtual_memory().used / 1e9, 1),
                    "percent": psutil.virtual_memory().percent},
            "gpus": gpus, "storage": get_storage()}


def get_gpus():
    gpus = []
    try:
        import pynvml
        pynvml.nvmlInit()
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode()
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            try:
                temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                temp = None
            gpus.append({"index": i, "name": name,
                         "vram_used_mb": round(mem.used / 1024 / 1024),
                         "vram_total_mb": round(mem.total / 1024 / 1024),
                         "util": util.gpu, "temp": temp})
    except Exception:
        pass
    return gpus


def get_storage():
    try:
        part = shutil.disk_usage("/")
        return {"total": round(part.total / 1e9, 1),
                "used": round(part.used / 1e9, 1),
                "free": round(part.free / 1e9, 1)}
    except Exception:
        return {}


def get_stats():
    return {
        "cpu_percent": round(psutil.cpu_percent(interval=0.3), 1),
        "ram": {"total_gb": round(psutil.virtual_memory().total / 1e9, 1),
                "used_gb": round(psutil.virtual_memory().used / 1e9, 1),
                "percent": psutil.virtual_memory().percent},
        "gpus": get_gpus(),
        "storage": get_storage(),
    }


# ─── FRONT (serve index.html) ───────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/llm", response_class=HTMLResponse)
@app.get("/hermes", response_class=HTMLResponse)
@app.get("/bonsai", response_class=HTMLResponse)
async def get_index():
    return HTMLResponse((STACK_DIR / "index.html").read_text(encoding="utf-8"))


# ─── SANTÉ ────────────────────────────────────────────────────────────────────
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


# ─── MONITORING (8082 + 5001) ────────────────────────────────────────────────
@app.get("/api/system/metrics")
async def system_metrics():
    return JSONResponse(get_stats())


@app.get("/api/stats")
async def stats():
    return JSONResponse(get_stats())


# ─── LLAMA-SERVER (8080) ───────────────────────────────────────────────────────
@app.post("/api/llama/chat")
async def llm_chat(req: Request):
    data = await req.json()
    messages = data.get("messages")
    prompt = data.get("prompt")
    if messages is None:
        messages = [{"role": "user", "content": (prompt or "").strip()}]
    model = data.get("model") or str(MODELS_DIR / "Qwen3.8-27B-IQ3_XS-3.01bpw.gguf")
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


# ─── HERMES (CLI) ──────────────────────────────────────────────────────────────
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


# ─── MODÈLES ────────────────────────────────────────────────────────────────────
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

exec "$LLAMA_BIN" \\\
  -m "$MODEL_FILE" \\\
  --host 0.0.0.0 \\\\
  --port 8080 \\\\
  -c 32768 \\\\
  -t 6 \\\\
  -ngl 99 \\\\
  -ts 1,1 \\\\
  -sm layer \\\\
  -b 2048 \\\\
  -ub 512 \\\\
  -ctk q4_0 \\\\
  -ctv q4_0 \\\\
  --flash-attn on \\\\
  -np 1 \\\\
  --jinja \\\\
  --metrics
"""
    start_sh.write_text(script_content)
    subprocess.Popen(["sudo", "systemctl", "restart", "llama-server"])
    return {"ok": True, "active": model_name}


# ─── LLAMA-SERVER (8080) ───────────────────────────────────────────────────────
def _wait_llama_state(target_state: str, timeout: int = 60) -> tuple[bool, str, Optional[str]]:
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


# ─── TASK RUN (crew / agent) ───────────────────────────────────────────────────
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


# ─── FICHIERS ──────────────────────────────────────────────────────────────────
@app.get("/api/files")
async def list_files():
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(WORKSPACE.rglob("*")):
        if p.is_file() and not any(part.startswith(".") for part in p.parts):
            items.append({"rel": str(p.relative_to(WORKSPACE)), "size_kb": round(p.stat().st_size / 1024, 1)})
    return {"files": items[:250]}


# ─── CLUSTER (5001) — 6 workers ───────────────────────────────────────────────
CLUSTER_PLAN_RE = re.compile(r"^\s*(\d+)\s*[\\.)\-:]+\s*(.+?)\s*$")
CLUSTER_STATE = {
    "status": "idle", "objective": "", "start_time": 0.0, "total_time": 0.0,
    "workers": {f"worker_{i+1}": {"task": "", "result": "", "latency_sec": 0.0, "done": False, "status": "pending"} for i in range(6)},
    "report": "", "error": "", "logs_inspected": 0, "system_alerts": [],
}
MAX_CONCURRENCY_ = 6
WORKER_TIMEOUT_ = 180
CHEF_TIMEOUT_ = 240
WORKER_MAX_TOKENS_ = 1024
SUMMARY_MAX_TOKENS_ = 3072
CHEF_MODEL_ = os.getenv("CHEF_MODEL", "lfm3-omni:2b")
WORKER_MODEL_ = os.getenv("WORKER_MODEL", "lfm3:1.7b")
OLLAMA_URL_ = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")
OLLAMA_BASE_ = OLLAMA_URL_.replace("/v1", "")
WORKER_TIMEOUT_ = float(os.getenv("WORKER_TIMEOUT", "180"))
CHEF_TIMEOUT_ = float(os.getenv("CHEF_TIMEOUT", "240"))
MAX_CONCURRENCY_ = int(os.getenv("MAX_CONCURRENCY", "6"))
WORKER_MAX_TOKENS_ = int(os.getenv("WORKER_MAX_TOKENS", "1024"))
SUMMARY_MAX_TOKENS_ = int(os.getenv("SUMMARY_MAX_TOKENS", "3072"))
HERMES_MODEL_ = os.getenv("HERMES_MODEL", "lfm3-omni:2b")
HERMES_SESSION = "cluster_hermes"
CFG_PATH = Path.home() / ".hermes/config.yaml"

_client = httpx.AsyncClient(base_url=OLLAMA_URL_, timeout=240.0)


def _cluster_plan(objective: str):
    plan = (
        "Tu es le coordinateur principal d'un cluster d'agents.\n"
        f"Décompose cet objectif en exactement 6 sous-tâches atomiques distinctes :\n"
        f'"{objective}"\n\n'
        "Format strict :\n"
        "1. <tâche 1>\n2. <tâche 2>\n3. <tâche 3>\n4. <tâche 4>\n5. <tâche 5>\n6. <tâche 6>\n"
    )
    return plan


def _cluster_state():
    return CLUSTER_STATE


@app.get("/api/cluster/status")
async def cluster_status():
    return JSONResponse(CLUSTER_STATE)


@app.post("/api/cluster/run")
async def api_run(req: Request):
    data = await req.json()
    objective = (data or {}).get("objective", "").strip() or \
                "Audit de sécurité et d'intégrité d'un serveur Linux local exposé sur un réseau local"
    if CLUSTER_STATE["status"] in ("planning", "workers", "synthesizing"):
        return JSONResponse({"ok": False, "error": "Un cluster est déjà en cours."}, status_code=409)
    import threading
    threading.Thread(target=_start_thread, args=(objective,), daemon=True).start()
    return {"ok": True, "status": "started", "objective": objective}


async def _run_worker_task(wid, subtask):
    t0 = time.perf_counter()
    CLUSTER_STATE["workers"][f"worker_{wid}"]["task"] = subtask
    CLUSTER_STATE["workers"][f"worker_{wid}"]["status"] = "running"
    prompt = (
        f"Tu es le sous-agent d'exécution #{wid}. Tâche atomique : '{subtask}'. "
        "Réponds en 5 puces maximum, chacune ≤ 25 mots, format :\n"
        "- Constat : …\n- Risque : …\n- Action : …\n"
        "Pas de préambule, pas de conclusion."
    )
    try:
        r = await _client.post("/v1/chat/completions", json={
            "model": WORKER_MODEL_, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1, "max_tokens": WORKER_MAX_TOKENS_})
        data = r.json()
        output = data["choices"][0]["message"]["content"].strip()
        if data["choices"][0]["finish_reason"] == "length":
            output += " […tronqué]"
        status = "ok"
    except Exception as e:
        output = f"Erreur: {type(e).__name__}: {e}"
        status = "error"
    el = time.perf_counter() - t0
    CLUSTER_STATE["workers"][f"worker_{wid}"]["result"] = output
    CLUSTER_STATE["workers"][f"worker_{wid}"]["latency_sec"] = round(el, 2)
    CLUSTER_STATE["workers"][f"worker_{wid}"]["done"] = True
    CLUSTER_STATE["workers"][f"worker_{wid}"]["status"] = status
    CLUSTER_STATE["logs_inspected"] += 1
    if status == "error":
        CLUSTER_STATE["system_alerts"].append(f"worker_{wid} en erreur")

async def _orchestrate(objective: str):
    CLUSTER_STATE.update({
        "status": "planning", "objective": objective, "start_time": time.perf_counter(),
        "report": "", "error": "", "logs_inspected": 0, "system_alerts": [], "workers": {
            f"worker_{i+1}": {"task": "", "result": "", "latency_sec": 0.0, "done": False, "status": "pending"}
            for i in range(6)
        },
    })
    try:
        plan = _cluster_plan(objective)
        r = await _client.post("/v1/chat/completions", json={
            "model": CHEF_MODEL_, "messages": [{"role": "user", "content": plan}],
            "temperature": 0.2, "max_tokens": 512})
        data = r.json()
        lines = [m.group(2).strip()
                 for line in data["choices"][0]["message"]["content"].strip().splitlines()
                 if (m := CLUSTER_PLAN_RE.match(line))]
        tasks = lines[:6] if len(lines) >= 6 else [f"Sous-tâche {i+1} pour {objective}" for i in range(6)]
        async def _run_all(tasks):
            sem = asyncio.Semaphore(6)
            async def _run(i, t):
                async with sem:
                    return _run_worker_task(i + 1, t)
            return await asyncio.gather(*[_run(i, t) for i, t in enumerate(tasks)])
        await _run_all(tasks)
        synthesising = (
            "Résultats des 6 sous-agents :\n"
            + json.dumps(CLUSTER_STATE["workers"], ensure_ascii=False, indent=2)
            + f"\n\nAlertes : {CLUSTER_STATE['system_alerts']}\n\n"
            "Produis une synthèse exécutive structurée et actionnable."
        )
        r = await _client.chat.completions.create(
            model=CHEF_MODEL_, messages=[{"role": "user", "content": synthesising}],
            temperature=0.2, max_tokens=SUMMARY_MAX_TOKENS_)
        report = r.choices[0].message.content.strip()
        if r.choices[0].finish_reason == "length":
            report += "\n\n[ATTENTION] Synthèse tronquée."
        CLUSTER_STATE["report"] = report
        CLUSTER_STATE["total_time"] = round(time.perf_counter() - CLUSTER_STATE["start_time"], 2)
        CLUSTER_STATE["status"] = "completed"
    except Exception as e:
        CLUSTER_STATE["status"] = "error"
        CLUSTER_STATE["error"] = f"{type(e).__name__}: {e}"
        CLUSTER_STATE["total_time"] = round(time.perf_counter() - CLUSTER_STATE["start_time"], 2)


async def _run_worker_task(wid, subtask):
    t0 = time.perf_counter()
    CLUSTER_STATE["workers"][f"worker_{wid}"]["task"] = subtask
    CLUSTER_STATE["workers"][f"worker_{wid}"]["status"] = "running"
    prompt = (
        f"Tu es le sous-agent d'exécution #{wid}. Tâche atomique : '{subtask}'. "
        "Réponds en 5 puces maximum, chacune ≤ 25 mots, format :\n"
        "- Constat : …\n- Risque : …\n- Action : …\n"
        "Pas de préambule, pas de conclusion."
    )
    try:
        r = await _client.post("/v1/chat/completions", json={
            "model": WORKER_MODEL_, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1, "max_tokens": WORKER_MAX_TOKENS_})
        data = r.json()
        output = data["choices"][0]["message"]["content"].strip()
        if data["choices"][0]["finish_reason"] == "length":
            output += " […tronqué]"
        status = "ok"
    except Exception as e:
        output = f"Erreur: {type(e).__name__}: {e}"
        status = "error"
    el = time.perf_counter() - t0
    CLUSTER_STATE["workers"][f"worker_{wid}"]["result"] = output
    CLUSTER_STATE["workers"][f"worker_{wid}"]["latency_sec"] = round(el, 2)
    CLUSTER_STATE["workers"][f"worker_{wid}"]["done"] = True
    CLUSTER_STATE["workers"][f"worker_{wid}"]["status"] = status
    CLUSTER_STATE["logs_inspected"] += 1
    if status == "error":
        CLUSTER_STATE["system_alerts"].append(f"worker_{wid} en erreur")


def _start_thread(objective: str):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_orchestrate(objective))
    loop.close()


# ─── OLLAMA CONTROL (5001) ─────────────────────────────────────────────────────
def _sysctl(action):
    try:
        r = subprocess.run(["sudo", "-n", "systemctl", action, "ollama"],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}"


def ollama_status():
    try:
        r = subprocess.run(["sudo", "-n", "systemctl", "is-active", "ollama"],
                           capture_output=True, text=True, timeout=10)
        return {"active": (r.stdout.strip() == "active")}
    except Exception:
        return {"active": False}


@app.get("/api/ollama/status")
async def api_ollama():
    return JSONResponse(ollama_status())


@app.post("/api/ollama/start")
async def api_ollama_start():
    ok, out, err = _sysctl("start")
    time.sleep(2)
    st = ollama_status()
    return {"ok": ok, "action": "start", "status": st, "stderr": err[:300]}


@app.post("/api/ollama/stop")
async def api_ollama_stop():
    if CLUSTER_STATE["status"] in ("planning", "workers", "synthesizing"):
        return JSONResponse({"ok": False, "error": "Cluster en cours — attends la fin ou annule."}, status_code=409)
    ok, out, err = _sysctl("stop")
    time.sleep(1)
    st = ollama_status()
    return {"ok": ok, "action": "stop", "status": st, "stderr": err[:300]}


@app.post("/api/ollama/restart")
async def api_ollama_restart():
    ok, out, err = _sysctl("restart")
    time.sleep(2)
    st = ollama_status()
    return {"ok": ok, "action": "restart", "status": st, "stderr": err[:300]}


@app.get("/api/ollama/check-sudo")
async def api_ollama_check_sudo():
    try:
        r = subprocess.run(["sudo", "-n", "systemctl", "is-active", "ollama"],
                           capture_output=True, text=True, timeout=5)
        stderr = (r.stderr or "").lower()
        can_sudo = not ("password is required" in stderr or "a terminal is required" in stderr)
        return {"ok": True, "can_sudo": can_sudo, "stderr": r.stderr.strip()[:300]}
    except Exception as e:
        return {"ok": False, "can_sudo": False, "error": str(e)}


# ─── HERMES CONTROL (5001) ─────────────────────────────────────────────────────
def _yaml():
    import yaml as _y
    return _y


def _fc():
    try:
        with CFG_PATH.open() as f:
            return dict(yaml.safe_load(f) or {})
    except Exception:
        return {}


def _sc(c):
    try:
        CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        r = {}
        if CFG_PATH.exists():
            with CFG_PATH.open("r") as f:
                r = yaml.safe_load(f) or {}
        for k, v in c.items():
            nd = r
            for x in k.split(".")[:-1]:
                nd = nd.setdefault(x, {})
            nd[k.split(".")[-1]] = v
        with CFG_PATH.open("w") as f:
            yaml.dump(r, f)
        return True
    except Exception:
        return False


def find_hermes_bin():
    cands = [
        shutil.which("hermes"),
        Path.home() / ".hermes" / "bin" / "hermes",
        Path("/home/tara/agent-stack/venv/bin/hermes"),
        Path("/home/tara/.hermes/hermes-agent/venv/bin/hermes"),
    ]
    for c in cands:
        p = Path(c)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def ensure_hermes_config():
    cfg = _fc()
    model = cfg.get("model", {}) or {}
    updates = {}
    cur_url = str(model.get("base_url", ""))
    if not cur_url or ":8080" in cur_url:
        updates["model.base_url"] = OLLAMA_URL_
    if not model.get("provider"):
        updates["model.provider"] = "custom"
    if not model.get("default"):
        updates["model.default"] = HERMES_MODEL_
    if not model.get("context_length"):
        updates["model.context_length"] = 32768
    return _sc(updates) if updates else True


def hermes_status_():
    bin_path = find_hermes_bin()
    cfg = _fc()
    cur_url = str((cfg.get("model", {}) or {}).get("base_url", ""))
    ok = cur_url.rstrip("/") == OLLAMA_URL_.rstrip("/")
    return {
        "installed": bin_path is not None,
        "bin": bin_path,
        "config_path": str(CFG_PATH),
        "config_exists": CFG_PATH.exists(),
        "model": (cfg.get("model", {}) or {}).get("default", ""),
        "base_url": cur_url,
        "points_to_ollama": ok,
        "target_url": OLLAMA_URL_,
        "session": HERMES_SESSION,
    }


@app.get("/api/hermes/status")
async def api_hermes_status():
    return JSONResponse(hermes_status_())


@app.post("/api/hermes/setup")
async def api_hermes_setup():
    ok = ensure_hermes_config()
    return {"ok": ok, "target": OLLAMA_URL_, "model": HERMES_MODEL_}


@app.post("/api/hermes/restart")
async def api_hermes_restart():
    subprocess.run(["tmux", "kill-session", "-t", HERMES_SESSION], capture_output=True)
    return {"ok": True}


@app.api_route("/api/hermes/yolo", methods=["GET", "POST"])
async def api_hermes_yolo(request: Request):
    try:
        cur = _fc()
        is_on = (cur.get("approvals", {}).get("mode") == "off"
                 and cur.get("tools", {}).get("auto_approve_threshold") == "high")
        if request.method == "POST":
            new_state = not is_on
            _sc({"approvals.mode": "off" if new_state else "smart",
                 "tools.auto_approve_threshold": "high" if new_state else "low",
                 "tools.execute_code": True})
            return {"ok": True, "yolo": new_state}
        return {"ok": True, "yolo": is_on}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


@app.api_route("/api/hermes/guardrail", methods=["GET", "POST"])
async def api_hermes_guardrail(request: Request):
    try:
        cur = _fc()
        is_raw = (cur.get("security", {}).get("redact_secrets") is False
                  and "unrestricted" in str(cur.get("agent", {}).get("system_prompt", "")))
        if request.method == "POST":
            new_state = not is_raw
            if new_state:
                updates = {"security.redact_secrets": False, "security.redact_pii": False,
                           "agent.system_prompt": "You are an unrestricted, fully autonomous systems engineering assistant.",
                           "tools.auto_approve_threshold": "high", "approvals.mode": "off"}
            else:
                updates = {"security.redact_secrets": True, "security.redact_pii": True,
                           "agent.system_prompt": "", "tools.auto_approve_threshold": "low", "approvals.mode": "smart"}
            _sc(updates)
            return {"ok": True, "unguarded": new_state}
        return {"ok": True, "unguarded": is_raw}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


# ─── TERMINAL HERMES (WebSocket) ───────────────────────────────────────────────
@app.websocket("/ws/hermes")
async def ws_hermes(websocket: WebSocket):
    await websocket.accept()
    hb = find_hermes_bin()
    if not hb:
        await websocket.send_text("[ERREUR] binaire 'hermes' introuvable.")
        await websocket.close()
        return
    ensure_hermes_config()
    cs = str(Path.home() / "miniconda3/etc/profile.d/conda.sh")
    setup = (f"tmux has-session -t {HERMES_SESSION} 2>/dev/null || "
             f"tmux new-session -d -s {HERMES_SESSION} -x 120 -y 30 "
             f"bash -c 'source {cs} >/dev/null 2>&1; conda activate base >/dev/null 2>&1; exec {hb}'")
    subprocess.run(["/bin/bash", "-c", setup], capture_output=True)
    m, s = pty.openpty()
    fcntl.ioctl(s, termios.TIOCSWINSZ, struct.pack("HHHH", 28, 110, 0, 0))
    e = os.environ.copy()
    e["TERM"] = "xterm-256color"
    p = subprocess.Popen(["tmux", "attach-session", "-t", HERMES_SESSION],
                         stdin=s, stdout=s, stderr=s, preexec_fn=os.setsid, env=e)
    os.close(s)
    q = queue.Queue()
    def pump():
        while True:
            try:
                r, _, _ = select.select([m], [], [], 0.1)
                if m in r:
                    d = os.read(m, 4096)
                    if d:
                        q.put(d.decode("utf-8", errors="replace"))
                    else:
                        break
            except OSError:
                break
    threading.Thread(target=pump, daemon=True).start()
    try:
        while True:
            try:
                text = q.get(timeout=0.1)
            except queue.Empty:
                continue
            await websocket.send_text(text)
            try:
                x = await websocket.receive_text()
            except Exception:
                break
            if x.startswith("\x01RESIZE:"):
                try:
                    c, rr = map(int, x[8:].split(","))
                    fcntl.ioctl(m, termios.TIOCSWINSZ, struct.pack("HHHH", rr, c, 0, 0))
                except Exception:
                    pass
            else:
                os.write(m, x.encode("utf-8"))
    except Exception:
        pass
    finally:
        try:
            os.close(m)
        except Exception:
            pass


# ─── AGENT AUTONOME (5001) ─────────────────────────────────────────────────────
@app.post("/api/agent/chat")
async def api_agent_chat(req: Request):
    data = await req.json()
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "Empty prompt"}, status_code=400)
    env = os.environ.copy()
    env["AGENT_TASK"] = prompt
    env["CREW_TEMPERATURE"] = "0.2"
    env["HARNESS_MAX_ITER"] = "35"
    env["AGENT_WORKSPACE"] = str(WORKSPACE)
    env["LLAMA_URL"] = "http://127.0.0.1:8080"
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


# ─── SOUTIEN (module principal) ────────────────────────────────────────────────

# ─── LLM CONFIG (lama.cpp paramètres) ──────────────────────────────────────────
LLM_PARAMS = {
    'ctx_size':     {'type': 'int',  'default': 32768, 'min': 2048, 'max': 131072},
    'n_gpu_layers': {'type': 'int',  'default': 99,    'min': 0,    'max': 999},
    'tensor_split': {'type': 'str',  'default': '1,1'},
    'threads':      {'type': 'int',  'default': 6,     'min': 1,    'max': 6},
    'batch_size':   {'type': 'int',  'default': 2048,  'min': 128,  'max': 8192},
    'flash_attn':   {'type': 'str',  'default': 'auto', 'values': ['auto', 'on', 'off']},
    'temperature':  {'type': 'float','default': 0.7,   'min': 0,    'max': 2},
    'max_tokens':   {'type': 'int',  'default': 2048,  'min': 64,   'max': 16384},
}
LLM_CONFIG_FILE = STACK_DIR / "llm_config.yaml"
LLM_CONFIG_DELETE_ON_START = False


def _load_llm_config():
    if LLM_CONFIG_FILE.exists():
        try:
            return yaml.safe_load(LLM_CONFIG_FILE.read_text()) or {}
        except Exception:
            return {}
    return {}


def _save_llm_config(cfg):
    LLM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LLM_CONFIG_FILE.write_text(yaml.safe_dump(cfg, default_flow_style=False), encoding='utf-8')


def _validate_llm(cfg):
    validated = {}
    for k, spec in LLM_PARAMS.items():
        v = cfg.get(k, spec.get('default'))
        t = spec['type']
        if t == 'int':
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = spec['default']
            v = max(spec['min'], min(spec['max'], v))
        elif t == 'float':
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = spec['default']
            v = max(spec['min'], min(spec['max'], v))
        elif t == 'str':
            if k == 'tensor_split' and v not in ('1,1', '2,2'):
                v = '1,1'
        elif isinstance(spec.get('values'), list) and v not in spec['values']:
            v = spec['default']
        validated[k] = v
    return validated


def _apply_llm_config(cfg):
    """Rewrite llama-server.service drop-in to include the configured parameters."""
    import subprocess as _sp
    dropin = Path("/etc/systemd/system/llama-server.service.d/override.conf")
    # Use sudo to write the drop-in into the systemd system directory
    content = (
        "[Service]\n"
        "ExecStart=\n"
        "ExecStart=/home/tara/llama.cpp/build/bin/llama-server "
        f"-m /home/tara/agent-stack/models/Qwen3.8-27B-IQ3_XS-3.01bpw.gguf "
        f"--host 0.0.0.0 --port 8080 "
        f"-c {cfg['ctx_size']} -t {cfg['threads']} -ngl {cfg['n_gpu_layers']} "
        f"-ts {cfg['tensor_split']} -sm layer "
        f"-b {cfg['batch_size']} -ub {cfg['batch_size'] // 4} "
        f"-ctk q4_0 -ctv q4_0 --flash-attn {cfg['flash_attn']} -np 1 --jinja --metrics\n"
    )
    _sp.run(["sudo", "mkdir", "-p", str(dropin.parent)], capture_output=True, timeout=30)
    _sp.run(["sudo", "tee", str(dropin)], input=content, text=True, capture_output=True, timeout=30)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], capture_output=True, timeout=30)
    subprocess.run(["sudo", "systemctl", "restart", "llama-server"], capture_output=True, timeout=60)
    # wait for port
    import socket
    end = time.time() + 60
    while time.time() < end:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        r = s.connect_ex(('127.0.0.1', 8080))
        s.close()
        if r == 0:
            return True
        time.sleep(1)
    return False


@app.get("/api/llm/config")
async def llm_get():
    cfg = _load_llm_config()
    merged = {}
    for k, spec in LLM_PARAMS.items():
        merged[k] = cfg.get(k, spec.get("default"))
    return merged


@app.post("/api/llm/config")
async def llm_set(req: Request):
    data = await req.json()
    cfg = _validate_llm(data)
    _save_llm_config(cfg)
    ok = _apply_llm_config(cfg)
    return {"ok": ok, "config": cfg}


# ─── HERMES CONFIG ─────────────────────────────────────────────────────────────
HERMES_KEYS = {
    'model.default':         {'group': 'model'},
    'model.base_url':        {'group': 'model'},
    'model.provider':        {'group': 'model'},
    'model.context_length':  {'group': 'model'},
    'agent.max_turns':       {'group': 'agent'},
    'compression.threshold': {'group': 'agent'},
    'terminal.backend':      {'group': 'terminal'},
}
HERMES_CFG = Path.home() / ".hermes/config.yaml"


def _load_hermes_config():
    if HERMES_CFG.exists():
        try:
            return yaml.safe_load(HERMES_CFG.read_text()) or {}
        except Exception:
            return {}
    return {}


def _save_hermes_config(cfg):
    HERMES_CFG.parent.mkdir(parents=True, exist_ok=True)
    HERMES_CFG.write_text(yaml.safe_dump(cfg, default_flow_style=False), encoding='utf-8')


@app.get("/api/hermes/config")
async def hermes_get():
    cfg = _load_hermes_config()
    out = {}
    for k in HERMES_KEYS:
        cur = cfg
        for part in k.split('.'):
            cur = cur.get(part, {}) if isinstance(cur, dict) else {}
        out[k] = cur if isinstance(cur, (str, int, float, bool)) else (cur.get('default', '') if isinstance(cur, dict) else '')
    return out


@app.post("/api/hermes/config")
async def hermes_set(req: Request):
    data = await req.json()
    cfg = _load_hermes_config()
    for k, v in data.items():
        parts = k.split('.')
        node = cfg
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = v
    _save_hermes_config(cfg)
    return {"ok": True, "config": cfg}


# ─── MCP POINTS (tools / servers) ──────────────────────────────────────────────
def _mcp_load():
    cfg = _load_hermes_config()
    return cfg.get('mcp_servers', {}) or {}


def _mcp_save(servers):
    cfg = _load_hermes_config()
    cfg['mcp_servers'] = servers
    _save_hermes_config(cfg)
    subprocess.run(['hermes', 'reload-mcp'], capture_output=True)


@app.get("/api/mcp")
async def mcp_list():
    return {"servers": _mcp_load()}


@app.post("/api/mcp/add")
async def mcp_add(req: Request):
    data = await req.json()
    name = data.get('name')
    spec = data.get('spec', {})
    servers = _mcp_load()
    if name and name not in servers:
        servers[name] = spec
        _mcp_save(servers)
        return {"ok": True, "name": name}
    return {"ok": False, "error": "server exists or missing name"}


@app.post("/api/mcp/remove")
async def mcp_remove(req: Request):
    data = await req.json()
    name = data.get('name')
    servers = _mcp_load()
    if name in servers:
        del servers[name]
        _mcp_save(servers)
        return {"ok": True, "name": name}
    return {"ok": False, "error": "not found"}


@app.post("/api/mcp/toggle")
async def mcp_toggle(req: Request):
    data = await req.json()
    name = data.get('name')
    enabled = data.get('enabled')
    servers = _mcp_load()
    if name in servers:
        servers[name]['enabled'] = enabled
        _mcp_save(servers)
        return {"ok": True, "name": name, "enabled": enabled}
    return {"ok": False, "error": "not found"}


@app.post("/api/mcp/reload")
async def mcp_reload():
    subprocess.run(['hermes', 'reload-mcp'], capture_output=True)
    return {"ok": True}


# ─── SOUTIEN (module principal) ────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8082, log_level="info")
