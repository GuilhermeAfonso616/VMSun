"""Gera um pacote de diagnostico de consumo, pool e eventos por janela.

Uso dentro do container runtime:

    python -B scripts/generate_runtime_event_report.py --hours 12

Saida padrao:
    /data/logs/runtime_event_report_<timestamp>/

O script e somente leitura. Ele cruza:
- logs do worker e da pool;
- eventos SQLite;
- historico operacional JSONL;
- metricas atuais por camera;
- snapshot atual de processos Python, quando psutil estiver disponivel.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


LOG_SOURCES = ("app.log", "error.log", "inference_pool.log")
LOG_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| ")
KEY_VALUE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^ ]+)")


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except Exception:
                parsed = None
        if parsed is None:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat(sep=" ")


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def parse_log_line(line: str, source: str) -> dict[str, Any] | None:
    if not LOG_PREFIX_RE.match(line):
        return None
    parts = line.rstrip("\n").split(" | ")
    if len(parts) < 4:
        return None
    timestamp = parse_dt(parts[0])
    if timestamp is None:
        return None

    context: dict[str, str] = {}
    message_start = 3
    for index, part in enumerate(parts[3:], start=3):
        if "=" not in part:
            message_start = index
            break
        key, value = part.split("=", 1)
        context[key.strip()] = value.strip()
    else:
        message_start = min(len(parts), 17)

    message = " | ".join(parts[message_start:]).strip()
    raw_camera = context.get("cam") or context.get("camera_id") or "-"
    camera_id = None
    try:
        if str(raw_camera).strip() not in {"", "-"}:
            camera_id = int(raw_camera)
    except Exception:
        camera_id = None

    return {
        "timestamp": timestamp,
        "source": source,
        "level": parts[1].strip().upper() if len(parts) > 1 else "-",
        "logger": parts[2].strip() if len(parts) > 2 else "-",
        "camera_id": camera_id,
        "pid": context.get("pid") or context.get("worker_pid") or "-",
        "mode": context.get("mode") or "-",
        "action": context.get("action") or "-",
        "status": context.get("status") or "-",
        "reason": context.get("reason") or "-",
        "message": message,
    }


def iter_log_entries(logs_dir: Path, start: datetime, end: datetime) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name in LOG_SOURCES:
        candidates = sorted(logs_dir.glob(f"{name}*"))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for line in lines:
                entry = parse_log_line(line, path.name)
                if not entry:
                    continue
                ts = entry["timestamp"]
                if start <= ts <= end:
                    entries.append(entry)
    entries.sort(key=lambda item: item["timestamp"])
    return entries


def message_fields(message: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in KEY_VALUE_RE.finditer(str(message or ""))}


def summarize_worker_health(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    by_camera: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "samples": 0,
        "fps_sum": 0.0,
        "raw_fps_sum": 0.0,
        "processed_fps_sum": 0.0,
        "infer_ms_sum": 0.0,
        "loop_ms_sum": 0.0,
        "queue_dropped_max": 0,
        "reconnects_max": 0,
        "last_status": "-",
        "last_reason": "-",
    })
    for entry in entries:
        if entry.get("action") != "worker_health_snapshot":
            continue
        fields = message_fields(entry.get("message", ""))
        camera_id = entry.get("camera_id")
        row = {
            "timestamp": iso(entry["timestamp"]),
            "camera_id": camera_id,
            "pid": entry.get("pid"),
            "mode": entry.get("mode"),
            "status": entry.get("status"),
            "fps": to_float(fields.get("fps")),
            "raw_fps": to_float(fields.get("raw_fps")),
            "processed_fps": to_float(fields.get("processed_fps")),
            "tracks": to_int(fields.get("tracks")),
            "infer_ms": to_float(fields.get("infer_ms")),
            "loop_ms": to_float(fields.get("loop_ms")),
            "reconnects": to_int(fields.get("reconnects")),
            "dropped": to_int(fields.get("dropped")),
            "queue_dropped": to_int(fields.get("queue_dropped")),
            "persist_failed": to_int(fields.get("failed")),
            "persist_rejected": to_int(fields.get("rejected")),
            "last_inference_at": fields.get("last_inference_at", ""),
        }
        rows.append(row)
        if camera_id is None:
            continue
        summary = by_camera[int(camera_id)]
        summary["samples"] += 1
        summary["fps_sum"] += row["fps"]
        summary["raw_fps_sum"] += row["raw_fps"]
        summary["processed_fps_sum"] += row["processed_fps"]
        summary["infer_ms_sum"] += row["infer_ms"]
        summary["loop_ms_sum"] += row["loop_ms"]
        summary["queue_dropped_max"] = max(summary["queue_dropped_max"], row["queue_dropped"])
        summary["reconnects_max"] = max(summary["reconnects_max"], row["reconnects"])
        summary["last_status"] = row["status"]
        summary["last_reason"] = entry.get("reason", "-")

    for summary in by_camera.values():
        samples = max(1, int(summary["samples"]))
        summary["fps_avg"] = round(summary.pop("fps_sum") / samples, 2)
        summary["raw_fps_avg"] = round(summary.pop("raw_fps_sum") / samples, 2)
        summary["processed_fps_avg"] = round(summary.pop("processed_fps_sum") / samples, 2)
        summary["infer_ms_avg"] = round(summary.pop("infer_ms_sum") / samples, 2)
        summary["loop_ms_avg"] = round(summary.pop("loop_ms_sum") / samples, 2)
    return rows, dict(by_camera)


def summarize_pool(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    event_counts = Counter()
    camera_counts: dict[int, Counter] = defaultdict(Counter)
    pool_counts: dict[str, Counter] = defaultdict(Counter)
    for entry in entries:
        if "inference_pool" not in str(entry.get("action", "")) and entry.get("source") != "inference_pool.log":
            continue
        fields = message_fields(entry.get("message", ""))
        event = fields.get("event") or entry.get("action") or "-"
        camera_id = entry.get("camera_id")
        if camera_id is None and fields.get("camera_id") not in {None, "-", ""}:
            camera_id = to_int(fields.get("camera_id"), 0) or None
        pool_id = fields.get("pool_id", "-")
        row = {
            "timestamp": iso(entry["timestamp"]),
            "event": event,
            "camera_id": camera_id,
            "pool_id": pool_id,
            "status": entry.get("status"),
            "reason": entry.get("reason"),
            "queue_size": fields.get("queue_size", ""),
            "active_camera_id": fields.get("active_camera_id", ""),
            "http_ms": fields.get("http_ms", ""),
            "infer_ms": fields.get("infer_ms", ""),
            "submitted": fields.get("submitted", ""),
            "completed": fields.get("completed", ""),
            "timed_out": fields.get("timed_out", ""),
            "rejected": fields.get("rejected", ""),
            "dropped_oldest": fields.get("dropped_oldest", ""),
            "stale_dropped": fields.get("stale_dropped", ""),
            "message": entry.get("message", ""),
        }
        rows.append(row)
        event_counts[event] += 1
        if camera_id is not None:
            camera_counts[int(camera_id)][event] += 1
        if pool_id not in {"", "-"}:
            pool_counts[str(pool_id)][event] += 1
    return rows, {
        "events": dict(event_counts),
        "by_camera": {camera_id: dict(counter) for camera_id, counter in sorted(camera_counts.items())},
        "by_pool": {pool_id: dict(counter) for pool_id, counter in sorted(pool_counts.items())},
    }


def read_events(db_path: Path, start: datetime, end: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not db_path.exists():
        return [], {"error": f"database not found: {db_path}"}
    rows: list[dict[str, Any]] = []
    start_s = start.isoformat(sep=" ")
    end_s = end.isoformat(sep=" ")
    query = """
        SELECT
            e.id,
            e.camera_id,
            COALESCE(c.name, '') AS camera_name,
            e.event_type,
            e.status,
            e.severity,
            e.confidence,
            e.detector_score,
            e.event_score,
            e.track_id,
            e.created_at,
            e.started_at,
            e.ended_at,
            e.details,
            e.snapshot_path,
            e.clip_path
        FROM events e
        LEFT JOIN cameras c ON c.id = e.camera_id
        WHERE COALESCE(e.started_at, e.created_at) >= ?
          AND COALESCE(e.started_at, e.created_at) <= ?
        ORDER BY COALESCE(e.started_at, e.created_at), e.id
    """
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(query, (start_s, end_s)):
            rows.append(dict(row))

    by_type = Counter(str(row.get("event_type") or "-") for row in rows)
    by_status = Counter(str(row.get("status") or "-") for row in rows)
    by_severity = Counter(str(row.get("severity") or "-") for row in rows)
    by_camera = Counter(str(row.get("camera_id") or "-") for row in rows)
    return rows, {
        "total": len(rows),
        "by_type": dict(by_type),
        "by_status": dict(by_status),
        "by_severity": dict(by_severity),
        "by_camera": dict(by_camera),
    }


def read_operational_history(runtime_state_dir: Path, start: datetime, end: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base = runtime_state_dir / "operational_history"
    raw_rows: list[dict[str, Any]] = []
    summaries: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "samples": 0,
        "ia": 0,
        "online": 0,
        "warming": 0,
        "degraded": 0,
        "reconnecting": 0,
        "offline": 0,
        "stopped": 0,
        "unknown": 0,
        "first_state": "",
        "last_state": "",
        "first_seen": "",
        "last_seen": "",
        "camera_name": "",
    })
    if not base.exists():
        return raw_rows, []

    day = datetime(start.year, start.month, start.day)
    end_day = datetime(end.year, end.month, end.day)
    paths = []
    while day <= end_day:
        paths.append(base / f"camera_status_{day.strftime('%Y%m%d')}.jsonl")
        day += timedelta(days=1)

    for path in paths:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for line in lines:
            try:
                sample = json.loads(line)
            except Exception:
                continue
            ts = parse_dt(sample.get("ts"))
            if ts is None or not (start <= ts <= end):
                continue
            for camera in sample.get("cameras", []):
                if not isinstance(camera, dict):
                    continue
                camera_id = to_int(camera.get("id"), 0)
                if camera_id <= 0:
                    continue
                state = str(camera.get("state") or "unknown")
                name = str(camera.get("name") or f"Camera {camera_id}")
                raw_rows.append({
                    "timestamp": iso(ts),
                    "camera_id": camera_id,
                    "camera_name": name,
                    "state": state,
                    "health_status": camera.get("health_status", ""),
                    "worker_mode": camera.get("worker_mode", ""),
                    "worker_pid": camera.get("worker_pid", ""),
                    "last_frame_at": camera.get("last_frame_at", ""),
                    "last_successful_inference_at": camera.get("last_successful_inference_at", ""),
                    "metrics_age_seconds": camera.get("metrics_age_seconds", ""),
                })
                summary = summaries[camera_id]
                summary["samples"] += 1
                summary[state if state in summary else "unknown"] += 1
                summary["camera_name"] = name
                if not summary["first_seen"]:
                    summary["first_seen"] = iso(ts)
                    summary["first_state"] = state
                summary["last_seen"] = iso(ts)
                summary["last_state"] = state

    summary_rows = []
    for camera_id, summary in sorted(summaries.items()):
        row = {"camera_id": camera_id, **summary}
        samples = max(1, int(row["samples"]))
        row["ia_percent"] = round(100.0 * int(row["ia"]) / samples, 2)
        row["stopped_percent"] = round(100.0 * int(row["stopped"]) / samples, 2)
        row["offline_percent"] = round(100.0 * int(row["offline"]) / samples, 2)
        summary_rows.append(row)
    return raw_rows, summary_rows


def latest_metrics(runtime_state_dir: Path) -> list[dict[str, Any]]:
    metrics_dir = runtime_state_dir / "metrics"
    latest_by_camera: dict[int, Path] = {}
    if not metrics_dir.exists():
        return []
    for path in metrics_dir.glob("camera_*.json"):
        if path.name.endswith(".tmp.json"):
            continue
        match = re.match(r"camera_(\d+)", path.name)
        if not match:
            continue
        camera_id = int(match.group(1))
        current = latest_by_camera.get(camera_id)
        try:
            if current is None or path.stat().st_mtime_ns > current.stat().st_mtime_ns:
                latest_by_camera[camera_id] = path
        except Exception:
            continue

    rows: list[dict[str, Any]] = []
    for camera_id, path in sorted(latest_by_camera.items()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append({
            "camera_id": camera_id,
            "updated_at": payload.get("updated_at"),
            "worker_pid": payload.get("worker_pid"),
            "fps": payload.get("fps"),
            "raw_fps": payload.get("raw_fps"),
            "processed_fps": payload.get("processed_fps"),
            "infer_ms": payload.get("infer_ms"),
            "loop_ms": payload.get("loop_ms"),
            "process_cpu_percent": payload.get("process_cpu_percent"),
            "process_rss_mb": payload.get("process_rss_mb"),
            "system_cpu_percent": payload.get("system_cpu_percent"),
            "system_ram_percent": payload.get("system_ram_percent"),
            "inference_pool_backend": payload.get("inference_pool_backend"),
            "inference_pool_id": payload.get("inference_pool_id"),
            "inference_pool_queue_size": payload.get("inference_pool_queue_size"),
            "inference_pool_dropped_oldest": payload.get("inference_pool_dropped_oldest"),
            "inference_pool_stale_dropped": payload.get("inference_pool_stale_dropped"),
        })
    return rows


def gpu_memory_by_pid() -> dict[int, float]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits"],
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
        pid = to_int(parts[0], 0)
        if pid > 0:
            usage[pid] = usage.get(pid, 0.0) + to_float(parts[1])
    return usage


def current_processes(metrics_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        import psutil  # type: ignore
    except Exception:
        return []

    camera_by_pid = {}
    for row in metrics_rows:
        pid = to_int(row.get("worker_pid"), 0)
        if pid > 0:
            camera_by_pid[pid] = row.get("camera_id")
    gpu_by_pid = gpu_memory_by_pid()

    processes = []
    for process in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
        try:
            cmdline = " ".join(process.info.get("cmdline") or [])
            name = str(process.info.get("name") or "")
            if "python" not in name.lower() and "python" not in cmdline.lower():
                continue
            if "main.py" not in cmdline and process.pid not in camera_by_pid:
                continue
            mem = process.memory_info()
            processes.append({
                "pid": process.pid,
                "ppid": process.ppid(),
                "camera_id": camera_by_pid.get(process.pid),
                "cpu_percent": process.cpu_percent(interval=None),
                "rss_mb": round(mem.rss / (1024 * 1024), 2),
                "vms_mb": round(mem.vms / (1024 * 1024), 2),
                "gpu_memory_mb": round(gpu_by_pid.get(process.pid, 0.0), 2),
                "command": cmdline[:240],
            })
        except Exception:
            continue
    return sorted(processes, key=lambda row: (row["camera_id"] is None, row["camera_id"] or 0, row["pid"]))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_summary(
    path: Path,
    *,
    start: datetime,
    end: datetime,
    events_summary: dict[str, Any],
    worker_summary: dict[int, dict[str, Any]],
    pool_summary: dict[str, Any],
    state_summary: list[dict[str, Any]],
    metrics_rows: list[dict[str, Any]],
    process_rows: list[dict[str, Any]],
) -> None:
    stopped_like = [row for row in state_summary if row.get("last_state") in {"stopped", "offline", "warming", "unknown"}]
    heavy_workers = sorted(
        metrics_rows,
        key=lambda row: to_float(row.get("process_cpu_percent")) + to_float(row.get("loop_ms")) / 10.0,
        reverse=True,
    )[:10]
    backpressure_total = sum(pool_summary.get("events", {}).get(name, 0) for name in ("central_backpressure", "reject", "timeout", "stale_drop", "drop_oldest"))

    lines = [
        "# Relatorio runtime/eventos",
        "",
        f"Janela: {iso(start)} ate {iso(end)}",
        f"Eventos no banco: {events_summary.get('total', 0)}",
        f"Amostras de worker health: {sum(item.get('samples', 0) for item in worker_summary.values())}",
        f"Eventos de pool/log: {sum(pool_summary.get('events', {}).values())}",
        f"Sinais de backpressure/drop/timeout: {backpressure_total}",
        f"Processos atuais capturados: {len(process_rows)}",
        "",
        "## Eventos por tipo",
    ]
    for key, value in sorted(events_summary.get("by_type", {}).items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {key}: {value}")

    lines.append("")
    lines.append("## Eventos por camera")
    for key, value in sorted(events_summary.get("by_camera", {}).items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- camera {key}: {value}")

    lines.append("")
    lines.append("## Pool IA")
    for key, value in sorted(pool_summary.get("events", {}).items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {key}: {value}")

    lines.append("")
    lines.append("## Cameras com ultimo estado ruim")
    for row in stopped_like[:30]:
        lines.append(
            f"- camera {row.get('camera_id')} {row.get('camera_name')}: "
            f"ultimo={row.get('last_state')} stopped%={row.get('stopped_percent')} offline%={row.get('offline_percent')}"
        )

    lines.append("")
    lines.append("## Top consumo atual / loop")
    for row in heavy_workers:
        lines.append(
            f"- camera {row.get('camera_id')}: cpu={row.get('process_cpu_percent')}% "
            f"rss={row.get('process_rss_mb')}MB loop={row.get('loop_ms')}ms infer={row.get('infer_ms')}ms "
            f"pool={row.get('inference_pool_backend')}:{row.get('inference_pool_id')}"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=12.0)
    parser.add_argument("--logs-dir", default=os.getenv("LOGS_DIR", "/data/logs"))
    parser.add_argument("--runtime-state-dir", default=os.getenv("RUNTIME_STATE_DIR", "/data/runtime_state"))
    parser.add_argument("--database", default=os.getenv("DATABASE_URL", "sqlite:////data/analytics.db"))
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    end = datetime.now().replace(tzinfo=None)
    start = end - timedelta(hours=max(0.1, float(args.hours)))
    logs_dir = Path(args.logs_dir)
    runtime_state_dir = Path(args.runtime_state_dir)
    database = str(args.database)
    if database.startswith("sqlite:///"):
        db_path = Path(database.removeprefix("sqlite:///"))
    elif database.startswith("sqlite://"):
        db_path = Path(database.removeprefix("sqlite://"))
    else:
        db_path = Path(database)

    stamp = end.strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) if args.output_dir else logs_dir / f"runtime_event_report_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    log_entries = iter_log_entries(logs_dir, start, end)
    worker_rows, worker_summary = summarize_worker_health(log_entries)
    pool_rows, pool_summary = summarize_pool(log_entries)
    event_rows, events_summary = read_events(db_path, start, end)
    history_rows, state_summary = read_operational_history(runtime_state_dir, start, end)
    metrics_rows = latest_metrics(runtime_state_dir)
    process_rows = current_processes(metrics_rows)

    write_csv(output_dir / "worker_health_12h.csv", worker_rows)
    write_csv(output_dir / "inference_pool_12h.csv", pool_rows)
    write_csv(output_dir / "events_12h.csv", event_rows)
    write_csv(output_dir / "camera_state_history_12h.csv", history_rows)
    write_csv(output_dir / "camera_state_summary_12h.csv", state_summary)
    write_csv(output_dir / "current_camera_metrics.csv", metrics_rows)
    write_csv(output_dir / "current_processes.csv", process_rows)

    payload = {
        "window": {"start": iso(start), "end": iso(end), "hours": float(args.hours)},
        "paths": {
            "logs_dir": str(logs_dir),
            "runtime_state_dir": str(runtime_state_dir),
            "database": str(db_path),
            "output_dir": str(output_dir),
        },
        "events": events_summary,
        "worker_health_by_camera": worker_summary,
        "pool": pool_summary,
        "state_summary": state_summary,
        "current_metrics_count": len(metrics_rows),
        "current_process_count": len(process_rows),
    }
    (output_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_summary(
        output_dir / "summary.txt",
        start=start,
        end=end,
        events_summary=events_summary,
        worker_summary=worker_summary,
        pool_summary=pool_summary,
        state_summary=state_summary,
        metrics_rows=metrics_rows,
        process_rows=process_rows,
    )

    print(f"Relatorio gerado em: {output_dir}")
    print(f"Resumo: {output_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
