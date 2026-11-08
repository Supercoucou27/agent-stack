# Unified LLM Controller & Terminal Deck

**Dashboard:** `http://127.0.0.1:8082/`
**Backend:** FastAPI (uvicorn) on port 8082
**LLM Backend:** llama-server on port 8080
**Hermes Agent:** Interactive CLI agent via WebSocket

## Architecture

```
┌──────────────────────────────────────────────┐
│  Browser (http://127.0.0.1:8082)             │
│  Unified Dashboard (index.html)              │
└──────────────────┬───────────────────────────┘
                   │ WebSocket /api/system/metrics
                   ▼
┌──────────────────┴───────────────────────────┐
│  FastAPI app (web_app.py)                     │
│  Port 8082                                    │
│  - /api/health                              │
│  - /api/system/metrics (hardware telemetry)  │
│  - /api/llama/chat (local + remote solar)    │
│  - /api/llama/start|stop|restart             │
│  - /api/llm/model (llama vs solar)           │
│  - /api/hermes/* (terminal, status, yolo)    │
│  - /api/ollama/* (ollama control)            │
│  - /api/cluster/* (Bonsai cluster)           │
│  - /ws/hermes (interactive terminal WS)      │
│  - /api/mcp/* (MCP config)                   │
└──────────────────┬───────────────────────────┘
                   │
     ┌─────────────┼─────────────┐
     ▼             ▼             ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ llama-    │ │ Solar   │ │ Hermes   │
│ server   │ │ Mini 4  │ │ agent    │
│ (8080)   │ │ (free)  │ │ CLI      │
│          │ │          │ │          │
│ systemd  │ │ Nous    │ │            │
│ service  │ │ research │ │            │
│          │ │ API      │ │            │
└──────────┘ └──────────┘ └──────────┘
```

## Features

### 🖥️ Hardware Metrics
- GPU VRAM allocation (RTX 2070, RTX 2060 SUPER)
- Combined VRAM pool
- CPU core load (per-core)
- Host RAM (total, used, available)
- Workspace disk (used, free)
- Network I/O (bytes sent/received)
- Disk I/O (read/write bytes)
- Process metrics (llama-server, hermes, python)

### 🤖 LLM Control
- **Local llama-server:** Qwen3.8-27B-3.01bpw (GGUF)
- **Remote Solar Mini 4:** Nous Research free tier
- Switch between models via UI
- Start, stop, restart with health checks
- Model switching with hot reload

### ⚙️ LLM Configuration
- Context size (ctx_size)
- Context train (n_ctx_train)
- GPU layers (n_gpu_layers)
- Tensor split (tensor_split)
- Threads (n_threads)
- Batch size (n_batch)
- Upper batch (n_ubatch)
- Past (n_past)
- Seq parallel (n_seq_parallel)
- MLock (mlock)
- FP16 KV (f16_kv)
- Logits all (logits_all)
- Use mmap (use_mmap)
- Use mlock (use_mlock)
- NUMA (numa)
- Main GPU (main_gpu)
- Flash attention (flash_attn)
- Temperature
- Max tokens (max_tokens)

### 🧙 Hermes Control
- Interactive WebSocket terminal (`/ws/hermes`)
- Status monitoring
- Yolo mode (toggle)
- Guardrail toggle
- Config via `/api/hermes/config` (GET/POST)
- System prompt
- Reasoning effort (high/medium/low)
- Terminal rows and columns
- U8 quantization parameters (temperature, hallucination rate, verbosity, encoding, etc.)

### 🔌 MCP Points (inside Hermes agent)
- List configured MCP servers
- Add new MCP servers (stdio or HTTP transport)
- Remove MCP servers
- Toggle enable/disable
- Reload MCP config

### 📊 LLM Model Selection
- Switch between local llama-server and remote Solar Mini 4 (free)
- Choice on the frontend at `/llm` and `/`
- Auto-detect model availability
- Add new MCP servers (stdio or HTTP transport)
- Toggle enable/disable
- Reload MCP config

## Setup

### Prerequisites
- Python 3.8+ with virtualenv
- llama-server (llama.cpp) on host 0.0.0.0:8080
- Qwen3.8-27B-3.01bpw GGUF model at `~/agent-stack/models/`
- tmux (for interactive terminal)
- Heroku CLI (for Hermes agent)

### Installation

```bash
# Clone the repository
git clone https://github.com/Supercoucou27/agent-stack.git
cd agent-stack

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install fastapi uvicorn httpx pynvml psutil

# Create systemd service
sudo cp agent-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable agent-web
sudo systemctl start agent-web

# Start your LLM backend (llama-server)
# Start the interactive terminal WebSocket
# Open the dashboard in your browser
```

