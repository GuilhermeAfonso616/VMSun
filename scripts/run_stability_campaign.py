#!/usr/bin/env python3
"""Executa campanhas de estabilidade e capacidade do Analitico.

O modo ``observe`` e somente leitura. O modo ``campaign`` somente controla
cameras quando ``--allow-camera-control`` e informado explicitamente.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import math
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_CONTAINERS = (
    "server-analiticos",
    "server-analiticos-runtime",
    "camera-gateway",
    "webrtc-gateway",
    "analitico-postgres",
)
HEALTHY_CAMERA_STATES = {"running", "running_motion_test", "ia", "online"}
SENSITIVE_KEY_RE = re.compile(
    r"(?:password|passwd|secret|token|credential|private[_-]?key|auth[_-]?key|api[_-]?key|authorization|cookie|session)",
    re.IGNORECASE,
)
URL_KEY_RE = re.compile(r"(?:url|uri|dsn|endpoint)", re.IGNORECASE)
URL_WITH_CREDENTIALS_RE = re.compile(r"\b(?:rtsp|rtsps|http|https)://[^\s/@:]+:[^\s/@]+@", re.IGNORECASE)
URL_TOKEN_RE = re.compile(r"\b(?:rtsp|rtsps|http|https)://[^\s|,\"']+", re.IGNORECASE)
ENV_PREFIXES = (
    "ANALYTIC_",
    "AUX_",
    "BOX_",
    "CAMERA_",
    "DETECTOR_",
    "EVENT_",
    "FRAME_",
    "GATEWAY_",
    "IA2_",
    "IA3_",
    "INFERENCE_",
    "MEDIA_",
    "MOTION_",
    "OPERATIONAL_",
    "RESOURCE_",
    "RUNTIME_",
    "VISUAL_",
    "WATCHDOG_",
    "WEBRTC_",
)
COUNTER_FIELDS = (
    "capture_queue_dropped_frames",
    "dropped_frames_count",
    "event_persistence_events_failed",
    "event_persistence_dropped_or_rejected_jobs",
    "inference_pool_failed",
    "inference_pool_timed_out",
    "inference_pool_rejected",
    "inference_pool_dropped_oldest",
    "inference_pool_stale_dropped",
    "inference_transport_errors_total",
    "frame_transport_errors_total",
    "visual_jobs_dropped",
)
DROP_GATE_FIELDS = {
    "inference_pool_failed",
    "inference_pool_timed_out",
    "inference_pool_rejected",
    "inference_pool_dropped_oldest",
    "inference_pool_stale_dropped",
    "inference_transport_errors_total",
}
COMPACT_METRIC_FIELDS = {
    "updated_at", "health_status", "worker_pid", "worker_generation", "worker_mode",
    "capture_source", "fps", "raw_fps", "processed_fps", "read_ms", "infer_ms",
    "plot_ms", "jpeg_ms", "loop_ms", "process_cpu_percent", "system_cpu_percent",
    "process_rss_mb", "system_ram_percent", "frame_width", "frame_height",
    "source_frame_width", "source_frame_height", "reconnect_count", "dropped_frames_count",
    "last_successful_inference_at", "last_frame_at", "last_processed_frame_at",
    "last_metrics_at", "consecutive_stall_checks", "capture_queue_dropped_frames",
    "event_persistence_queue_size", "event_persistence_events_failed",
    "event_persistence_dropped_or_rejected_jobs", "visual_queue_size", "visual_jobs_dropped",
    "inference_result_age_ms", "visual_tracks_stale", "inference_pool_id",
    "inference_pool_queue_size", "inference_pool_submitted", "inference_pool_completed",
    "inference_pool_failed", "inference_pool_timed_out", "inference_pool_rejected",
    "inference_pool_dropped_oldest", "inference_pool_stale_dropped",
    "inference_pool_last_wait_ms", "inference_pool_last_total_latency_ms",
    "inference_pool_last_infer_ms", "inference_transport_latency_ms",
    "inference_transport_errors_total", "frame_transport_errors_total",
    "shared_buffer_frame_age_ms", "shared_buffer_read_latency_ms",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


def parse_csv_ints(value: str | None) -> list[int]:
    result: list[int] = []
    for item in str(value or "").split(","):
        item = item.strip()
        if not item:
            continue
        number = int(item)
        if number <= 0:
            raise ValueError(f"valor deve ser positivo: {number}")
        if number not in result:
            result.append(number)
    return result


def percentile(values: Iterable[float], percent: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * max(0.0, min(100.0, percent)) / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 3)


def safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return URL_WITH_CREDENTIALS_RE.sub(lambda match: match.group(0).split("://", 1)[0] + "://<redacted>@", value)
    if not parsed.scheme or not parsed.netloc:
        return URL_WITH_CREDENTIALS_RE.sub(lambda match: match.group(0).split("://", 1)[0] + "://<redacted>@", value)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    userinfo = "<redacted>@" if parsed.username is not None or parsed.password is not None else ""
    return urlunsplit((parsed.scheme, f"{userinfo}{host}{port}", parsed.path, "", ""))


def sanitize(value: Any, key: str = "") -> Any:
    if SENSITIVE_KEY_RE.search(str(key)):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): sanitize(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [sanitize(item, key) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item, key) for item in value]
    if isinstance(value, str):
        if URL_KEY_RE.search(str(key)) or "://" in value:
            if value.strip().lower().startswith(("rtsp://", "rtsps://", "http://", "https://")) and not any(
                separator in value.strip() for separator in (" ", "|", '"', "'")
            ):
                return redact_url(value)
            return URL_TOKEN_RE.sub(lambda match: redact_url(match.group(0)), value)
        return URL_WITH_CREDENTIALS_RE.sub(lambda match: match.group(0).split("://", 1)[0] + "://<redacted>@", value)
    return value


def append_jsonl(handle, payload: dict[str, Any]) -> None:
    handle.write(json.dumps(sanitize(payload), ensure_ascii=False, default=str) + "\n")
    handle.flush()


def run_command(command: list[str], *, cwd: Path, timeout: float = 15.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
        }


def http_json(
    method: str,
    base_url: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    base = str(base_url).rstrip("/")
    query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
    url = f"{base}/{str(path).lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    request = Request(url, method=method.upper(), headers={"Accept": "application/json", **(headers or {})})
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = int(getattr(response, "status", 200) or 200)
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"raw": raw[:1000]}
        return {
            "ok": 200 <= status < 400,
            "status_code": status,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "data": sanitize(data),
        }
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
        except Exception:
            body = ""
        return {
            "ok": False,
            "status_code": exc.code,
            "error": f"HTTPError: {exc.reason}",
            "body": sanitize(body),
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}",
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
        }


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith(ENV_PREFIXES):
            values[key] = sanitize(value.strip(), key)
    return dict(sorted(values.items()))


def parse_json_lines(text: str) -> list[Any]:
    result: list[Any] = []
    stripped = str(text or "").strip()
    if not stripped:
        return result
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        pass
    for line in stripped.splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def parse_percent(value: Any) -> float | None:
    return safe_float(str(value or "").strip().rstrip("%"))


class IncrementalFileMonitor:
    def __init__(self, roots: list[Path], patterns: tuple[str, ...]):
        self.roots = roots
        self.patterns = patterns
        self.positions: dict[str, int] = {}
        for path in self._discover():
            try:
                self.positions[str(path)] = path.stat().st_size
            except OSError:
                continue

    def _discover(self) -> list[Path]:
        found: set[Path] = set()
        for root in self.roots:
            if not root.exists():
                continue
            for pattern in self.patterns:
                found.update(path for path in root.rglob(pattern) if path.is_file())
        return sorted(found)

    def poll(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in self._discover():
            key = str(path)
            try:
                size = path.stat().st_size
                previous = self.positions.get(key, 0)
                if size < previous:
                    previous = 0
                if size == previous:
                    self.positions[key] = size
                    continue
                with path.open("rb") as handle:
                    handle.seek(previous)
                    data = handle.read()
                self.positions[key] = size
            except OSError:
                continue
            for raw in data.decode("utf-8", errors="replace").splitlines():
                if raw.strip():
                    records.append({"source": key, "line": sanitize(raw)})
        return records


def log_level(line: str) -> str:
    match = re.search(r"\b(CRITICAL|ERROR|WARNING|WARN|INFO|DEBUG)\b", line, re.IGNORECASE)
    if not match:
        return "unknown"
    level = match.group(1).lower()
    return "warning" if level == "warn" else level


def log_category(line: str) -> str:
    text = line.lower()
    checks = (
        ("oom", ("out of memory", "oom", "cuda out of memory")),
        ("inference", ("inference_failed", "infer timeout", "central inference failed")),
        ("backpressure", ("queue_full", "queue full", "dropped_oldest", "stale_dropped")),
        ("watchdog", ("watchdog", "stale_worker")),
        ("gateway", ("gateway", "ffmpeg")),
        ("rtsp", ("rtsp", "no_frame", "frame_missing")),
        ("restart", ("restart", "reconnecting")),
    )
    for category, tokens in checks:
        if any(token in text for token in tokens):
            return category
    return "other"


@dataclass
class Thresholds:
    min_collector_coverage_percent: float = 99.0
    min_endpoint_success_percent: float = 99.0
    min_camera_availability_percent: float = 99.0
    min_canary_success_percent: float = 99.0
    max_canary_p95_ms: float = 2000.0
    max_host_cpu_p95_percent: float = 80.0
    max_host_ram_p95_percent: float = 80.0
    max_gpu_memory_p95_percent: float = 80.0
    max_drop_rate_percent: float = 0.1


@dataclass
class StageStats:
    name: str
    planned_seconds: float
    interval_seconds: float
    expected_camera_ids: set[int]
    target_camera_count: int | None
    thresholds: Thresholds
    started_at: str = field(default_factory=utc_text)
    ended_at: str | None = None
    samples: int = 0
    endpoint_attempts: Counter[str] = field(default_factory=Counter)
    endpoint_successes: Counter[str] = field(default_factory=Counter)
    camera_attempts: Counter[int] = field(default_factory=Counter)
    camera_healthy: Counter[int] = field(default_factory=Counter)
    camera_states: dict[int, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    camera_restart_previous: dict[int, float] = field(default_factory=dict)
    camera_restart_delta: Counter[int] = field(default_factory=Counter)
    previous_camera_healthy: dict[int, bool] = field(default_factory=dict)
    incident_started: dict[int, float] = field(default_factory=dict)
    incident_durations: dict[int, list[float]] = field(default_factory=lambda: defaultdict(list))
    open_incidents: set[int] = field(default_factory=set)
    host_cpu: list[float] = field(default_factory=list)
    host_ram: list[float] = field(default_factory=list)
    gpu_util: list[float] = field(default_factory=list)
    gpu_memory_percent: list[float] = field(default_factory=list)
    inference_ms: list[float] = field(default_factory=list)
    canary_attempts: int = 0
    canary_successes: int = 0
    canary_ms: list[float] = field(default_factory=list)
    log_levels: Counter[str] = field(default_factory=Counter)
    log_categories: Counter[str] = field(default_factory=Counter)
    counter_previous: dict[tuple[int, str], float] = field(default_factory=dict)
    counter_deltas: Counter[str] = field(default_factory=Counter)
    submitted_previous: dict[int, float] = field(default_factory=dict)
    submitted_delta: float = 0.0
    docker_initial_restarts: dict[str, int] = field(default_factory=dict)
    docker_final_restarts: dict[str, int] = field(default_factory=dict)
    docker_initial_ids: dict[str, str] = field(default_factory=dict)
    docker_final_ids: dict[str, str] = field(default_factory=dict)
    docker_oom: set[str] = field(default_factory=set)
    runtime_pid_initial: int | None = None
    runtime_pid_final: int | None = None
    gateway_instance_initial: str | None = None
    gateway_instance_final: str | None = None
    max_observed_running: int = 0

    def add(self, sample: dict[str, Any], log_records: list[dict[str, Any]]) -> None:
        self.samples += 1
        now_monotonic = time.monotonic()
        endpoints = sample.get("endpoints") or {}
        for name, result in endpoints.items():
            if result is None:
                continue
            self.endpoint_attempts[name] += 1
            if isinstance(result, dict) and result.get("ok"):
                self.endpoint_successes[name] += 1
        runtime_live = ((endpoints.get("runtime_live") or {}).get("data") or {})
        try:
            runtime_pid = int(runtime_live.get("pid"))
        except (TypeError, ValueError):
            runtime_pid = None
        if runtime_pid is not None:
            if self.runtime_pid_initial is None:
                self.runtime_pid_initial = runtime_pid
            self.runtime_pid_final = runtime_pid
        gateway_health = ((endpoints.get("gateway_health") or {}).get("data") or {})
        gateway_instance = str(gateway_health.get("gateway_instance_id") or "").strip() or None
        if gateway_instance is not None:
            if self.gateway_instance_initial is None:
                self.gateway_instance_initial = gateway_instance
            self.gateway_instance_final = gateway_instance

        runtime_data = ((endpoints.get("runtime_cameras") or {}).get("data") or {})
        cameras = runtime_data.get("cameras") or []
        by_id: dict[int, dict[str, Any]] = {}
        for camera in cameras:
            try:
                by_id[int(camera.get("camera_id") or camera.get("id"))] = camera
            except (TypeError, ValueError):
                continue
        self.max_observed_running = max(
            self.max_observed_running,
            sum(1 for camera in cameras if str(camera.get("health_status") or "").lower() in HEALTHY_CAMERA_STATES),
        )
        for camera_id in self.expected_camera_ids:
            camera = by_id.get(camera_id)
            state = str((camera or {}).get("health_status") or "missing").strip().lower()
            healthy = state in HEALTHY_CAMERA_STATES and bool((camera or {}).get("is_running", True))
            self.camera_attempts[camera_id] += 1
            self.camera_states[camera_id][state] += 1
            if healthy:
                self.camera_healthy[camera_id] += 1
            restart_count = safe_float((camera or {}).get("restart_count"))
            if restart_count is not None:
                previous_restart = self.camera_restart_previous.get(camera_id)
                if previous_restart is not None:
                    self.camera_restart_delta[camera_id] += (
                        max(0.0, restart_count - previous_restart)
                        if restart_count >= previous_restart
                        else max(0.0, restart_count)
                    )
                self.camera_restart_previous[camera_id] = restart_count
            previous = self.previous_camera_healthy.get(camera_id)
            if previous is None and not healthy:
                self.incident_started[camera_id] = now_monotonic
                self.open_incidents.add(camera_id)
            elif previous is True and not healthy:
                self.incident_started[camera_id] = now_monotonic
                self.open_incidents.add(camera_id)
            elif previous is False and healthy and camera_id in self.incident_started:
                self.incident_durations[camera_id].append(now_monotonic - self.incident_started.pop(camera_id))
                self.open_incidents.discard(camera_id)
            self.previous_camera_healthy[camera_id] = healthy

        host = sample.get("host") or {}
        for values, key in ((self.host_cpu, "cpu_percent"), (self.host_ram, "ram_percent")):
            number = safe_float(host.get(key))
            if number is not None:
                values.append(number)
        for gpu in sample.get("gpu") or []:
            utilization = safe_float(gpu.get("utilization_gpu_percent"))
            used = safe_float(gpu.get("memory_used_mb"))
            total = safe_float(gpu.get("memory_total_mb"))
            if utilization is not None:
                self.gpu_util.append(utilization)
            if used is not None and total and total > 0:
                self.gpu_memory_percent.append(used / total * 100.0)

        canary = sample.get("canary")
        if isinstance(canary, dict):
            self.canary_attempts += 1
            payload = canary.get("data") or {}
            if canary.get("ok") and payload.get("ok", True):
                self.canary_successes += 1
            duration = safe_float(payload.get("total_ms")) or safe_float(canary.get("duration_ms"))
            if duration is not None:
                self.canary_ms.append(duration)

        worker_metrics = sample.get("worker_metrics") or {}
        for raw_camera_id, metrics in worker_metrics.items():
            try:
                camera_id = int(raw_camera_id)
            except (TypeError, ValueError):
                continue
            infer_ms = safe_float((metrics or {}).get("infer_ms"))
            if infer_ms is not None:
                self.inference_ms.append(infer_ms)
            for field_name in COUNTER_FIELDS:
                current = safe_float((metrics or {}).get(field_name))
                if current is None:
                    continue
                counter_key = (camera_id, field_name)
                previous = self.counter_previous.get(counter_key)
                if previous is not None:
                    self.counter_deltas[field_name] += max(0.0, current - previous) if current >= previous else max(0.0, current)
                self.counter_previous[counter_key] = current
            submitted = safe_float((metrics or {}).get("inference_pool_submitted"))
            if submitted is not None:
                previous = self.submitted_previous.get(camera_id)
                if previous is not None:
                    self.submitted_delta += max(0.0, submitted - previous) if submitted >= previous else max(0.0, submitted)
                self.submitted_previous[camera_id] = submitted

        docker_inspect = ((sample.get("docker") or {}).get("inspect") or [])
        current_restarts: dict[str, int] = {}
        current_ids: dict[str, str] = {}
        for item in docker_inspect:
            name = str(item.get("Name") or "").lstrip("/")
            if not name:
                continue
            restarts = int(item.get("RestartCount") or 0)
            current_restarts[name] = restarts
            current_ids[name] = str(item.get("Id") or "")
            state = item.get("State") or {}
            if bool(state.get("OOMKilled")):
                self.docker_oom.add(name)
        if not self.docker_initial_restarts:
            self.docker_initial_restarts = dict(current_restarts)
            self.docker_initial_ids = dict(current_ids)
        self.docker_final_restarts = dict(current_restarts)
        self.docker_final_ids = dict(current_ids)

        for record in log_records:
            line = str(record.get("line") or "")
            self.log_levels[log_level(line)] += 1
            self.log_categories[log_category(line)] += 1

    def finish(self) -> dict[str, Any]:
        self.ended_at = utc_text()
        expected_samples = max(1, math.ceil(max(0.0, self.planned_seconds) / max(0.1, self.interval_seconds)))
        coverage = min(100.0, self.samples / expected_samples * 100.0)
        endpoints: dict[str, dict[str, Any]] = {}
        for name, attempts in sorted(self.endpoint_attempts.items()):
            successes = self.endpoint_successes[name]
            endpoints[name] = {
                "attempts": attempts,
                "successes": successes,
                "success_percent": round(successes / attempts * 100.0, 3) if attempts else None,
            }
        cameras: list[dict[str, Any]] = []
        for camera_id in sorted(self.expected_camera_ids):
            attempts = self.camera_attempts[camera_id]
            healthy = self.camera_healthy[camera_id]
            durations = self.incident_durations[camera_id]
            cameras.append({
                "camera_id": camera_id,
                "samples": attempts,
                "healthy_samples": healthy,
                "availability_percent": round(healthy / attempts * 100.0, 3) if attempts else 0.0,
                "states": dict(sorted(self.camera_states[camera_id].items())),
                "recovered_incidents": len(durations),
                "mttr_p95_seconds": percentile(durations, 95),
                "incident_open_at_end": camera_id in self.open_incidents,
            })
        camera_total = sum(item["samples"] for item in cameras)
        camera_healthy = sum(item["healthy_samples"] for item in cameras)
        camera_availability = round(camera_healthy / camera_total * 100.0, 3) if camera_total else None
        canary_success = (
            round(self.canary_successes / self.canary_attempts * 100.0, 3)
            if self.canary_attempts
            else None
        )
        dropped = sum(float(self.counter_deltas.get(key, 0.0)) for key in DROP_GATE_FIELDS)
        drop_rate = round(dropped / self.submitted_delta * 100.0, 5) if self.submitted_delta > 0 else (0.0 if dropped == 0 else None)
        restart_delta = {
            name: max(0, self.docker_final_restarts.get(name, 0) - initial)
            for name, initial in self.docker_initial_restarts.items()
            if self.docker_final_restarts.get(name, 0) > initial
        }
        recreated_containers = sorted(
            name
            for name, initial_id in self.docker_initial_ids.items()
            if initial_id and self.docker_final_ids.get(name) and self.docker_final_ids[name] != initial_id
        )
        resource = {
            "host_cpu_p50_percent": percentile(self.host_cpu, 50),
            "host_cpu_p95_percent": percentile(self.host_cpu, 95),
            "host_ram_p95_percent": percentile(self.host_ram, 95),
            "gpu_util_p50_percent": percentile(self.gpu_util, 50),
            "gpu_util_p95_percent": percentile(self.gpu_util, 95),
            "gpu_memory_p95_percent": percentile(self.gpu_memory_percent, 95),
            "inference_p50_ms": percentile(self.inference_ms, 50),
            "inference_p95_ms": percentile(self.inference_ms, 95),
            "canary_p95_ms": percentile(self.canary_ms, 95),
        }
        failures: list[str] = []
        if coverage < self.thresholds.min_collector_coverage_percent:
            failures.append("collector_coverage")
        required_endpoints = ("runtime_ready", "runtime_cameras", "gateway_health", "gateway_cameras")
        for name in required_endpoints:
            metric = endpoints.get(name)
            if not metric or metric.get("success_percent") is None or metric["success_percent"] < self.thresholds.min_endpoint_success_percent:
                failures.append(f"endpoint:{name}")
        if not self.expected_camera_ids:
            failures.append("no_expected_cameras")
        elif camera_availability is None or camera_availability < self.thresholds.min_camera_availability_percent:
            failures.append("camera_availability")
        if self.target_camera_count is not None and self.max_observed_running < self.target_camera_count:
            failures.append("target_camera_count_not_reached")
        if self.canary_attempts == 0:
            failures.append("canary_not_sampled")
        elif canary_success is None or canary_success < self.thresholds.min_canary_success_percent:
            failures.append("canary_success")
        if resource["canary_p95_ms"] is not None and resource["canary_p95_ms"] > self.thresholds.max_canary_p95_ms:
            failures.append("canary_latency")
        if resource["host_cpu_p95_percent"] is not None and resource["host_cpu_p95_percent"] > self.thresholds.max_host_cpu_p95_percent:
            failures.append("host_cpu")
        if resource["host_ram_p95_percent"] is not None and resource["host_ram_p95_percent"] > self.thresholds.max_host_ram_p95_percent:
            failures.append("host_ram")
        if resource["gpu_memory_p95_percent"] is not None and resource["gpu_memory_p95_percent"] > self.thresholds.max_gpu_memory_p95_percent:
            failures.append("gpu_memory")
        if drop_rate is None or drop_rate > self.thresholds.max_drop_rate_percent:
            failures.append("drop_rate")
        if restart_delta:
            failures.append("container_restarts")
        if recreated_containers:
            failures.append("container_recreated")
        if (
            self.runtime_pid_initial is not None
            and self.runtime_pid_final is not None
            and self.runtime_pid_initial != self.runtime_pid_final
        ):
            failures.append("runtime_process_restarted")
        if (
            self.gateway_instance_initial
            and self.gateway_instance_final
            and self.gateway_instance_initial != self.gateway_instance_final
        ):
            failures.append("gateway_process_restarted")
        if self.docker_oom:
            failures.append("container_oom")
        if any(value > 0 for value in self.camera_restart_delta.values()):
            failures.append("camera_worker_restarts")
        if self.open_incidents:
            failures.append("open_camera_incident")
        return {
            "stage": self.name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "planned_seconds": self.planned_seconds,
            "interval_seconds": self.interval_seconds,
            "samples": self.samples,
            "expected_samples": expected_samples,
            "collector_coverage_percent": round(coverage, 3),
            "target_camera_count": self.target_camera_count,
            "max_observed_running": self.max_observed_running,
            "camera_availability_percent": camera_availability,
            "canary": {
                "attempts": self.canary_attempts,
                "successes": self.canary_successes,
                "success_percent": canary_success,
            },
            "endpoints": endpoints,
            "cameras": cameras,
            "resources": resource,
            "counter_deltas": {key: round(float(value), 3) for key, value in sorted(self.counter_deltas.items())},
            "inference_submitted_delta": round(self.submitted_delta, 3),
            "drop_rate_percent": drop_rate,
            "container_restart_delta": restart_delta,
            "containers_recreated": recreated_containers,
            "containers_oom_killed": sorted(self.docker_oom),
            "runtime_pid": {"initial": self.runtime_pid_initial, "final": self.runtime_pid_final},
            "gateway_instance_id": {"initial": self.gateway_instance_initial, "final": self.gateway_instance_final},
            "camera_worker_restart_delta": {
                str(key): int(value) for key, value in sorted(self.camera_restart_delta.items()) if value > 0
            },
            "log_levels": dict(sorted(self.log_levels.items())),
            "log_categories": dict(sorted(self.log_categories.items())),
            "thresholds": self.thresholds.__dict__,
            "passed": not failures,
            "failures": failures,
        }


class CampaignCollector:
    def __init__(self, args: argparse.Namespace, run_dir: Path):
        self.args = args
        self.project_root = Path(args.project_root).resolve()
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.samples_handle = (run_dir / "telemetry.jsonl").open("a", encoding="utf-8", buffering=1)
        self.logs_handle = (run_dir / "logs.jsonl").open("a", encoding="utf-8", buffering=1)
        self.native_handle = (run_dir / "native_history.jsonl").open("a", encoding="utf-8", buffering=1)
        log_roots = [self.project_root / "logs", self.project_root / "data" / "logs"]
        history_roots = [
            self.project_root / "runtime_state" / "operational_history",
            self.project_root / "runtime_state" / "resource_history",
            self.project_root / "data" / "runtime_state" / "operational_history",
            self.project_root / "data" / "runtime_state" / "resource_history",
        ]
        self.log_monitor = IncrementalFileMonitor(log_roots, ("*.log*", "startup_error.log"))
        self.history_monitor = IncrementalFileMonitor(history_roots, ("*.jsonl",))
        self.last_canary_at = 0.0
        self.last_detail_at = 0.0
        self.last_container_log_at = utc_now()
        self.headers: dict[str, str] = {}
        token = str(os.environ.get(args.supervisor_token_env, "") or "").strip()
        if token:
            self.headers["X-Analitico-Supervisor-Token"] = token

    def close(self) -> None:
        for handle in (self.samples_handle, self.logs_handle, self.native_handle):
            try:
                handle.close()
            except Exception:
                pass

    def manifest(self) -> dict[str, Any]:
        versions = {
            "python": sys.version,
            "platform": sys.platform,
            "hostname": socket.gethostname(),
            "docker": run_command(["docker", "version", "--format", "{{json .}}"], cwd=self.project_root),
            "docker_compose": run_command(["docker", "compose", "version", "--short"], cwd=self.project_root),
            "nvidia_smi": run_command(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"], cwd=self.project_root),
        }
        git = {
            "commit": run_command(["git", "rev-parse", "HEAD"], cwd=self.project_root),
            "branch": run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.project_root),
            "status": run_command(["git", "status", "--short"], cwd=self.project_root),
        }
        tracked_files: dict[str, Any] = {}
        for relative in ("docker-compose.yml", "Dockerfile", "Dockerfile.gpu", "requirements.txt"):
            path = self.project_root / relative
            if path.exists():
                stat = path.stat()
                tracked_files[relative] = {"size": stat.st_size, "mtime_utc": utc_text(datetime.fromtimestamp(stat.st_mtime, timezone.utc))}
        model_files: dict[str, Any] = {}
        model_candidates = list((self.project_root / "models").rglob("*")) if (self.project_root / "models").exists() else []
        root_best = self.project_root / "best.pt"
        if root_best.exists():
            model_candidates.append(root_best)
        for path in model_candidates:
            if not path.is_file() or path.suffix.lower() not in {".pt", ".onnx", ".engine"}:
                continue
            stat = path.stat()
            relative = str(path.relative_to(self.project_root))
            model_files[relative] = {
                "size": stat.st_size,
                "mtime_utc": utc_text(datetime.fromtimestamp(stat.st_mtime, timezone.utc)),
            }
        payload = {
            "generated_at": utc_text(),
            "command": [sanitize(value) for value in sys.argv],
            "project_root": str(self.project_root),
            "run_dir": str(self.run_dir),
            "parameters": parse_env_file(self.project_root / ".env.docker"),
            "versions": versions,
            "git": git,
            "tracked_files": tracked_files,
            "model_files": model_files,
        }
        (self.run_dir / "manifest.json").write_text(
            json.dumps(sanitize(payload), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return payload

    def _host_snapshot(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        try:
            import psutil  # type: ignore

            payload.update({
                "cpu_percent": psutil.cpu_percent(interval=None),
                "ram_percent": psutil.virtual_memory().percent,
                "ram_available_mb": round(psutil.virtual_memory().available / 1024 / 1024, 2),
                "boot_time": utc_text(datetime.fromtimestamp(psutil.boot_time(), timezone.utc)),
            })
        except Exception as exc:
            payload["psutil_error"] = f"{type(exc).__name__}: {exc}"
        for name, path in (("project", self.project_root), ("data", self.project_root / "data")):
            try:
                usage = shutil.disk_usage(path)
                payload[f"{name}_disk"] = {
                    "total_gb": round(usage.total / 1024**3, 3),
                    "used_gb": round(usage.used / 1024**3, 3),
                    "free_gb": round(usage.free / 1024**3, 3),
                    "used_percent": round(usage.used / usage.total * 100.0, 3) if usage.total else None,
                }
            except OSError:
                continue
        return payload

    def _gpu_snapshot(self) -> list[dict[str, Any]]:
        fields = (
            "index,name,driver_version,utilization.gpu,utilization.memory,"
            "memory.used,memory.total,temperature.gpu,power.draw,power.limit"
        )
        result = run_command(
            ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
            cwd=self.project_root,
        )
        if not result.get("ok"):
            return []
        rows: list[dict[str, Any]] = []
        names = (
            "index",
            "name",
            "driver_version",
            "utilization_gpu_percent",
            "utilization_memory_percent",
            "memory_used_mb",
            "memory_total_mb",
            "temperature_c",
            "power_draw_w",
            "power_limit_w",
        )
        for line in str(result.get("stdout") or "").splitlines():
            values = [item.strip() for item in line.split(",")]
            if len(values) == len(names):
                rows.append(dict(zip(names, values)))
        return rows

    def _docker_snapshot(self) -> dict[str, Any]:
        containers = list(self.args.containers)
        stats_result = run_command(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", *containers],
            cwd=self.project_root,
            timeout=20.0,
        )
        inspect_result = run_command(["docker", "inspect", *containers], cwd=self.project_root, timeout=20.0)
        stats = parse_json_lines(str(stats_result.get("stdout") or "")) if stats_result.get("ok") else []
        inspect = parse_json_lines(str(inspect_result.get("stdout") or "")) if inspect_result.get("ok") else []
        compact_inspect: list[dict[str, Any]] = []
        for item in inspect:
            if not isinstance(item, dict):
                continue
            state = item.get("State") or {}
            config = item.get("Config") or {}
            compact_inspect.append({
                "Id": str(item.get("Id") or "")[:16],
                "Name": item.get("Name"),
                "Image": config.get("Image"),
                "RestartCount": item.get("RestartCount"),
                "State": {
                    "Status": state.get("Status"),
                    "Running": state.get("Running"),
                    "Paused": state.get("Paused"),
                    "Restarting": state.get("Restarting"),
                    "OOMKilled": state.get("OOMKilled"),
                    "Dead": state.get("Dead"),
                    "Pid": state.get("Pid"),
                    "ExitCode": state.get("ExitCode"),
                    "Error": state.get("Error"),
                    "StartedAt": state.get("StartedAt"),
                    "FinishedAt": state.get("FinishedAt"),
                    "Health": state.get("Health"),
                },
            })
        return {
            "stats_ok": bool(stats_result.get("ok")),
            "stats_error": stats_result.get("error") or stats_result.get("stderr"),
            "stats": stats,
            "inspect_ok": bool(inspect_result.get("ok")),
            "inspect_error": inspect_result.get("error") or inspect_result.get("stderr"),
            "inspect": compact_inspect,
        }

    def _worker_metrics(self, *, detail: bool) -> dict[str, Any]:
        root = self.project_root / "data" / "runtime_state" / "metrics"
        if not root.exists():
            root = self.project_root / "runtime_state" / "metrics"
        newest: dict[int, Path] = {}
        for path in root.glob("camera_*.json") if root.exists() else []:
            match = re.match(r"camera_(\d+)(?:_\d+)?\.json$", path.name)
            if not match or path.name.endswith(".tmp.json"):
                continue
            camera_id = int(match.group(1))
            current = newest.get(camera_id)
            try:
                if current is None or path.stat().st_mtime_ns > current.stat().st_mtime_ns:
                    newest[camera_id] = path
            except OSError:
                continue
        result: dict[str, Any] = {}
        stale: dict[str, float] = {}
        for camera_id, path in newest.items():
            try:
                age_seconds = max(0.0, time.time() - path.stat().st_mtime)
                if age_seconds > max(300.0, self.args.detail_interval_seconds * 5.0):
                    stale[str(camera_id)] = round(age_seconds, 2)
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    continue
                payload.pop("latest_tracks", None)
                if not detail:
                    payload = {key: value for key, value in payload.items() if key in COMPACT_METRIC_FIELDS}
                result[str(camera_id)] = sanitize(payload)
            except (OSError, json.JSONDecodeError):
                continue
        result["_inventory"] = {
            "detail_sample": detail,
            "fresh_camera_ids": sorted(int(key) for key in result if key.isdigit()),
            "stale_camera_age_seconds": stale,
        }
        return result

    def _container_logs(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        since = utc_text(self.last_container_log_at)
        self.last_container_log_at = utc_now()
        def read_container(container: str) -> tuple[str, dict[str, Any]]:
            return container, run_command(
                    ["docker", "logs", "--timestamps", "--since", since, container],
                    cwd=self.project_root,
                    timeout=10.0,
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(self.args.containers))) as executor:
            results = list(executor.map(read_container, self.args.containers))
        for container, result in results:
            for stream in ("stdout", "stderr"):
                for line in str(result.get(stream) or "").splitlines():
                    if line.strip():
                        records.append({"source": f"docker:{container}", "line": sanitize(line)})
        return records

    def collect_once(self, stage: str, target_camera_count: int | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        monotonic_now = time.monotonic()
        jobs = {
            "web_health": lambda: http_json("GET", self.args.web_url, "/api/health"),
            "runtime_live": lambda: http_json("GET", self.args.runtime_url, "/internal/health/live"),
            "runtime_ready": lambda: http_json("GET", self.args.runtime_url, "/internal/health/ready"),
            "runtime_cameras": lambda: http_json("GET", self.args.runtime_url, "/internal/health/cameras", timeout=12.0),
            "supervisor_snapshot": lambda: http_json(
                "GET", self.args.runtime_url, "/internal/supervisor/snapshot", headers=self.headers, timeout=15.0
            ),
            "gateway_health": lambda: http_json("GET", self.args.gateway_url, "/healthz"),
            "gateway_cameras": lambda: http_json("GET", self.args.gateway_url, "/cameras", timeout=12.0),
        }
        run_canary = monotonic_now - self.last_canary_at >= self.args.canary_interval_seconds
        if run_canary:
            self.last_canary_at = monotonic_now
            jobs["_canary"] = lambda: http_json(
                "POST", self.args.runtime_url, "/internal/supervisor/canary", headers=self.headers, timeout=15.0
            )
        collect_detail = monotonic_now - self.last_detail_at >= self.args.detail_interval_seconds
        if collect_detail:
            self.last_detail_at = monotonic_now
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs) + 4) as executor:
            futures = {name: executor.submit(job) for name, job in jobs.items()}
            system_futures = {
                "host": executor.submit(self._host_snapshot),
                "gpu": executor.submit(self._gpu_snapshot),
                "docker": executor.submit(self._docker_snapshot),
                "worker_metrics": executor.submit(self._worker_metrics, detail=collect_detail),
            }
            results = {name: future.result() for name, future in futures.items()}
            system_results = {name: future.result() for name, future in system_futures.items()}
        canary = results.pop("_canary", None)
        endpoints = results
        log_records = self.log_monitor.poll()
        log_records.extend(self._container_logs())
        for record in log_records:
            append_jsonl(self.logs_handle, {"ts": utc_text(), "stage": stage, **record})
        for record in self.history_monitor.poll():
            parsed: Any = record["line"]
            try:
                parsed = json.loads(str(record["line"]))
            except (TypeError, json.JSONDecodeError):
                pass
            append_jsonl(
                self.native_handle,
                {"ts": utc_text(), "stage": stage, "source": record["source"], "payload": parsed},
            )
        sample = {
            "ts": utc_text(),
            "stage": stage,
            "target_camera_count": target_camera_count,
            "endpoints": endpoints,
            "canary": canary,
            "host": system_results["host"],
            "gpu": system_results["gpu"],
            "docker": system_results["docker"],
            "worker_metrics": system_results["worker_metrics"],
            "log_records_collected": len(log_records),
        }
        append_jsonl(self.samples_handle, sample)
        return sample, log_records

    def camera_control(self, camera_id: int, action: str) -> dict[str, Any]:
        if action == "start":
            return http_json(
                "POST",
                self.args.runtime_url,
                f"/internal/cameras/{camera_id}/start",
                params={"use_motion_test": "true", "restart_existing": "false"},
                headers=self.headers,
                timeout=30.0,
            )
        return http_json(
            "POST",
            self.args.runtime_url,
            f"/internal/cameras/{camera_id}/stop",
            params={"timeout_seconds": 8},
            headers=self.headers,
            timeout=15.0,
        )


def discover_expected_camera_ids(sample: dict[str, Any]) -> set[int]:
    endpoints = sample.get("endpoints") or {}
    supervisor = ((endpoints.get("supervisor_snapshot") or {}).get("data") or {})
    result: set[int] = set()
    for camera in supervisor.get("cameras") or []:
        if camera.get("desired_running"):
            try:
                result.add(int(camera.get("camera_id")))
            except (TypeError, ValueError):
                pass
    if result:
        return result
    runtime = ((endpoints.get("runtime_cameras") or {}).get("data") or {})
    for camera in runtime.get("cameras") or []:
        if camera.get("is_running") or str(camera.get("health_status") or "").lower() in {
            "starting", "warming_up", "degraded", "reconnecting", *HEALTHY_CAMERA_STATES
        }:
            try:
                result.add(int(camera.get("camera_id") or camera.get("id")))
            except (TypeError, ValueError):
                pass
    return result


def validate_control_preflight(sample: dict[str, Any]) -> list[str]:
    endpoints = sample.get("endpoints") or {}
    failures: list[str] = []
    for name in ("runtime_ready", "runtime_cameras", "supervisor_snapshot", "gateway_health", "gateway_cameras"):
        if not bool((endpoints.get(name) or {}).get("ok")):
            failures.append(name)
    canary = sample.get("canary") or {}
    canary_payload = canary.get("data") or {}
    if not canary.get("ok") or not canary_payload.get("ok", True):
        failures.append("inference_canary")
    return failures


def write_stage_report(run_dir: Path, summary: dict[str, Any]) -> tuple[Path, Path, Path]:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(summary["stage"])).strip("_") or "stage"
    json_path = run_dir / f"stage_{slug}.json"
    md_path = run_dir / f"stage_{slug}.md"
    csv_path = run_dir / f"stage_{slug}_cameras.csv"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    resources = summary.get("resources") or {}
    outcome = "INFORMATIVO" if summary.get("informational") else ("APROVADO" if summary["passed"] else "REPROVADO")
    lines = [
        f"# Campanha de estabilidade - {summary['stage']}",
        "",
        f"Resultado: **{outcome}**",
        "",
        f"- Inicio: `{summary['started_at']}`",
        f"- Fim: `{summary['ended_at']}`",
        f"- Cobertura do coletor: `{summary['collector_coverage_percent']}%`",
        f"- Disponibilidade das cameras esperadas: `{summary['camera_availability_percent']}%`",
        f"- Maximo de cameras rodando observado: `{summary['max_observed_running']}`",
        f"- Sucesso do canario: `{summary['canary']['success_percent']}%`",
        f"- Latencia p95 do canario: `{resources.get('canary_p95_ms')}` ms",
        f"- CPU host p95: `{resources.get('host_cpu_p95_percent')}`%",
        f"- RAM host p95: `{resources.get('host_ram_p95_percent')}`%",
        f"- VRAM p95: `{resources.get('gpu_memory_p95_percent')}`%",
        f"- Taxa de descartes/falhas: `{summary.get('drop_rate_percent')}`%",
        f"- Falhas de gate: `{', '.join(summary['failures']) if summary['failures'] else 'nenhuma'}`",
        "",
        "## Cameras",
        "",
        "| ID | Disponibilidade | Estados | Incidentes recuperados | MTTR p95 | Aberto no fim |",
        "|---:|---:|---|---:|---:|---|",
    ]
    for camera in summary.get("cameras") or []:
        states = ", ".join(f"{key}={value}" for key, value in camera["states"].items())
        lines.append(
            f"| {camera['camera_id']} | {camera['availability_percent']}% | {states} | "
            f"{camera['recovered_incidents']} | {camera['mttr_p95_seconds']} | "
            f"{'sim' if camera['incident_open_at_end'] else 'nao'} |"
        )
    lines.extend([
        "",
        "## Endpoints",
        "",
        "| Endpoint | Tentativas | Sucessos | Percentual |",
        "|---|---:|---:|---:|",
    ])
    for name, metric in summary.get("endpoints", {}).items():
        lines.append(f"| {name} | {metric['attempts']} | {metric['successes']} | {metric['success_percent']}% |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "camera_id", "samples", "healthy_samples", "availability_percent", "states",
                "recovered_incidents", "mttr_p95_seconds", "incident_open_at_end",
            ),
        )
        writer.writeheader()
        for camera in summary.get("cameras") or []:
            writer.writerow({**camera, "states": json.dumps(camera.get("states") or {}, ensure_ascii=False)})
    return json_path, md_path, csv_path


def run_stage(
    collector: CampaignCollector,
    *,
    name: str,
    duration_seconds: float,
    expected_camera_ids: set[int],
    target_camera_count: int | None,
    thresholds: Thresholds,
) -> dict[str, Any]:
    stats = StageStats(
        name=name,
        planned_seconds=max(0.0, duration_seconds),
        interval_seconds=collector.args.interval_seconds,
        expected_camera_ids=set(expected_camera_ids),
        target_camera_count=target_camera_count,
        thresholds=thresholds,
    )
    print(f"[{utc_text()}] etapa={name} duracao={duration_seconds:.0f}s cameras={sorted(expected_camera_ids)}")
    deadline = time.monotonic() + max(0.0, duration_seconds)
    first = True
    try:
        while first or time.monotonic() < deadline:
            first = False
            cycle_started = time.monotonic()
            sample, logs = collector.collect_once(name, target_camera_count)
            stats.add(sample, logs)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sleep_for = min(remaining, max(0.0, collector.args.interval_seconds - (time.monotonic() - cycle_started)))
            if sleep_for > 0:
                time.sleep(sleep_for)
    except BaseException:
        partial = stats.finish()
        partial["passed"] = False
        partial["failures"] = list(dict.fromkeys([*partial.get("failures", []), "stage_interrupted"]))
        write_stage_report(collector.run_dir, partial)
        raise
    summary = stats.finish()
    write_stage_report(collector.run_dir, summary)
    print(
        f"[{utc_text()}] etapa={name} resultado={'APROVADO' if summary['passed'] else 'REPROVADO'} "
        f"falhas={summary['failures']}"
    )
    return summary


def thresholds_from_args(args: argparse.Namespace) -> Thresholds:
    return Thresholds(
        min_collector_coverage_percent=args.min_collector_coverage,
        min_endpoint_success_percent=args.min_endpoint_success,
        min_camera_availability_percent=args.min_camera_availability,
        min_canary_success_percent=args.min_canary_success,
        max_canary_p95_ms=args.max_canary_p95_ms,
        max_host_cpu_p95_percent=args.max_host_cpu_p95,
        max_host_ram_p95_percent=args.max_host_ram_p95,
        max_gpu_memory_p95_percent=args.max_gpu_memory_p95,
        max_drop_rate_percent=args.max_drop_rate,
    )


def make_run_dir(args: argparse.Namespace) -> Path:
    if args.run_dir:
        return Path(args.run_dir).resolve()
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    return (Path(args.output_dir) / f"campaign_{stamp}").resolve()


def write_campaign_index(run_dir: Path, summaries: list[dict[str, Any]], *, aborted: bool = False) -> None:
    decisive_stages = [item for item in summaries if not item.get("informational")]
    passed_stages = [item for item in decisive_stages if item.get("passed")]
    ramp_passed = [
        item for item in passed_stages
        if str(item.get("stage") or "").startswith("ramp_") and not str(item.get("stage") or "").endswith("_warmup")
    ]
    max_passed = max((int(item.get("target_camera_count") or 0) for item in ramp_passed), default=0)
    recommended = math.floor(max_passed * 0.75) if max_passed else None
    payload = {
        "generated_at": utc_text(),
        "aborted": aborted,
        "all_passed": bool(decisive_stages) and all(bool(item.get("passed")) for item in decisive_stages),
        "maximum_ramp_stage_passed": max_passed or None,
        "provisional_operational_limit_with_25_percent_reserve": recommended,
        "stages": summaries,
    }
    (run_dir / "campaign_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Resumo da campanha de estabilidade",
        "",
        f"- Gerado em: `{payload['generated_at']}`",
        f"- Campanha interrompida: `{'sim' if aborted else 'nao'}`",
        f"- Maior rampa aprovada: `{payload['maximum_ramp_stage_passed']}`",
        f"- Limite operacional provisorio com 25% de reserva: `{recommended}`",
        "",
        "| Etapa | Alvo | Rodando observado | Disponibilidade | Cobertura | Resultado | Falhas |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for item in summaries:
        outcome = "INFORMATIVO" if item.get("informational") else ("APROVADO" if item.get("passed") else "REPROVADO")
        lines.append(
            f"| {item['stage']} | {item.get('target_camera_count')} | {item.get('max_observed_running')} | "
            f"{item.get('camera_availability_percent')}% | {item.get('collector_coverage_percent')}% | "
            f"{outcome} | {', '.join(item.get('failures') or [])} |"
        )
    (run_dir / "campaign_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_observe(args: argparse.Namespace) -> int:
    run_dir = make_run_dir(args)
    collector = CampaignCollector(args, run_dir)
    summaries: list[dict[str, Any]] = []
    try:
        collector.manifest()
        preflight, _ = collector.collect_once("preflight")
        expected = set(args.camera_ids) or discover_expected_camera_ids(preflight)
        duration = args.duration_seconds
        summary = run_stage(
            collector,
            name=args.stage_name,
            duration_seconds=duration,
            expected_camera_ids=expected,
            target_camera_count=len(expected) if expected else None,
            thresholds=thresholds_from_args(args),
        )
        summaries.append(summary)
        write_campaign_index(run_dir, summaries)
        print(f"Relatorio: {run_dir / 'campaign_summary.md'}")
        return 0 if summary["passed"] else 2
    except KeyboardInterrupt:
        write_campaign_index(run_dir, summaries, aborted=True)
        return 130
    finally:
        collector.close()


def run_campaign(args: argparse.Namespace) -> int:
    if not args.allow_camera_control:
        raise SystemExit("campaign exige --allow-camera-control; nenhuma camera foi alterada")
    if not args.camera_ids:
        raise SystemExit("campaign exige --camera-ids em ordem de ativacao")
    run_dir = make_run_dir(args)
    collector = CampaignCollector(args, run_dir)
    summaries: list[dict[str, Any]] = []
    started_by_campaign: list[int] = []
    aborted = False
    thresholds = thresholds_from_args(args)
    try:
        collector.manifest()
        preflight, _ = collector.collect_once("preflight")
        preflight_failures = validate_control_preflight(preflight)
        if preflight_failures:
            raise RuntimeError(f"preflight bloqueou controle de cameras: {', '.join(preflight_failures)}")
        original_running = discover_expected_camera_ids(preflight)
        if not original_running:
            raise RuntimeError("preflight nao encontrou nenhuma camera desejada/ativa")
        if args.baseline_seconds > 0:
            baseline = run_stage(
                collector,
                name="baseline",
                duration_seconds=args.baseline_seconds,
                expected_camera_ids=original_running,
                target_camera_count=len(original_running),
                thresholds=thresholds,
            )
            summaries.append(baseline)
            if not baseline["passed"] and args.abort_on_failure:
                aborted = True
                return 2

        expected = set(original_running)
        candidates = [camera_id for camera_id in args.camera_ids if camera_id not in expected]
        highest_passed: int | None = None
        for target in args.ramp:
            if target < len(expected):
                print(f"Ignorando rampa {target}: ja existem {len(expected)} cameras esperadas")
                continue
            needed = target - len(expected)
            if needed > len(candidates):
                raise RuntimeError(
                    f"rampa {target} precisa de {needed} cameras adicionais, mas restam {len(candidates)} candidatas"
                )
            for _ in range(needed):
                camera_id = candidates.pop(0)
                result = collector.camera_control(camera_id, "start")
                append_jsonl(
                    collector.logs_handle,
                    {"ts": utc_text(), "stage": f"ramp_{target}_control", "source": "campaign", "line": f"start camera={camera_id} result={sanitize(result)}"},
                )
                if not result.get("ok"):
                    raise RuntimeError(f"falha ao iniciar camera {camera_id}: {result}")
                started_by_campaign.append(camera_id)
                expected.add(camera_id)
            if args.warmup_seconds > 0:
                warmup = run_stage(
                    collector,
                    name=f"ramp_{target}_warmup",
                    duration_seconds=args.warmup_seconds,
                    expected_camera_ids=expected,
                    target_camera_count=target,
                    thresholds=thresholds,
                )
                warmup["informational"] = True
                write_stage_report(collector.run_dir, warmup)
                summaries.append(warmup)
            measured = run_stage(
                collector,
                name=f"ramp_{target}",
                duration_seconds=args.stage_seconds,
                expected_camera_ids=expected,
                target_camera_count=target,
                thresholds=thresholds,
            )
            summaries.append(measured)
            if measured["passed"]:
                highest_passed = target
            elif args.abort_on_failure:
                aborted = True
                break

        if not aborted and highest_passed and args.soak_seconds > 0:
            soak = run_stage(
                collector,
                name=f"soak_{highest_passed}",
                duration_seconds=args.soak_seconds,
                expected_camera_ids=expected,
                target_camera_count=highest_passed,
                thresholds=thresholds,
            )
            summaries.append(soak)
        return 0 if summaries and all(item["passed"] for item in summaries if not str(item["stage"]).endswith("_warmup")) else 2
    except KeyboardInterrupt:
        aborted = True
        return 130
    except Exception as exc:
        aborted = True
        append_jsonl(
            collector.logs_handle,
            {"ts": utc_text(), "stage": "campaign", "source": "campaign", "line": f"fatal {type(exc).__name__}: {exc}"},
        )
        print(f"Campanha interrompida: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if args.restore_original_state:
            for camera_id in reversed(started_by_campaign):
                result = collector.camera_control(camera_id, "stop")
                append_jsonl(
                    collector.logs_handle,
                    {"ts": utc_text(), "stage": "restore", "source": "campaign", "line": f"stop camera={camera_id} result={sanitize(result)}"},
                )
        write_campaign_index(run_dir, summaries, aborted=aborted)
        collector.close()
        print(f"Relatorio: {run_dir / 'campaign_summary.md'}")


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--output-dir", default="reports/stability_campaign")
    parser.add_argument("--run-dir", default="", help="Diretorio exato; por padrao cria campaign_TIMESTAMP.")
    parser.add_argument("--web-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8001")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8090")
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument("--canary-interval-seconds", type=float, default=60.0)
    parser.add_argument("--detail-interval-seconds", type=float, default=60.0, help="Intervalo para persistir todos os campos dos workers; entre eles grava um conjunto compacto.")
    parser.add_argument("--containers", type=lambda value: tuple(item.strip() for item in value.split(",") if item.strip()), default=DEFAULT_CONTAINERS)
    parser.add_argument("--camera-ids", type=parse_csv_ints, default=[])
    parser.add_argument("--supervisor-token-env", default="ANALITICO_SUPERVISOR_TOKEN")
    parser.add_argument("--min-collector-coverage", type=float, default=99.0)
    parser.add_argument("--min-endpoint-success", type=float, default=99.0)
    parser.add_argument("--min-camera-availability", type=float, default=99.0)
    parser.add_argument("--min-canary-success", type=float, default=99.0)
    parser.add_argument("--max-canary-p95-ms", type=float, default=2000.0)
    parser.add_argument("--max-host-cpu-p95", type=float, default=80.0)
    parser.add_argument("--max-host-ram-p95", type=float, default=80.0)
    parser.add_argument("--max-gpu-memory-p95", type=float, default=80.0)
    parser.add_argument("--max-drop-rate", type=float, default=0.1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    observe = subparsers.add_parser("observe", help="Coleta somente leitura para baseline ou diagnostico.")
    add_common_arguments(observe)
    observe.add_argument("--stage-name", default="baseline")
    observe.add_argument("--duration-minutes", type=float, default=15.0)
    observe.add_argument("--duration-hours", type=float, default=0.0)

    campaign = subparsers.add_parser("campaign", help="Executa baseline, rampa e soak com controle opt-in.")
    add_common_arguments(campaign)
    campaign.add_argument("--ramp", type=parse_csv_ints, default=[8, 10, 12, 14, 16])
    campaign.add_argument("--baseline-hours", type=float, default=72.0)
    campaign.add_argument("--warmup-minutes", type=float, default=30.0)
    campaign.add_argument("--stage-minutes", type=float, default=120.0)
    campaign.add_argument("--soak-hours", type=float, default=168.0)
    campaign.add_argument("--allow-camera-control", action="store_true")
    campaign.add_argument("--restore-original-state", action=argparse.BooleanOptionalAction, default=True)
    campaign.add_argument("--abort-on-failure", action=argparse.BooleanOptionalAction, default=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.interval_seconds <= 0 or args.canary_interval_seconds <= 0 or args.detail_interval_seconds <= 0:
        parser.error("intervalos devem ser positivos")
    if args.command == "observe":
        args.duration_seconds = max(0.0, (args.duration_hours * 3600.0) if args.duration_hours > 0 else (args.duration_minutes * 60.0))
        return run_observe(args)
    args.baseline_seconds = max(0.0, args.baseline_hours * 3600.0)
    args.warmup_seconds = max(0.0, args.warmup_minutes * 60.0)
    args.stage_seconds = max(0.0, args.stage_minutes * 60.0)
    args.soak_seconds = max(0.0, args.soak_hours * 3600.0)
    return run_campaign(args)


if __name__ == "__main__":
    raise SystemExit(main())
