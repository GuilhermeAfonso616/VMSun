from __future__ import annotations

import subprocess


def _safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def read_gpu_snapshot() -> dict:
    gpu = {
        "available": False,
        "name": None,
        "utilization_percent": None,
        "memory_used_mb": None,
        "memory_total_mb": None,
        "memory_allocated_mb": None,
        "memory_reserved_mb": None,
        "temperature_c": None,
        "device_count": 0,
    }

    try:
        import torch

        if torch.cuda.is_available():
            gpu["available"] = True
            gpu["device_count"] = int(torch.cuda.device_count())
            gpu["name"] = torch.cuda.get_device_name(0)

            props = torch.cuda.get_device_properties(0)
            gpu["memory_total_mb"] = round(float(props.total_memory) / (1024 * 1024), 2)
            gpu["memory_allocated_mb"] = round(float(torch.cuda.memory_allocated(0)) / (1024 * 1024), 2)
            gpu["memory_reserved_mb"] = round(float(torch.cuda.memory_reserved(0)) / (1024 * 1024), 2)
    except Exception:
        pass

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,name",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            first_line = result.stdout.strip().splitlines()[0]
            parts = [part.strip() for part in first_line.split(",")]
            if len(parts) >= 5:
                gpu["available"] = True
                gpu["utilization_percent"] = _safe_float(parts[0], gpu["utilization_percent"])
                gpu["memory_used_mb"] = _safe_float(parts[1], gpu["memory_used_mb"])
                gpu["memory_total_mb"] = _safe_float(parts[2], gpu["memory_total_mb"])
                gpu["temperature_c"] = _safe_float(parts[3], gpu["temperature_c"])
                gpu["name"] = parts[4] or gpu["name"]
    except Exception:
        pass

    return gpu
