import asyncio
import json
import os
import shutil
import socket
import struct
import subprocess
import time
import re
import logging
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

# ─── Logging Configuration (structured JSON output) ───────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("agent-stack")
logger.setLevel(logging.INFO)

app = FastAPI(title="Unified LLM, Hermes & Terminal Deck")
STACK_DIR = Path(__file__).parent.resolve()
WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE", Path.home() / "agent-workspace")).resolve()
MODELS_DIR = STACK_DIR / "models"
GATEWAY_SOCK = Path(os.environ.get("HERMES_GATEWAY_SOCK", "/home/tara/.hermes/gateway.sock"))
HERMES_SESSION = os.environ.get("HERMES_SESSION", "hermes_agent")

# ─── HERMES UTILITY ─────────────────────────────────────────────────────────────
def find_hermes_bin():
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


def ensure_hermes_config():
    """Crée un fichier de config minimal si absent."""
    cfg_path = Path.home() / ".hermes" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg_path.exists():
        cfg_path.write_text(
            "model:\n"
            "  default: llama\n"
            "  base_url: http://127.0.0.1:8080\n\n"
            "agent:\n"
            "  max_turns: 50\n"
            "  system_prompt: You are an autonomous systems engineering assistant.\n"
            "  context_length: 131072\n\n"
            "terminal:\n"
            "  backend: websocket\n\n"
            "approvals:\n"
            "  mode: smart\n"
            "tools:\n"
            "  auto_approve_threshold: low\n"
            "  execute_code: false\n\n"
            "security:\n"
            "  redact_secrets: true\n"
            "  redact_pii: true\n",
            encoding="utf-8"
        )
    return cfg_path


# ─── MCP CLIENT MANAGER (inside Hermes agent) ─────────────────────────────────
# Embeds MCP server management directly inside the Hermes agent, transforming
# it from a simple terminal executor into an MCP Host/Client.
import asyncio
import json
import subprocess
import shlex
from pathlib import Path
from typing import Optional

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import Tool, TextContent
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    ClientSession = None
    StdioServerParameters = None
    stdio_client = None


