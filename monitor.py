import os
import shutil
import subprocess
import psutil

def get_stats():
    # 1. CPU
    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_temp = None
    try:
        temps = psutil.sensors_temperatures()
        if "coretemp" in temps and temps["coretemp"]:
            cpu_temp = round(temps["coretemp"][0].current, 1)
        elif "k10temp" in temps and temps["k10temp"]:
            cpu_temp = round(temps["k10temp"][0].current, 1)
    except Exception:
        pass

    # 2. Host RAM
    ram = psutil.virtual_memory()
    ram_data = {
        "total_gb": round(ram.total / (1024**3), 2),
        "used_gb": round(ram.used / (1024**3), 2),
        "percent": ram.percent,
    }

    # 3. Disk (Workspace mount)
    ws_path = os.environ.get("AGENT_WORKSPACE", os.path.expanduser("~/agent-workspace"))
    if not os.path.exists(ws_path):
        ws_path = os.path.expanduser("~")
    disk = psutil.disk_usage(ws_path)
    disk_data = {
        "total_gb": round(disk.total / (1024**3), 1),
        "used_gb": round(disk.used / (1024**3), 1),
        "percent": disk.percent,
    }

    # 4. GPUs & Aggregate VRAM via nvidia-smi
    gpus = []
    total_vram_used_mb = 0
    total_vram_capacity_mb = 0

    if shutil.which("nvidia-smi"):
        try:
            cmd = [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                for line in res.stdout.strip().split("\n"):
                    if not line.strip():
                        continue
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 6:
                        idx = int(parts[0])
                        name = parts[1]
                        gpu_util = float(parts[2])
                        mem_used = float(parts[3])
                        mem_total = float(parts[4])
                        temp = float(parts[5])

                        vram_pct = round((mem_used / mem_total) * 100, 1) if mem_total > 0 else 0
                        total_vram_used_mb += mem_used
                        total_vram_capacity_mb += mem_total

                        gpus.append({
                            "index": idx,
                            "name": name,
                            "util_percent": gpu_util,
                            "mem_used_mb": round(mem_used),
                            "mem_total_mb": round(mem_total),
                            "mem_percent": vram_pct,
                            "temp_c": temp
                        })
        except Exception:
            pass

    aggregate_vram = {
        "used_mb": round(total_vram_used_mb),
        "total_mb": round(total_vram_capacity_mb),
        "used_gb": round(total_vram_used_mb / 1024, 2),
        "total_gb": round(total_vram_capacity_mb / 1024, 2),
        "percent": round((total_vram_used_mb / total_vram_capacity_mb) * 100, 1) if total_vram_capacity_mb > 0 else 0
    }

    return {
        "cpu": {"percent": cpu_percent, "temp_c": cpu_temp},
        "ram": ram_data,
        "disk": disk_data,
        "gpus": gpus,
        "vram_pool": aggregate_vram
    }
