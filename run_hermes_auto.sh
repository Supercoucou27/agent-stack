#!/bin/bash
set -euo pipefail

TASK="${1:-}"
if [ -z "$TASK" ]; then
    echo "Usage: ./run_hermes_auto.sh 'Votre tâche ici'"
    exit 1
fi

export AGENT_WORKSPACE="$HOME/agent-workspace"
export AGENT_TASK="$TASK"
source "$HOME/agent-stack/venv/bin/activate"

# Vérification rapide du endpoint local
if ! curl -s http://127.0.0.1:8080/v1/models >/dev/null; then
    echo "⚡ llama-server indisponible. Réveil par l'auto-runtime..."
    sudo systemctl restart llama-server
    until curl -s http://127.0.0.1:8080/v1/models >/dev/null; do
        sleep 1
    done
fi

cd "$HOME/agent-stack/my_agent/src"
python runner.py