class MCPManager:
    """Manages MCP server lifecycle inside Hermes agent."""

    def __init__(self, config_path: Path = None):
        self.config_path = config_path or (Path.home() / '.hermes' / 'mcp_config.json')
        self.sessions = {}
        self._exit_stack = None
        self.tools = []
        self._running = False

    def _load_config(self) -> dict:
        """Load MCP server configuration from JSON file."""
        if not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f).get('mcpServers', {})
        except Exception as e:
            logger.warning(f'[MCP] Failed to load config: {e}')
            return {}

    def _load_config_from_env(self) -> dict:
        """Load MCP config if specified via HERMES_MCP_CONFIG env var."""
        env_path = os.environ.get('HERMES_MCP_CONFIG')
        if env_path and Path(env_path).exists():
            try:
                with open(env_path, 'r') as f:
                    return json.load(f).get('mcpServers', {})
            except Exception as e:
                logger.warning(f'[MCP] Failed to load env config: {e}')
        return {}

    async def initialize(self):
        """Start all MCP servers and create client sessions."""
        if not MCP_AVAILABLE:
            logger.warning('[MCP] SDK not available. Install with: pip install mcp')
            return

        self._exit_stack = AsyncExitStack()
        self.sessions = {}
        self.tools = []
        config = self._load_config()
        if not config:
            config = self._load_config_from_env()

        for server_name, params in config.items():
            try:
                # Setup standard I/O connection
                server_params = StdioServerParameters(
                    command=params.get("command", ""),
                    args=params.get("args", []),
                    env=params.get("env")
                )

                # Enter the context manager to keep the process alive
                read_stream, write_stream = await self._exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
                session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()

                self.sessions[server_name] = session
                logger.info(f'[MCP] Server "{server_name}" connected.')
            except Exception as e:
                logger.error(f'[MCP] Failed to start "{server_name}": {e}')

        # Discover all tools
        await self._discover_tools()

    async def _discover_tools(self):
        """Fetch tools from all connected MCP servers and prefix them."""
        self.tools = []
        for server_name, session in self.sessions.items():
            try:
                response = await session.list_tools()
                for tool in response.tools:
                    tool_id = f"{server_name}__{tool.name}"
                    self.tools.append({
                        "type": "function",
                        "function": {
                            "name": tool_id,
                            "description": tool.description or f"Tool from {server_name}",
                            "parameters": tool.inputSchema or {}
                        },
                        "server": server_name,
                        "original_name": tool.name
                    })
                    logger.debug(f'[MCP] Registered tool: {tool_id}')
            except Exception as e:
                logger.error(f'[MCP] Failed to list tools for {server_name}: {e}')

    def get_all_tools(self) -> list:
        """Synchronous version for initial loading."""
        return self.tools

    async def call_tool(self, tool_name: str, arguments: dict):
        """Route a tool call to the correct MCP server."""
        # Parse the prefixed tool name
        if '__' in tool_name:
            server_name, tool_name = tool_name.split('__', 1)
        else:
            # Backward compatibility - try direct match
            server_name = None
            for sn in self.sessions:
                if tool_name.startswith(sn + '__'):
                    server_name = sn
                    tool_name = tool_name[len(sn)+2:]
                    break

        if server_name not in self.sessions:
            return f"Error: Unknown MCP server '{server_name}'"

        # SECURITY: Enforce allowlist before execution
        allowed_tools = os.environ.get('HERMES_MCP_ALLOWLIST', '')
        if allowed_tools:
            allowed = [t.strip() for t in allowed_tools.split(',')]
            if tool_name not in allowed:
                return f"Security Error: Tool '{tool_name}' not in allowlist"

        session = self.sessions[server_name]
        try:
            result = await session.call_tool(tool_name, arguments)
            # Extract text content
            if hasattr(result, 'content'):
                for c in result.content:
                    if isinstance(c, TextContent):
                        return c.text
            return str(result)
        except Exception as e:
            return f"Error executing tool {tool_name}: {str(e)}"

    async def close(self):
        """Shut down all MCP server sessions."""
        if self._exit_stack:
            await self._exit_stack.aclose()
        self.sessions.clear()
        logger.info('[MCP] All servers closed.')

    def is_available(self) -> bool:
        return MCP_AVAILABLE

# ─── HERMES AGENT WITH MCP INTEGRATION ──────────────────────────────────────────
class HermesMCPModerator:
    """
    Hermes agent with integrated MCP support.
    Transforms Hermes into an MCP Host/Client that:
    1. Spawns MCP servers
    2. Discovers their tools
    3. Feeds tools to the LLM
    4. Routes LLM tool calls back to MCP servers
    5. Maintains WebSocket connection to UI
    """

    def __init__(self, llm_client, websocket, mcp_manager=None):
        self.llm = llm_client
        self.ws = websocket
        self.mcp = mcp_manager or MCPManager()
        self.history = []
        self._running = False
        self.busy = False

    async def start(self):
        """Initialize MCP and start listening."""
        await self.mcp.initialize()
        self._running = True
        logger.info(f'[MCP] Total tools available: {len(self.mcp.tools)}')
        await self.listen_ws()

    async def listen_ws(self):
        """Listen for messages on WebSocket."""
        async for message in self.ws.iter_text():
            await self.process_turn(message)

    async def process_turn(self, user_input: str):
        if self.busy:
            await self.ws.send_json({"status": "busy", "message": "Agent already working."})
            return

        self.history.append({"role": "user", "content": user_input})
        self.busy = True
        await self.ws.send_json({"status": "thinking", "message": "Reasoning..."})
        self.busy = False

        while True:
            try:
                # 1. Send prompt + tools to LLM
                response = await self.llm.chat(
                    messages=self.history,
                    tools=self.mcp.get_all_tools()
                )
            except Exception as e:
                await self.ws.send_json({"status": "error", "message": str(e)})
                break

            message = response.choices[0].message

            # 2. Check if LLM wants to use a tool
            if message.tool_calls:
                self.history.append(message)
                self.busy = True
                await self.ws.send_json({"status": "executing_tools", "tools": len(message.tool_calls)})

                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)

                    # Execute via MCP
                    result = await self.mcp.call_tool(tool_name, args)

                    # Feed result back to LLM
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": result
                    })

                self.busy = False
                # Continue loop to get next LLM response
            else:
                # No tool calls - just text response
                self.history.append({"role": "assistant", "content": message.content})
                await self.ws.send_json({
                    "status": "complete",
                    "response": message.content
                })
                break

    async def shutdown(self):
        self._running = False
        await self.mcp.close()


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


