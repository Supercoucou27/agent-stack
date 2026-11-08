# Unified LLM Controller & Cluster Deck

**Dashboard:** `http://127.0.0.1:8082/`
**Backend:** FastAPI (uvicorn) on port 8082
**LLM Backend:** llama-server on port 8080
**Hermes Web Console:** port 8090 (separate)

## Overview

This is a local AI dashboard that controls:
- **LLM Direct:** Chat with the local llama-server (Qwen3.8-27B) or remote Solar Mini 4 (free tier)
- **Hardware Metrics:** GPU VRAM, CPU, RAM, and disk telemetry with real-time graphs
- **LLM Configuration:** Context size, GPU layers, tensor split, threads, batch size, flash attention, temperature, max tokens
- **Hermes Control:** Interactive terminal (WebSocket), status, yolo/guardrail, config
- **Ollama Control:** Start, stop, restart, status check, sudo verification
- **Claude 8082:** Agent chat, task management, auto-runtime
- **MCP Points:** Configurable MCPs with stdio/HTTP transport

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
- CPU core load
- Host RAM
- Workspace disk

### 🤖 LLM Control
- **Local llama-server:** Qwen3.8-27B-3.01bpw (GGUF)
- **Remote Solar Mini 4:** Nous Research free tier
- Switch between models via UI
- Start, stop, restart with health checks
- Model switching with hot reload

### ⚙️ LLM Configuration
- Context size (ctx_size)
- GPU layers (n_gpu_layers)
- Tensor split (tensor_split)
- Threads
- Batch size
- Flash attention
- Temperature
- Max tokens

### 🧙 Hermes Control
- Interactive WebSocket terminal
- Status monitoring
- Yolo mode
- Guardrail toggle
- Config via `hermes config set`

### 🤖 Ollama Control
- Start, stop, restart
- Status check
- Sudo verification

### 📊 Bonsai Cluster Control
- 6-worker cluster orchestration
- Objective-based task management
- Status polling

### 🔌 MCP Points
- List configured MCP servers
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

### Ollama Control
```bash
# Check Ollama status
curl http://127.0.0.1:8082/api/ollama/status

# Start Ollama
curl -X POST http://127.0.0.1:8082/api/ollama/start

# Stop Ollama
curl -X POST http://127.0.0.1:8082/api/ollama/stop
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
