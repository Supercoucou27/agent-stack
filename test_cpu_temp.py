import sys
import os
import threading
import psutil
import shutil

# Read the full get_stats function
with open('web_app.py', 'r') as f:
    lines = f.readlines()

# Extract from 'def get_stats():' to the end of the function (before the cache wrapper)
start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if line.strip() == 'def get_stats():' and 'gpus = []' in line:
        start_idx = i
    if start_idx is not None and i > start_idx:
        if 'get_stats_raw():' in line or 'def get_stats_raw():' in line:
            end_idx = i
            break

if start_idx is None:
    print("get_stats function not found")
    exit(1)

# Extract the function
func_code = ''.join(lines[start_idx:end_idx])
print(f"Extracted {len(func_code)} chars of function code")

# Add threading import if needed
if 'import threading' not in func_code:
    func_code = 'import threading\n' + func_code

# Add execute in namespace
ns = {}
exec(func_code, ns)

# Test the function
print("\nTesting get_stats()...")
try:
    metrics = ns['get_stats']()
    print("  CPU:", metrics['cpu'])
    print("  RAM:", metrics['ram'])
    print("  GPU count:", len(metrics['gpus']))
    for i, gpu in enumerate(metrics['gpus']):
        gpu_name = gpu.get('name', 'unknown')
        gpu_vram = f"{gpu['vram_used_mb']}/{gpu['vram_total_mb']} MB"
        gpu_util = gpu.get('utilization', {}).get('gpu', 'N/A')
        gpu_temp = gpu.get('temps', {}).get('GPU', 'N/A')
        print(f"    GPU{i}: {gpu_name} - VRAM: {gpu_vram}, Util: {gpu_util}%, Temp: {gpu_temp}")
    print("  Storage:", metrics['storage'])
    print("  Network:", metrics['network'])
    print("  Disk I/O:", metrics['disk_io'])
    print("  Processes:", {k: len(v) for k, v in metrics['processes'].items()})
    print("  Timestamp:", metrics.get('timestamp', 'N/A'))
except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()
