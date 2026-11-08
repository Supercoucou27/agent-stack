import sys
import os
import types
sys.path.insert(0, '.')

# Create a mock FastAPI class to prevent real initialization
class MockFastAPI:
    def __init__(self, *args, **kwargs):
        self.title = "Mock"
    def route(self, *args, **kwargs):
        def decorator(f):
            return f
        return decorator
    def get(self, *args, **kwargs):
        def decorator(f):
            return f
        return decorator
    def post(self, *args, **kwargs):
        def decorator(f):
            return f
        return decorator
    def websocket(self, *args, **kwargs):
        def decorator(f):
            return f
        return decorator
    def on_event(self, *args, **kwargs):
        def decorator(f):
            return f
        return decorator

# Setup fake FastAPI modules
fastapi_mod = types.ModuleType('fastapi')
fastapi_mod.FastAPI = MockFastAPI
sys.modules['fastapi'] = fastapi_mod

responses_mod = types.ModuleType('fastapi.responses')
def JSONResponse(*args, **kwargs):
    return {}
responses_mod.JSONResponse = JSONResponse
sys.modules['fastapi.responses'] = responses_mod

# Also mock other needed imports
fastapi_responses_mod = types.ModuleType('fastapi.responses')
fastapi_responses_mod.JSONResponse = JSONResponse
sys.modules['fastapi.responses'] = fastapi_responses_mod

# Now execute web_app.py
with open('web_app.py', 'r') as f:
    code = f.read()

ns = {}
exec(code, ns)

# List available functions
available = [k for k in sorted(ns.keys()) if k.startswith('get_')]
print('Available functions:', available)
print()

# Test get_stats (should be the hardware metrics function)
if 'get_stats' in ns:
    print('Trying get_stats()...')
    try:
        metrics = ns['get_stats']()
        print('  CPU:', metrics['cpu'])
        print('  RAM:', metrics['ram'])
        print('  GPU count:', len(metrics['gpus']))
        for i, gpu in enumerate(metrics['gpus']):
            gpu_name = gpu.get('name', 'unknown')
            gpu_vram = f"{gpu['vram_used_mb']}/{gpu['vram_total_mb']} MB"
            gpu_util = gpu.get('utilization', {}).get('gpu', 'N/A')
            gpu_temp = gpu.get('temps', {}).get('GPU', 'N/A')
            print(f"    GPU{i}: {gpu_name} - VRAM: {gpu_vram}, Util: {gpu_util}%, Temp: {gpu_temp}")
        print('  Storage:', metrics['storage'])
        print('  Network:', metrics['network'])
        print('  Disk I/O:', metrics['disk_io'])
        print('  Processes:', {k: len(v) for k, v in metrics['processes'].items()})
        print('  Timestamp:', metrics.get('timestamp', 'N/A'))
    except Exception as e:
        print(f'  ERROR: {e}')
        import traceback
        traceback.print_exc()
else:
    print('  get_stats not found')
