"""Mede RAM, CPU e VRAM dos processos do runtime analitico.

Uso recomendado no host Linux de producao:

    sudo python3 -B scripts/measure_runtime_resources.py \
      --container server-analiticos-runtime --sample-seconds 1

O script e somente leitura. Quando executado no host, usa o PID inicial do
container para localizar todos os camera workers e cruza os PIDs publicados
nas metricas com o consumo reportado pelo nvidia-smi.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil


def _docker_container_pid(container: str) -> int | None:
    if not container.strip():
        return None
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Pid}}", container],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        value = int((result.stdout or "").strip())
        return value if result.returncode == 0 and value > 0 else None
    except Exception:
        return None


def _camera_pid_map(metrics_dir: Path) -> dict[int, int]:
    latest_by_camera: dict[int, Path] = {}
    try:
        paths = list(metrics_dir.glob("camera_*.json"))
    except Exception:
        return {}

    for path in paths:
        try:
            camera_id = int(path.name.split("_", 2)[1].split(".", 1)[0])
            current = latest_by_camera.get(camera_id)
            if current is None or path.stat().st_mtime_ns > current.stat().st_mtime_ns:
                latest_by_camera[camera_id] = path
        except Exception:
            continue

    mapped: dict[int, int] = {}
    for camera_id, path in latest_by_camera.items():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            pid = int(payload.get("worker_pid") or 0)
            if pid > 0:
                mapped[pid] = camera_id
        except Exception:
            continue
    return mapped


def _gpu_process_memory() -> dict[int, float]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return {}

    if result.returncode != 0:
        return {}

    usage: dict[int, float] = {}
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            memory_mb = float(parts[1])
        except (TypeError, ValueError):
            continue
        usage[pid] = usage.get(pid, 0.0) + memory_mb
    return usage


def _runtime_processes(root_pid: int | None) -> list[psutil.Process]:
    if root_pid:
        try:
            root = psutil.Process(root_pid)
            return [root, *root.children(recursive=True)]
        except (psutil.Error, OSError):
            pass

    matches: list[psutil.Process] = []
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            command = " ".join(process.info.get("cmdline") or []).lower()
            name = str(process.info.get("name") or "").lower()
            if "main.py" in command or "multiprocessing.spawn" in command or "camera-worker" in command:
                if "python" in name or "python" in command:
                    matches.append(process)
        except (psutil.Error, OSError):
            continue
    return matches


def _collect(
    processes: list[psutil.Process],
    *,
    sample_seconds: float,
    camera_by_pid: dict[int, int],
    gpu_by_pid: dict[int, float],
) -> list[dict[str, Any]]:
    unique = {process.pid: process for process in processes}
    for process in unique.values():
        try:
            process.cpu_percent(interval=None)
        except (psutil.Error, OSError):
            pass

    time.sleep(max(0.1, float(sample_seconds)))
    rows: list[dict[str, Any]] = []
    for pid, process in sorted(unique.items()):
        try:
            memory = process.memory_info()
            command = " ".join(process.cmdline())
            rows.append(
                {
                    "pid": pid,
                    "ppid": process.ppid(),
                    "camera_id": camera_by_pid.get(pid),
                    "cpu_percent": round(process.cpu_percent(interval=None), 2),
                    "rss_mb": round(memory.rss / (1024 * 1024), 2),
                    "vms_mb": round(memory.vms / (1024 * 1024), 2),
                    "gpu_memory_mb": round(gpu_by_pid.get(pid, 0.0), 2),
                    "command": command[:180],
                }
            )
        except (psutil.Error, OSError):
            continue
    return rows


def _print_table(rows: list[dict[str, Any]]) -> None:
    print("PID      PPID     CAM   CPU%    RSS MB   VRAM MB  COMANDO")
    print("-" * 104)
    for row in rows:
        camera = str(row["camera_id"]) if row["camera_id"] is not None else "-"
        print(
            f"{row['pid']:<8} {row['ppid']:<8} {camera:<5} "
            f"{row['cpu_percent']:>6.1f} {row['rss_mb']:>9.1f} "
            f"{row['gpu_memory_mb']:>9.1f}  {row['command']}"
        )
    print("-" * 104)
    print(
        f"Processos: {len(rows)} | "
        f"RSS total: {sum(row['rss_mb'] for row in rows):.1f} MB | "
        f"VRAM mapeada: {sum(row['gpu_memory_mb'] for row in rows):.1f} MB | "
        f"CPU somada: {sum(row['cpu_percent'] for row in rows):.1f}%"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="server-analiticos-runtime")
    parser.add_argument("--root-pid", type=int, default=0)
    parser.add_argument("--metrics-dir", default="data/runtime_state/metrics")
    parser.add_argument("--sample-seconds", type=float, default=1.0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    root_pid = int(args.root_pid or 0) or _docker_container_pid(str(args.container))
    processes = _runtime_processes(root_pid)
    rows = _collect(
        processes,
        sample_seconds=max(0.1, float(args.sample_seconds)),
        camera_by_pid=_camera_pid_map(Path(args.metrics_dir)),
        gpu_by_pid=_gpu_process_memory(),
    )

    if args.as_json:
        print(json.dumps({"root_pid": root_pid, "processes": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"Runtime root PID: {root_pid or 'nao identificado'}")
        _print_table(rows)
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
