#!/usr/bin/env python3
"""
Workstation Auto-Runtime Supervisor
Gère la persistance de llama-server, le nettoyage VRAM et le dispatch autonome.
"""
import time
import subprocess
import os
import sys
import httpx
from pathlib import Path

LLAMA_URL = "http://127.0.0.1:8080"
LLAMA_MODELS_URL = f"{LLAMA_URL}/v1/models"
CHECK_INTERVAL_SEC = 5

def check_endpoint() -> bool:
    try:
        r = httpx.get(LLAMA_MODELS_URL, timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False

def clean_and_restart_llama():
    print("[Auto-Runtime] ⚠️ llama-server ne répond plus. Récupération automatique...", flush=True)
    # Tuer tout processus fantôme
    subprocess.run(["sudo", "pkill", "-9", "-f", "llama-server"], capture_output=True)
    time.sleep(1)
    # Relancer le service systemd
    subprocess.run(["sudo", "systemctl", "restart", "llama-server"], check=True)
    
    # Attente active jusqu'à ce que le LLM soit prêt
    for _ in range(40):
        if check_endpoint():
            print("[Auto-Runtime] ✅ llama-server restauré et prêt sur le port 8080.", flush=True)
            return True
        time.sleep(1)
    print("[Auto-Runtime] ❌ Échec du démarrage de llama-server après 40s.", flush=True)
    return False

def main():
    print("🚀 [Auto-Runtime] Superviseur autonome démarré.", flush=True)
    consecutive_failures = 0

    while True:
        ready = check_endpoint()
        if not ready:
            consecutive_failures += 1
            print(f"[Auto-Runtime] Échec sonde de santé ({consecutive_failures}/3)...", flush=True)
            if consecutive_failures >= 3:
                clean_and_restart_llama()
                consecutive_failures = 0
        else:
            consecutive_failures = 0

        time.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    main()
