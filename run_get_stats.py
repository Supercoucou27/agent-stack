import sys
import os
import threading
import psutil
import shutil
import subprocess

# Add the current directory to path
sys.path.insert(0, '.')

# Import the function from the candidate file
exec(open('get_stats_candidate.py').read())

# Test the function
print("Testing get_stats()...")
try:
    metrics = get_stats()
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