### Available systemd services
- `agent-web` — The dashboard on port 8082
- `llama-server` — Llama.cpp server (recreated for this project)
- `llama-bonsai` — Bonsai LLM control unit
- `llama-qwen-moe` — Qwen MoE model unit

> **Note:** Port 5001 (Bonsai Cluster Control on Flask) is stopped/disabled by design. The dashboard on 8082 is the unified interface.

## Configuration

### LLM Model Selection
The dashboard can switch between:
- `llama` — Local llama-server (default)
- `solar` — Remote Solar Mini 4 (free tier via Nous research API)

Set the model via UI or API:
```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"model":"solar"}' \
  http://127.0.0.1:8082/api/llm/model
```

### Llama Configuration
```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"ctx_size":16384,"n_gpu_layers":99,"tensor_split":"1,1","threads":8,"batch_size":2048,"flash_attn":"auto","temperature":0.7,"max_tokens":2048}' \
  http://127.0.0.1:8082/api/llama/config
```

### MCP Configuration
```bash
# List MCP servers
curl http://127.0.0.1:8082/api/mcp

# Add an MCP server
curl -X POST -H "Content-Type: application/json" \
  -d '{"name":"filesystem","spec":{"command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/home/user/projects"],"enabled":true,"timeout":120,"trust":"untrusted"}}' \
  http://127.0.0.1:8082/api/mcp/add

# Reload MCP config
curl -X POST http://127.0.0.1:8082/api/mcp/reload
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Health check (llama status) |
| `/api/system/metrics` | GET | Hardware metrics (CPU, RAM, GPU, disk) |
| `/api/llama/chat` | POST | Chat with LLM (local or solar) |
| `/api/llama/start` | POST | Start llama-server |
| `/api/llama/stop` | POST | Stop llama-server |
| `/api/llama/restart` | POST | Restart llama-server |
| `/api/llama/model` | GET/POST | Get/set active LLM model |
| `/api/llm/config` | GET/POST | Get/set llama.cpp parameters |
| `/api/hermes/send` | POST | Send message to Hermes terminal |
| `/api/hermes/status` | GET | Hermes status |
| `/api/hermes/yolo` | GET/POST | Yolo mode |
| `/api/hermes/guardrail` | GET/POST | Guardrail toggle |
| `/api/ollama/status` | GET | Ollama status |
| `/api/ollama/start` | POST | Start Ollama |
| `/api/ollama/stop` | POST | Stop Ollama |
| `/api/ollama/restart` | POST | Restart Ollama |
| `/api/ollama/check-sudo` | GET | Check sudo access |
| `/api/cluster/run` | POST | Run cluster job |
| `/api/cluster/status` | GET | Cluster status |
| `/api/mcp` | GET | List MCP servers |
| `/api/mcp/add` | POST | Add MCP server |
| `/api/mcp/toggle` | POST | Toggle MCP server |
| `/api/mcp/remove` | POST | Remove MCP server |
| `/ws/hermes` | WS | Interactive terminal WebSocket |

## Files & Directories

```
agent-stack/
├── web_app.py          # FastAPI backend (main dashboard)
├── index.html          # Unified frontend (single-page app)
├── llama-params.json   # LLM parameters
├── llama-server.service  # systemd service for llama.cpp
├── agent-web.service     # systemd service for the dashboard
├── .env                  # Environment variables (gitignored)
├── .gitignore            # Git ignore rules
├── requirements.txt      # Python dependencies
├── auto_runtime.py       # Auto-runtime for agent
├── start_llama_server.sh # Start script for llama.cpp
└── models/              # Model weights directory
```

## Ports

| Port | Service |
|---|---|
| 8080 | llama-server (llama.cpp) |
| 8082 | **Dashboard** (fastapi / web_app.py) |
| 8090 | Hermes web console |

## Troubleshooting

### llama-server won't start
- Check the model path: `/home/tara/agent-stack/models/`
- Check the systemd service: `sudo systemctl status llama-server`
- Check the logs: `sudo journalctl -u llama-server -f`

### Dashboard won't load
- Check the service: `sudo systemctl status agent-web`
- Check the port: `ss -tlnp | grep 8082`
- Check the logs: `sudo journalctl -u agent-web -f`

### WebSocket terminal not working
- Verify tmux is installed: `which tmux`
- Check the hermes binary: `which hermes`
- Check the WebSocket connection in the browser console

### Solar Mini 4 API key
The Solar Mini 4 (free tier) requires a Nous research API key.
```bash
export SOLAR_API_KEY=your_api_key_here
```

## License

MIT