def get_cpu_temp():
    """Retourne la température CPU en °C si disponible."""
    try:
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        pass
    try:
        import psutil
        for t in psutil.sensors_temperatures():
            for entry in t.entries:
                if entry.label == "Tdie" and entry.current is not None:
                    return round(entry.current / 1000, 1)
    except Exception:
        pass
    return None


def get_cpu_core_usage():
    """Retourne l'utilisation par cœur CPU (si matériel disponible)."""
    try:
        cores = psutil.cpu_percent(interval=0.3, percpu=True)
        return {"cores": len(cores), "usage": [round(c, 1) for c in cores]}
    except Exception:
        return {"cores": 0, "usage": []}


def get_network_io():
    """Retourne I/O réseau depuis psutil (delta auto-géré)."""
    try:
        net = psutil.net_io_counters()
        return {"bytes_sent": net.bytes_sent, "bytes_recv": net.bytes_recv,
                "packets_sent": net.packets_sent, "packets_recv": net.packets_recv}
    except Exception:
        return {}


def get_disk_io():
    """Retourne I/O disque depuis psutil (delta auto-géré)."""
    try:
        dsk = psutil.disk_io_counters()
        return {"read_bytes": dsk.read_bytes if dsk else 0,
                "write_bytes": dsk.write_bytes if dsk else 0}
    except Exception:
        return {}


