def get_stats():
    gpus = []
    try:
        import pynvml
        pynvml.nvmlInit()
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode()
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            power = pynvml.nvmlDeviceGetPowerUsage(h)
            temps = {}
            for ttype in (pynvml.NVML_TEMPERATURE_GPU,
                          pynvml.NVML_TEMPERATURE_MEMORY,
                          pynvml.NVML_TEMPERATURE_ENCODER,
                          pynvml.NVML_TEMPERATURE_DECODER):
                try:
                    temps[pynvml.nvmlTemperatureTypeToString(ttype)] = \
                        pynvml.nvmlDeviceGetTemperature(h, ttype)
                except Exception:
                    pass
            try:
                power_state = pynvml.nvmlDeviceGetPowerManagementLimit(h)
                power_actual = pynvml.nvmlDeviceGetPowerManagementCounter(h)
                power_percent = (power_actual / power_state) * 100 if power_state and power_actual else None
            except Exception:
                power_percent = None
            gpus.append({"index": i, "name": name,
                         "vram_used_mb": round(mem.used / 1024 / 1024),
                         "vram_total_mb": round(mem.total / 1024 / 1024),
                         "vram_free_mb": round(mem.free / 1024 / 1024),
                         "utilization": {"gpu": util.gpu, "memory": util.memory},
                         "power_watts": round(power / 1000, 1),
                         "power_percent": round(power_percent, 1) if power_percent is not None else None,
                         "temps": temps})
    except Exception:
        pass
    return {
        "cpu": {
            "percent": round(psutil.cpu_percent(interval=0.3), 1),
            "cores": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
            "temperature": get_cpu_temp(),
        },
        "cpu_cores": get_cpu_core_usage(),
        "ram": {"total_gb": round(psutil.virtual_memory().total / 1e9, 1),
                "used_gb": round(psutil.virtual_memory().used / 1e9, 1),
                "percent": psutil.virtual_memory().percent,
                "available_gb": round(psutil.virtual_memory().available / 1e9, 1)},
        "gpus": gpus,
        "storage": get_storage(),
        "network": get_network_io(),
        "disk_io": get_disk_io(),
