#!/bin/bash
set -euo pipefail

MODEL_FILE="$HOME/agent-stack/models/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"
LLAMA_BIN="$HOME/llama.cpp/build/bin/llama-server"

exec "$LLAMA_BIN" \  -m "$MODEL_FILE" \  --host 0.0.0.0 \\
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
