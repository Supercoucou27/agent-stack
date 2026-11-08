# llamaUI — Local Llama-Server Manager

**Native Qt desktop application for managing llama-server and LLM models locally.**

> [LlamaUI GitHub](https://github.com/NickPittas/llamaUI) · [Documentation](https://github.com/NickPittas/llamaUI#readme)

---

## What is llamaUI?

llamaUI is a native desktop UI for [llama.cpp](https://github.com/ggerganov/llama.cpp)'s `llama-server`. Built with **PySide6 / Qt Widgets**, targeting Linux (KDE Wayland + NVIDIA tested).

**Not a web app wrapped in Electron** — it is a real Qt application that talks directly to the llama-server binary.

---

## Features

### 📚 Library
Scan a directory of GGUF files. Distinguish **primary runnable models** from **companion files** (mmproj, text-encoder, vision-encoder, embedding GGUFs). Each model gets a card showing its quant, size, and hardware-fit estimate based on detected RAM/VRAM.

### 🔍 Discover
Search HuggingFace for GGUF models. Filter by GGUF format, gated repos, and multimodal support. Results ranked by local hardware-fit score using detected GPU VRAM, system RAM, CPU threads, model quant size, companion projectors, MTP/draft files, and long-context KV cache estimates.

### 🏃 Run
The control center with two modes:
- **Single Model**: Pick a model and profile, start/stop/restart. Live status tiles show state, PID, endpoint, and model name.
- **Router Mode**: Serve all models from your library via llama-server's native router with LRU eviction.

### 📊 Dashboard
Real-time server metrics and log viewer with three rolling charts (pure QPainter, zero external dependencies):
- **Throughput** — tokens/sec over time
- **Health latency** — API round-trip time in milliseconds
- **Slot utilization** — active slots vs total, with color flip from green to red at 80%

### ⚙️ Settings
- Point to your `llama-server` binary
- Set the models download directory
- Configure bind host and port
- Toggle router mode with models directory
- HuggingFace token management

### 🔌 MCP Integration
- Connect to MCP servers, filter their tools, extend your agent safely
- Local stdio servers and remote HTTP MCP servers in the same config
- Automatic tool discovery and registration at startup

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run llamaUI
python -m qt_app

# Or install globally
pip install -e .
llamaui
```

---

## Architecture

- **Frontend**: PySide6 Qt Widgets (two-pane splitter: collapsible sidebar + page stack)
- **Data layer**: Plain Python dataclasses + JSON files (ConfigStore, LibraryStore, ProfileStore)
- **Background tasks**: QThread subclasses for search, downloads, server management
- **MCP**: Integrated inside Hermes agent for tool routing and discovery

---

## Security

The WebSocket endpoint `/ws/hermes` requires authentication:
- Set token: `export HERMES_WS_TOKEN=$(openssl rand -hex 32)`
- Restrict origins: `export HERMES_ALLOWED_ORIGINS=http://your-domain.com`

See [SECURITY.md](SECURITY.md) for full details.

---

## Ports

| Port | Service |
|---|---|
| 8080 | llama-server (llama.cpp) |
| 8082 | Dashboard monitoring API (optional) |

---

## License

MIT