def get_process_metrics():
    """Retourne les métriques des processus (llama-server, hermes)."""
    procs = {}
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "memory_info", "create_time", "cmdline"]):
            try:
                name = p.info["name"] or "unknown"
                mem = p.info["memory_info"]
                if name in ("llama-server", "llama", "hermes", "node", "python"):
                    if name not in procs:
                        procs[name] = []
                    procs[name].append({
                        "pid": p.info["pid"],
                        "mem_mb": round(mem.rss / 1024 / 1024, 1) if mem else 0,
                        "create_time": p.info["create_time"],
                        "cmd": p.info["cmdline"][:4] if p.info["cmdline"] else ""
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass
    return procs


def get_stats():
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
            power = pynvml.nvmlDeviceGetPowerUsage(h)
            temps = {}
            for ttype in (pynvml.NVML_TEMPERATURE_GPU,
                          pynvml.NVML_TEMPERATURE_MEMORY,
                          pynvml.NVML_TEMPERATURE_ENCODER,
                          pynvml.NVML_TEMPERATURE_DECODER):
                try:
                    temps[pynvml.nvmlTemperatureTypeToString(ttype)] = \
                        pynvml.nvmlDeviceGetTemperature(h, ttype)
                except Exception:
                    pass
            try:
                power_state = pynvml.nvmlDeviceGetPowerManagementLimit(h)
                power_actual = pynvml.nvmlDeviceGetPowerManagementCounter(h)
                power_percent = (power_actual / power_state) * 100 if power_state and power_actual else None
            except Exception:
                power_percent = None
            gpus.append({"index": i, "name": name,
                         "vram_used_mb": round(mem.used / 1024 / 1024),
                         "vram_total_mb": round(mem.total / 1024 / 1024),
                         "vram_free_mb": round(mem.free / 1024 / 1024),
                         "utilization": {"gpu": util.gpu, "memory": util.memory},
                         "power_watts": round(power / 1000, 1),
                         "power_percent": round(power_percent, 1) if power_percent is not None else None,
                         "temps": temps})
    except Exception:
        pass
    return {
        "cpu": {
            "percent": round(psutil.cpu_percent(interval=0.3), 1),
            "cores": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
            "temperature": get_cpu_temp(),
        },
        "cpu_cores": get_cpu_core_usage(),
        "ram": {"total_gb": round(psutil.virtual_memory().total / 1e9, 1),
                "used_gb": round(psutil.virtual_memory().used / 1e9, 1),
                "percent": psutil.virtual_memory().percent,
                "available_gb": round(psutil.virtual_memory().available / 1e9, 1)},
        "gpus": gpus,
        "storage": get_storage(),
        "network": get_network_io(),
        "disk_io": get_disk_io(),
        "processes": get_process_metrics(),
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
# ─── LLM MODEL SELECTION (local llama / remote solar) ─────────────────────────
MODEL_CONFIG_FILE = STACK_DIR / "llm_model.yaml"

def _load_model_config():
    if MODEL_CONFIG_FILE.exists():
        try:
            return yaml.safe_load(MODEL_CONFIG_FILE.read_text()) or {}
        except Exception:
            pass
    return {"model": "llama"}

def _save_model_config(cfg):
    MODEL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODEL_CONFIG_FILE.write_text(yaml.safe_dump(cfg, default_flow_style=False), encoding='utf-8')

@app.get("/api/llm/model")
async def llm_model_get():
    """Get selected LLM model (local llama-server or remote solar)."""
    return {"model": _load_model_config().get("model", "llama"), "config": _load_model_config()}

@app.post("/api/llm/model")
async def llm_model_set(req: Request):
    """Set selected LLM model (local llama-server or remote solar)."""
    data = await req.json()
    model = data.get("model", "llama")
    cfg = _load_model_config()
    cfg["model"] = model
    _save_model_config(cfg)
    return {"ok": True, "model": model, "config": cfg}

# ─── LLAMA-SERVER (8080) ───────────────────────────────────────────────────────
@app.post("/api/llama/chat")
async def llm_chat(req: Request):
    data = await req.json()
    messages = data.get("messages")
    prompt = data.get("prompt")
    if messages is None:
        messages = [{"role": "user", "content": (prompt or "").strip()}]
    model = data.get("model")
    if not model:
        # Use the configured model selection
        model_cfg = _load_model_config()
        model = model_cfg.get("model", "llama")
    if model == "solar":
        # Remote Nous AI Solar Mini 4 (free) via Nous research API
        solar_base_url = os.environ.get("SOLAR_BASE_URL", "https://inference-api.nousresearch.com/v1")
        solar_key = os.environ.get("SOLAR_API_KEY", "")
        solar_payload = {
            "model": "upstage/solar-mini4:free",
            "messages": messages,
            "temperature": data.get("temperature", 0.7),
            "max_tokens": data.get("max_tokens", 2048),
        }
        try:
            headers = {"Content-Type": "application/json"}
            if solar_key:
                headers["x-api-key"] = solar_key
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(
                    f"{solar_base_url}/chat/completions",
                    json=solar_payload,
                    headers=headers,
                )
        except Exception as e:
            return JSONResponse({"error": f"solar: {str(e)}"}, status_code=502)
        if r.status_code != 200:
            body = r.text[:200] if r.text else ""
            return JSONResponse({"error": f"solar HTTP {r.status_code}: {body}"}, status_code=502)
        return r.json()
    else:
        # Local llama-server
        if not model:
            model = str(MODELS_DIR / "Qwen3.8-27B-IQ3_XS-3.01bpw.gguf")
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


# ─── SOUTIEN (module principal) ────────────────────────────────────────────────

# ─── LLM CONFIG (lama.cpp paramètres) ──────────────────────────────────────────
LLM_PARAMS = {
    'ctx_size':       {'type': 'int',  'default': 32768, 'min': 2048, 'max': 131072},
    'n_ctx_train':    {'type': 'int',  'default': 32768, 'min': 2048, 'max': 131072},
    'n_gpu_layers':   {'type': 'int',  'default': 99,    'min': 0,    'max': 999},
    'tensor_split':   {'type': 'str',  'default': '1,1'},
    'threads':        {'type': 'int',  'default': 6,     'min': 1,    'max': 6},
    'batch_size':     {'type': 'int',  'default': 2048,  'min': 128,  'max': 8192},
    'n_ubatch':       {'type': 'int',  'default': 512,   'min': 64,   'max': 8192},
    'n_past':         {'type': 'int',  'default': 512,   'min': 64,   'max': 32768},
    'n_seq_parallel': {'type': 'int',  'default': 1,     'min': 1,    'max': 8},
    'mlock':          {'type': 'bool', 'default': True},
    'f16_kv':         {'type': 'bool', 'default': False},
    'logits_all':     {'type': 'bool', 'default': False},
    'use_mmap':       {'type': 'bool', 'default': True},
    'use_mlock':      {'type': 'bool', 'default': False},
    'numa':           {'type': 'bool', 'default': False},
    'main_gpu':       {'type': 'int',  'default': 0,     'min': 0,    'max': 1},
    'flash_attn':     {'type': 'str',  'default': 'auto', 'values': ['auto', 'on', 'off']},
    'temperature':    {'type': 'float','default': 0.7,   'min': 0,    'max': 2},
    'max_tokens':     {'type': 'int',  'default': 2048,  'min': 64,   'max': 16384},
}
LLM_CONFIG_FILE = STACK_DIR / "llm_config.yaml"
LLM_CONFIG_DELETE_ON_START = False


def _load_llama_config():
    if LLM_CONFIG_FILE.exists():
        try:
            return yaml.safe_load(LLM_CONFIG_FILE.read_text()) or {}
        except Exception:
            return {}
    return {}


def _save_llama_config(cfg):
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
    cfg = _load_llama_config()
    merged = {}
    for k, spec in LLM_PARAMS.items():
        merged[k] = cfg.get(k, spec.get("default"))
    return merged


@app.post("/api/llm/config")
async def llm_set(req: Request):
    data = await req.json()
    cfg = _validate_llm(data)
    _save_llama_config(cfg)
    ok = _apply_llm_config(cfg)
    return {"ok": ok, "config": cfg}


# ─── HERMES CONFIG ─────────────────────────────────────────────────────────────
HERMES_KEYS = {
    'model.default':             {'group': 'model'},
    'model.base_url':            {'group': 'model'},
    'model.provider':            {'group': 'model'},
    'model.context_length':      {'group': 'model'},
    'agent.max_turns':           {'group': 'agent'},
    'agent.system_prompt':       {'group': 'agent'},
    'agent.reasoning_effort':    {'group': 'agent'},
    'compression.threshold':     {'group': 'agent'},
    'terminal.backend':          {'group': 'terminal'},
    'terminal.rows':             {'group': 'terminal'},
    'terminal.columns':          {'group': 'terminal'},
    'u8.temperature':            {'group': 'u8'},
    'u8.hallucination_rate':     {'group': 'u8'},
    'u8.verbosity':              {'group': 'u8'},
    'u8.encoding':               {'group': 'u8'},
    'u8.encoding_version':       {'group': 'u8'},
    'u8.scalar_dim':             {'group': 'u8'},
    'u8.qnrm':                   {'group': 'u8'},
    'u8.scalar_q':               {'group': 'u8'},
    'u8.qnrm_q':                 {'group': 'u8'},
    'u8.qs':                     {'group': 'u8'},
    'u8.qs_q':                   {'group': 'u8'},
    'u8.qk':                     {'group': 'u8'},
    'u8.qk_q':                   {'group': 'u8'},
    'u8.qv':                     {'group': 'u8'},
    'u8.qv_q':                   {'group': 'u8'},
    'u8.qo':                     {'group': 'u8'},
    'u8.qo_q':                   {'group': 'u8'},
    'u8.wo':                     {'group': 'u8'},
    'u8.wo_q':                   {'group': 'u8'},
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


# ─── HERMES CONTROL (yolo/guardrail) ────────────────────────────────────────────
@app.post("/api/hermes/yolo")
async def hermes_yolo(req: Request):
    data = await req.json()
    cur = _load_llama_config()
    is_on = (cur.get("approvals", {}).get("mode") == "off"
             and cur.get("tools", {}).get("auto_approve_threshold") == "high")
    if request.method == "POST":
        new_state = not is_on
        _save_llama_config({
            "approvals.mode": "off" if new_state else "smart",
            "tools.auto_approve_threshold": "high" if new_state else "low",
            "tools.execute_code": True,
        })
        return {"ok": True, "yolo": new_state}
    return {"ok": True, "yolo": is_on}

@app.post("/api/hermes/guardrail")
async def hermes_guardrail(req: Request):
    data = await req.json()
    cur = _load_llama_config()
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
        _save_llama_config(updates)
        return {"ok": True, "unguarded": new_state}
    return {"ok": True, "unguarded": is_raw}


# ─── TERMINAL HERMES (WebSocket) ────────────────────────────────────────────────
# Security: Token + origin validation on WebSocket handshake
import secrets
import time
from urllib.parse import urlparse

# Load secrets from .env or generate secure defaults
def _load_secrets():
    secrets_config = {
        'ws_token': os.environ.get('HERMES_WS_TOKEN', secrets.token_urlsafe(32)),
        'allowed_origins': os.environ.get('HERMES_ALLOWED_ORIGINS', 'http://127.0.0.1:8082').split(','),
    }
    # Save to .hermes/secrets.json if not already present
    secrets_file = Path.home() / '.hermes' / 'secrets.json'
    if not secrets_file.exists():
        secrets_file.parent.mkdir(parents=True, exist_ok=True)
        secrets_file.write_text(
            json.dumps(secrets_config, indent=2),
            encoding='utf-8'
        )
        # Also export to .env for compatibility
        env_file = Path.home() / '.hermes' / '.env'
        if not env_file.exists():
            env_file.parent.mkdir(parents=True, exist_ok=True)
            env_file.write_text(
                f"HERMES_WS_TOKEN={secrets_config['ws_token']}\n"
                f"HERMES_ALLOWED_ORIGINS={secrets_config['allowed_origins'][0]}\n",
                encoding='utf-8'
            )
    try:
        return json.loads(secrets_file.read_text(encoding='utf-8'))
    except Exception:
        return secrets_config

SECRETS = _load_secrets()
WS_TOKEN = os.environ.get('HERMES_WS_TOKEN', SECRETS['ws_token'])
default_origins = SECRETS.get('allowed_origins', ['http://127.0.0.1:8082'])
if isinstance(default_origins, str):
    default_origins = [o.strip() for o in default_origins.split(',')]
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get('HERMES_ALLOWED_ORIGINS', default_origins) if o.strip()]

def _validate_ws_token(token: str) -> bool:
    """Validate WebSocket authentication token."""
    if not token:
        return False
    # Accept token as query param or header token
    expected = WS_TOKEN
    if not isinstance(expected, str):
        expected = str(expected)
    return secrets.compare_digest(token, expected)

def _validate_origin(origin: str) -> bool:
    """Validate Origin header against allowlist."""
    if not origin:
        return False
    parsed = urlparse(origin)
    for allowed in ALLOWED_ORIGINS:
        if allowed in origin or allowed == parsed.hostname:
            return True
    return False

@app.websocket("/ws/hermes")
async def ws_hermes(websocket: WebSocket):
    # ─── Security: Validate token ───────────────────────────────────────────
    token = websocket.query_params.get('token')
    if not _validate_ws_token(token):
        await websocket.close(code=4401, reason='Unauthorized: invalid token')
        return

    # ─── Security: Validate Origin ──────────────────────────────────────────
    origin = websocket.headers.get('origin') or websocket.headers.get('Sec-Origin') or ''
    if not _validate_origin(origin):
        await websocket.close(code=4403, reason='Forbidden: invalid origin')
        return

    # ─── Security: Log connection (structured audit) ────────────────────────
    logger.info('WS_CONNECT', extra={
        'ip': websocket.client[0] if websocket.client else 'unknown',
        'user_agent': websocket.headers.get('User-Agent', 'unknown'),
        'origin': origin,
        'token_prefix': token[:8] + '***' if token and len(token) > 8 else token,
    })

    hb = find_hermes_bin()
    if not hb:
        await websocket.send_text("[ERREUR] binaire 'hermes' introuvable.")
        await websocket.close()
        return
    ensure_hermes_config()

    # ─── Initialize MCP Manager inside Hermes ──────────────────────────────
    mcp_manager = MCPManager()
    try:
        await mcp_manager.initialize()
        logger.info(f'[MCP] Hermes MCP modules loaded: {len(mcp_manager.tools)} tools')
    except Exception as e:
        logger.error(f'[MCP] Initialization failed: {e}')
        # Continue without MCP - backward compatible
        mcp_manager = None
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


# ─── AGENT AUTONOME ─────────────────────────────────────────────────────────────
@app.post("/api/agent/chat")
async def api_agent_chat(req: Request):
    """Agent chat endpoint with MCP tool support.

    Uses HermesMCPModerator which embeds MCP client management directly inside
    the Hermes agent, enabling dynamic tool discovery and routing.
    """
    data = await req.json()
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "Empty prompt"}, status_code=400)

    # Initialize MCP manager
    mcp_manager = MCPManager()
    try:
        await mcp_manager.initialize()
    except Exception as e:
        logger.warning(f'[MCP] Failed to initialize: {e}')
        mcp_manager = None

    # Create Hermes agent with MCP support
    agent = HermesMCPModerator(llm_client=None, websocket=None, mcp_manager=mcp_manager)

    # Set up the conversation
    agent.history = [
        {"role": "system", "content": "You are an autonomous local agent."},
        {"role": "user", "content": prompt}
    ]

    # If MCP tools are available, add them to the system prompt
    if mcp_manager and mcp_manager.tools:
        tool_desc = "\n\nAvailable MCP Tools:"
        for t in mcp_manager.tools:
            tool_desc += f'\n- {t["function"]["name"]}: {t["function"]["description"]}'
        agent.history[0]["content"] += tool_desc

    # Stream response back to client
    async def event_generator():
        nonlocal agent
        # Initial MCP discovery
        if mcp_manager and mcp_manager.tools:
            yield f"data: {json.dumps({'type': 'mcp_discovered', 'count': len(mcp_manager.tools)})}\n\n"

        # Run the agent loop
        try:
            while True:
                if agent.busy:
                    await asyncio.sleep(0.1)
                    continue

                agent.busy = True
                # Call LLM with tools
                try:
                    response = await agent.llm.chat(
                        messages=agent.history,
                        tools=agent.mcp.get_all_tools() if agent.mcp else None
                    )
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"
                    break

                message = response.choices[0].message

                # Check for tool calls
                if message.tool_calls:
                    agent.history.append(message)
                    yield f"data: {json.dumps({'type': 'tool_calls', 'count': len(message.tool_calls)})}\n\n"

                    for tool_call in message.tool_calls:
                        tool_name = tool_call.function.name
                        args = json.loads(tool_call.function.arguments)

                        # Route through MCP
                        result = await agent.mcp.call_tool(tool_name, args) if agent.mcp else f"[MCP Unavailable] Tool: {tool_name}"

                        # Feed result back to conversation
                        agent.history.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_name,
                            "content": result
                        })

                        yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool_name, 'result': result[:200]+'...' if len(result) > 200 else result})}\n\n"
                else:
                    # Text response
                    agent.history.append({"role": "assistant", "content": message.content})
                    yield f"data: {json.dumps({'type': 'complete', 'response': message.content})}\n\n"
                    break

                agent.busy = False
        except Exception as e:
            logger.error(f'[Agent] Error: {e}')
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─── SOUTIEN (module principal) ────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8082, log_level="info")
