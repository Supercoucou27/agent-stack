#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from openai import OpenAI

WORKSPACE = Path(os.environ.get("AGENT_WORKSPACE", "/home/tara/agent-workspace")).resolve()
CHROMA_PATH = Path("/home/tara/agent-stack/chroma")
RAW_LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8080/v1")
BASE_URL = RAW_LLAMA_URL.replace("localhost", "127.0.0.1").rstrip("/")
if not BASE_URL.endswith("/v1"):
    BASE_URL += "/v1"

TASK = (os.environ.get("AGENT_TASK") or "").strip()
if not TASK:
    print("ERROR: AGENT_TASK is empty.", flush=True)
    sys.exit(1)

WORKSPACE.mkdir(parents=True, exist_ok=True)

# 1. First priority: invoke official hermes CLI if available
hermes_bin = (
    shutil.which("hermes")
    or str(Path.home() / ".local" / "bin" / "hermes")
    or str(Path.home() / ".hermes" / "bin" / "hermes")
    or str(Path.home() / "agent-stack" / "venv" / "bin" / "hermes")
)

if Path(hermes_bin).is_file() and os.access(hermes_bin, os.X_OK):
    print(f"⚡ [Nous Hermes Engine] Running via CLI: {hermes_bin}", flush=True)
    print(f"📁 Workspace: {WORKSPACE}\n", flush=True)
    cmd = [hermes_bin, "chat", "-q", TASK, "--yolo"]
    env = os.environ.copy()
    env["PATH"] = f"{Path.home()}/.local/bin:{Path.home()}/agent-stack/venv/bin:{env.get('PATH', '')}"
    try:
        proc = subprocess.Popen(cmd, cwd=str(WORKSPACE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
        for line in proc.stdout:
            print(line, end="", flush=True)
        proc.wait()
        sys.exit(proc.returncode)
    except Exception as e:
        print(f"⚠️ Hermes CLI failed to spawn ({e}), falling back to direct Hermes runtime loop...", flush=True)

# 2. Fallback: Native Hermes Agent Tool-Calling Loop
TEMPERATURE = float(os.environ.get("CREW_TEMPERATURE", "0.2"))
TOP_P = float(os.environ.get("CREW_TOP_P", "0.95"))
MAX_TOKENS = int(os.environ.get("CREW_MAX_TOKENS", "4096"))
MAX_ITER = int(os.environ.get("HARNESS_MAX_ITER", "35"))
TIMEOUT_SEC = int(os.environ.get("HARNESS_MAX_EXEC_TIME", "300"))

def tool_shell(command: str, timeout_s: int = 120) -> str:
    limit = min(int(timeout_s), TIMEOUT_SEC)
    try:
        proc = subprocess.run(["bash", "-c", command], capture_output=True, text=True, cwd=str(WORKSPACE), timeout=limit)
        out = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
        if len(out) > 14000:
            out = out[:14000] + "\n...[truncated]"
        return f"exit_code={code}\n{out}" if out else f"exit_code={code} (no output)"
    except subprocess.TimeoutExpired:
        return f"ERROR: Timed out after {limit}s."
    except Exception as e:
        return f"ERROR: {e}"

def tool_read_file(filename: str) -> str:
    target = Path(filename).expanduser()
    if not target.is_absolute():
        target = WORKSPACE / target
    full = target.resolve()
    if not full.is_file():
        return f"ERROR: File '{full}' does not exist."
    try:
        return full.read_text(encoding="utf-8")[:60000]
    except UnicodeDecodeError:
        return f"ERROR: '{full}' is binary."

def tool_write_file(filename: str, content: str) -> str:
    target = Path(filename).expanduser()
    if not target.is_absolute():
        target = WORKSPACE / target
    full = target.resolve()
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return f"Success: wrote {len(content)} bytes to {full}"

def tool_list_files(directory_path: str = "") -> str:
    base = Path(directory_path).expanduser() if directory_path.strip() else WORKSPACE
    if not base.exists():
        return f"ERROR: Directory '{base}' missing."
    files = [str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()]
    return f"Files in {base}:\n- " + "\n- ".join(files[:200]) if files else f"'{base}' is empty."

HERMES_TOOLS = [
    {"type": "function", "function": {"name": "shell", "description": "Execute a bash shell command in the workspace.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read file contents.", "parameters": {"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write text to a file.", "parameters": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}}},
    {"type": "function", "function": {"name": "list_files", "description": "List files in directory.", "parameters": {"type": "object", "properties": {"directory_path": {"type": "string"}}}}}
]
TOOL_MAP = {"shell": tool_shell, "read_file": tool_read_file, "write_file": tool_write_file, "list_files": tool_list_files}

print("⚡ [Hermes Agent Native Loop] Initializing...", flush=True)
client = OpenAI(base_url=BASE_URL, api_key="sk-local-hermes", timeout=180.0)
messages = [
    {"role": "system", "content": f"You are Hermes, an autonomous AI engineer on Ubuntu.\nWorkspace: {WORKSPACE}\nExecute actions immediately using tools. Never print markdown json commands."},
    {"role": "user", "content": TASK}
]

for it in range(1, MAX_ITER + 1):
    resp = client.chat.completions.create(model="hermes-agent", messages=messages, tools=HERMES_TOOLS, tool_choice="auto", temperature=TEMPERATURE, top_p=TOP_P, max_tokens=MAX_TOKENS)
    msg = resp.choices[0].message
    messages.append(msg)
    
    if msg.content:
        print(msg.content.strip(), flush=True)

    if not msg.tool_calls:
        print("\n===RÉSULTAT FINAL===\n" + (msg.content or "(Completed)"), flush=True)
        sys.exit(0)

    for tc in msg.tool_calls:
        fn_name = tc.function.name
        try:
            fn_args = json.loads(tc.function.arguments or "{}")
        except Exception:
            fn_args = {}
        print(f"\n⚙️  <tool_call>\n{{\"name\": \"{fn_name}\", \"arguments\": {json.dumps(fn_args, ensure_ascii=False)}}}\n</tool_call>", flush=True)
        res = TOOL_MAP.get(fn_name, lambda **kw: "ERROR: unknown tool")(**fn_args)
        print(f"⬅️  <tool_response>\n{str(res)[:400]}\n</tool_response>", flush=True)
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(res)})

print(f"ERROR: Max iterations ({MAX_ITER}) reached.", flush=True)
sys.exit(1)
