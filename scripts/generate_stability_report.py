from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

# The report is copied to /app/scripts and may be executed by absolute path.
# Keep /app importable in that mode as well as when run from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from app.core.config import settings
except Exception:  # pragma: no cover - fallback for ad-hoc runs outside the app env.
    settings = None


LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| ")
BAD_STATUSES = {"degraded", "error", "exception", "offline", "reconnecting", "stopped"}
NOISE_ACTIONS = {
    "http_request",
    "review_revalidator_audit_saved",
    "revalidator_dataset_collect",
}


@dataclass(slots=True)
class LogEntry:
    timestamp: datetime
    level: str
    logger: str
    camera_id: int | None
    worker_pid: str
    mode: str
    action: str
    status: str
    reason: str
    event_id: str
    message: str
    path: str


@dataclass(slots=True)
class Finding:
    timestamp: datetime
    camera_id: int | None
    level: str
    action: str
    status: str
    reason: str
    category: str
    cause: str
    message: str
    logger: str


@dataclass(slots=True)
class Incident:
    camera_id: int | None
    started_at: datetime
    last_signal_at: datetime
    category: str
    cause: str
    findings: list[Finding] = field(default_factory=list)
    common_cause_hint: str = ""


@dataclass(slots=True)
class WorkerLifecycleEvent:
    timestamp: datetime
    camera_id: int | None
    worker_pid: str
    action: str
    status: str
    reason: str
    kind: str
    logger: str
    message: str


@dataclass(slots=True)
class WorkerLifecycleSummary:
    camera_id: int | None
    starts: int = 0
    stops: int = 0
    manual_stops: int = 0
    fatal_errors: int = 0
    process_exits: int = 0
    watchdog_restarts: int = 0
    watchdog_restart_successes: int = 0
    watchdog_restart_failures: int = 0
    open_sessions_at_window_end: int = 0
    terminal_sessions_without_start_in_window: int = 0
    last_start_at: datetime | None = None
    last_terminal_at: datetime | None = None


RESOURCE_CSV_FIELDS = [
    "start",
    "end",
    "sampled",
    "cpu",
    "ram_mb",
    "gpu",
    "gpu_mem_mb",
    "fps",
    "raw_fps",
    "workers",
    "running",
    "host_cpu",
    "host_ram",
]

AVAILABILITY_CSV_FIELDS = [
    "camera_id",
    "camera_name",
    "total_minutes",
    "ia_minutes",
    "online_minutes",
    "warming_minutes",
    "degraded_minutes",
    "reconnecting_minutes",
    "offline_minutes",
    "stopped_minutes",
    "unknown_minutes",
    "ia_percent",
    "unavailable_minutes",
    "unavailable_percent",
    "instability_score",
]

STOP_CLASSIFICATION_CSV_FIELDS = [
    "camera_id",
    "camera_name",
    "primary_state",
    "recommended_action",
    "evidence",
    "total_minutes",
    "ia_minutes",
    "ia_percent",
    "stopped_minutes",
    "warming_minutes",
    "degraded_minutes",
    "reconnecting_minutes",
    "offline_minutes",
    "unknown_minutes",
    "manual_stops",
    "process_exits",
    "fatal_errors",
    "watchdog_restarts",
    "gateway_signals",
    "gateway_timeout_signals",
    "server_worker_signals",
    "manual_signals",
]


def _default_base_dir() -> Path:
    if settings is not None:
        try:
            return Path(settings.app_base_dir)
        except Exception:
            pass
    return Path.cwd()


def _default_database_url() -> str:
    if settings is not None:
        return str(settings.database_url)
    return f"sqlite:///{(_default_base_dir() / 'data' / 'analytics.db').resolve().as_posix()}"


def _default_logs_dir() -> Path:
    if settings is not None:
        return Path(settings.logs_dir)
    return _default_base_dir() / "logs"


def _parse_datetime(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _sqlite_path_from_url(database_url: str) -> Path:
    if database_url.startswith("sqlite:///"):
        return Path(database_url.removeprefix("sqlite:///"))
    if database_url.startswith("sqlite://"):
        return Path(database_url.removeprefix("sqlite://"))
    return Path(database_url)


def parse_log_line(line: str, path: str = "") -> LogEntry | None:
    if not LOG_LINE_RE.match(line):
        return None
    parts = line.rstrip("\n").split(" | ")
    if len(parts) < 18:
        return None

    context = {}
    for part in parts[3:17]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        context[key.strip()] = value.strip()

    camera_id = None
    raw_camera = context.get("cam", "-")
    try:
        if raw_camera not in {"", "-"}:
            camera_id = int(raw_camera)
    except Exception:
        camera_id = None

    try:
        timestamp = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

    return LogEntry(
        timestamp=timestamp,
        level=parts[1].strip().upper(),
        logger=parts[2].strip(),
        camera_id=camera_id,
        worker_pid=context.get("pid", "-"),
        mode=context.get("mode", "-"),
        action=context.get("action", "-"),
        status=context.get("status", "-"),
        reason=context.get("reason", "-"),
        event_id=context.get("event", "-"),
        message=" | ".join(parts[17:]).strip(),
        path=path,
    )


def _classify_text(action: str, status: str, reason: str, logger: str, message: str) -> tuple[str, str]:
    text = " ".join([action, status, reason, logger, message]).lower()

    if "frame_ring_gap" in text or "gateway_frames_context_gap" in text:
        return (
            "worker_lag",
            "Worker/consumidor ficou atrasado em relacao ao ring buffer curto do gateway; isso nao prova falha da camera/gateway.",
        )
    if "manual_stop" in text or "stopped_manual" in text:
        return "operacao_manual", "Parada manual ou camera marcada como parada."
    if "shutdown" in text:
        return "servidor_reinicio", "Processo ou servico foi encerrado/reiniciado."
    if "watchdog" in text or "stale_worker" in text or "worker process" in text:
        return "servidor_worker", "Worker travou, ficou stale ou foi reiniciado pelo watchdog."
    if "queue_full" in text or "dropped_or_rejected" in text or "jobs_dropped" in text:
        return "servidor_backpressure", "Fila interna encheu ou houve descarte por pressao no servidor."
    if "inference_failed" in text or "cuda" in text or "out of memory" in text:
        return "servidor_ia", "Falha na inferencia/modelo/recursos de IA."
    if "model_not_found" in text:
        return "config_modelo_ia", "Modelo de IA configurado nao foi encontrado."
    if "gateway" in text:
        return "gateway_video", "Falha ou degradacao no camera-gateway/ponte de video."
    if "rtsp" in text or "capture" in text or "no_frame" in text or "frame_missing" in text:
        return "camera_rtsp", "Falha no fluxo da camera/RTSP ou ausencia de frames."
    if "timeout" in text or "timed out" in text or "connection" in text or "unreachable" in text or "refused" in text or "socket" in text:
        return "rede_camera", "Falha de rede entre servidor e camera/NVR/gateway."
    if status in BAD_STATUSES:
        return "indefinido", "Sinal ruim registrado, mas sem evidencia suficiente para cravar a origem."
    return "informativo", "Registro operacional relevante."


def is_relevant_entry(entry: LogEntry) -> bool:
    action = entry.action.strip().lower()
    status = entry.status.strip().lower()
    if action in NOISE_ACTIONS:
        return False
    if entry.level in {"WARNING", "ERROR", "CRITICAL"}:
        return True
    if status in BAD_STATUSES:
        return True
    if any(token in action for token in ("restart", "reconnect", "failed", "failure", "health_change")):
        return True
    return False


def finding_from_entry(entry: LogEntry) -> Finding | None:
    if not is_relevant_entry(entry):
        return None
    category, cause = _classify_text(entry.action, entry.status, entry.reason, entry.logger, entry.message)
    return Finding(
        timestamp=entry.timestamp,
        camera_id=entry.camera_id,
        level=entry.level,
        action=entry.action,
        status=entry.status,
        reason=entry.reason,
        category=category,
        cause=cause,
        message=entry.message,
        logger=entry.logger,
    )


def worker_lifecycle_event_from_entry(entry: LogEntry) -> WorkerLifecycleEvent | None:
    action = entry.action.strip().lower()
    status = entry.status.strip().lower()
    reason = entry.reason.strip().lower()

    kind = ""
    if action == "worker_process_entry" and reason == "process_started":
        kind = "start"
    elif action == "run_worker" and status == "stopped" and reason == "stop_requested":
        kind = "manual_stop"
    elif action == "run_worker" and status == "stopped" and reason == "shutdown":
        kind = "stop"
    elif action == "worker_process_entry" and reason == "fatal_exception":
        kind = "fatal_error"
    elif action == "run_worker" and reason == "fatal_exception":
        kind = "fatal_error"
    elif action == "worker_process_exit":
        kind = "process_exit"
    elif action == "watchdog_force_restart":
        kind = "watchdog_restart"
    elif action == "watchdog_restart_success":
        kind = "watchdog_restart_success"
    elif action == "watchdog_restart_failed":
        kind = "watchdog_restart_failed"

    if not kind:
        return None

    return WorkerLifecycleEvent(
        timestamp=entry.timestamp,
        camera_id=entry.camera_id,
        worker_pid=entry.worker_pid,
        action=entry.action,
        status=entry.status,
        reason=entry.reason,
        kind=kind,
        logger=entry.logger,
        message=entry.message,
    )


def build_worker_lifecycle_summary(events: list[WorkerLifecycleEvent]) -> list[WorkerLifecycleSummary]:
    summaries: dict[int | None, WorkerLifecycleSummary] = {}
    started_pids_by_camera: dict[int | None, set[str]] = {}
    terminal_pids_by_camera: dict[int | None, set[str]] = {}

    for event in sorted(events, key=lambda item: item.timestamp):
        summary = summaries.setdefault(event.camera_id, WorkerLifecycleSummary(camera_id=event.camera_id))
        worker_pid = str(event.worker_pid or "-")
        if event.kind == "start":
            summary.starts += 1
            summary.last_start_at = event.timestamp
            if worker_pid != "-":
                started_pids_by_camera.setdefault(event.camera_id, set()).add(worker_pid)
        elif event.kind == "stop":
            summary.stops += 1
            summary.last_terminal_at = event.timestamp
            if worker_pid != "-":
                terminal_pids_by_camera.setdefault(event.camera_id, set()).add(worker_pid)
        elif event.kind == "manual_stop":
            summary.manual_stops += 1
        elif event.kind == "fatal_error":
            summary.fatal_errors += 1
            summary.last_terminal_at = event.timestamp
            if worker_pid != "-":
                terminal_pids_by_camera.setdefault(event.camera_id, set()).add(worker_pid)
        elif event.kind == "process_exit":
            summary.process_exits += 1
            summary.last_terminal_at = event.timestamp
            if worker_pid != "-":
                terminal_pids_by_camera.setdefault(event.camera_id, set()).add(worker_pid)
        elif event.kind == "watchdog_restart":
            summary.watchdog_restarts += 1
        elif event.kind == "watchdog_restart_success":
            summary.watchdog_restart_successes += 1
        elif event.kind == "watchdog_restart_failed":
            summary.watchdog_restart_failures += 1

    for camera_id, summary in summaries.items():
        started = started_pids_by_camera.get(camera_id, set())
        terminal = terminal_pids_by_camera.get(camera_id, set())
        summary.open_sessions_at_window_end = len(started - terminal)
        summary.terminal_sessions_without_start_in_window = len(terminal - started)

    return sorted(summaries.values(), key=lambda item: (-1 if item.camera_id is None else item.camera_id))


def iter_log_paths(logs_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in ("app.log*", "error.log*"):
        paths.extend(path for path in logs_dir.glob(pattern) if path.is_file())
    return sorted(set(paths), key=lambda path: path.stat().st_mtime)


def load_log_entries(logs_dir: Path, since: datetime, until: datetime) -> list[LogEntry]:
    entries: list[LogEntry] = []
    for path in iter_log_paths(logs_dir):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    entry = parse_log_line(line, path=str(path))
                    if entry and since <= entry.timestamp <= until:
                        entries.append(entry)
        except Exception:
            continue
    return sorted(entries, key=lambda item: item.timestamp)


def build_incidents(findings: list[Finding], gap_minutes: int = 5, common_window_minutes: int = 3) -> list[Incident]:
    incidents: list[Incident] = []
    current_by_camera: dict[int | None, Incident] = {}
    max_gap = timedelta(minutes=gap_minutes)

    for finding in sorted(findings, key=lambda item: item.timestamp):
        key = finding.camera_id
        current = current_by_camera.get(key)
        if current is None or finding.timestamp - current.last_signal_at > max_gap:
            if current is not None:
                incidents.append(current)
            current = Incident(
                camera_id=key,
                started_at=finding.timestamp,
                last_signal_at=finding.timestamp,
                category=finding.category,
                cause=finding.cause,
                findings=[],
            )
            current_by_camera[key] = current

        current.findings.append(finding)
        current.last_signal_at = finding.timestamp
        if current.category in {"informativo", "indefinido"} and finding.category not in {"informativo", "indefinido"}:
            current.category = finding.category
            current.cause = finding.cause

    incidents.extend(current_by_camera.values())
    incidents.sort(key=lambda item: item.started_at)

    for incident in incidents:
        if incident.camera_id is None:
            continue
        peers = {
            other.camera_id
            for other in incidents
            if other.camera_id is not None
            and other.camera_id != incident.camera_id
            and abs((other.started_at - incident.started_at).total_seconds()) <= common_window_minutes * 60
        }
        if peers:
            incident.common_cause_hint = "Varias cameras tiveram sinais proximos; investigar rede/gateway/servidor como causa comum."
    return incidents


def load_events(database_url: str, since: datetime, until: datetime) -> tuple[list[dict], dict[int, str]]:
    db_path = _sqlite_path_from_url(database_url)
    if not db_path.exists():
        return [], {}

    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cameras = {
            int(row["id"]): str(row["name"] or f"Camera {row['id']}")
            for row in conn.execute("SELECT id, name FROM cameras")
        }

        since_text = since.strftime("%Y-%m-%d %H:%M:%S")
        until_text = until.strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            """
            SELECT
                e.id, e.camera_id, c.name AS camera_name, e.event_type, e.started_at, e.ended_at,
                e.track_id, e.detector_score, e.confidence, e.event_score, e.severity, e.status,
                e.alarm_eligible, e.lifecycle_action, e.is_alarm_active, e.snapshot_path, e.clip_path
            FROM events e
            LEFT JOIN cameras c ON c.id = e.camera_id
            WHERE COALESCE(e.started_at, e.created_at) >= ?
              AND COALESCE(e.started_at, e.created_at) <= ?
            ORDER BY COALESCE(e.started_at, e.created_at), e.id
            """,
            (since_text, until_text),
        ).fetchall()
        return [dict(row) for row in rows], cameras
    finally:
        conn.close()


def _csv_write(path: Path, rows: Iterable[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _fmt_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _counter_table(counter: Counter, title_a: str, title_b: str) -> list[str]:
    if not counter:
        return [f"| {title_a} | {title_b} |", "|---|---:|", "| nenhum | 0 |"]
    lines = [f"| {title_a} | {title_b} |", "|---|---:|"]
    for key, count in counter.most_common():
        lines.append(f"| {key} | {count} |")
    return lines


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _metric_value(resource_summary: dict[str, Any], key: str, field: str) -> float | None:
    metrics = ((resource_summary or {}).get("summary") or {}).get("metrics") or {}
    return _safe_float((metrics.get(key) or {}).get(field))


def _fmt_metric(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "-"
    if abs(value) >= 100:
        text = f"{value:.0f}"
    else:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text}{suffix}"


def load_resource_history_summary(since: datetime, until: datetime, *, bucket_minutes: int = 1) -> dict[str, Any]:
    try:
        from app.services.resource_history_store import resource_history_store

        return resource_history_store.query(
            start_iso=since.isoformat(),
            end_iso=until.isoformat(),
            bucket_minutes=bucket_minutes,
        )
    except Exception as exc:
        return {
            "error": f"{type(exc).__name__}: {exc}",
            "summary": {"samples": 0, "metrics": {}},
            "buckets": [],
        }


def load_operational_availability(since: datetime, until: datetime, *, bucket_minutes: int = 1) -> list[dict[str, Any]]:
    try:
        from app.services.operational_history_store import operational_history_store

        history = operational_history_store.query(
            start_iso=since.isoformat(),
            end_iso=until.isoformat(),
            bucket_minutes=bucket_minutes,
        )
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    for camera in history.get("cameras", []):
        minutes = camera.get("minutes") or {}
        ia = int(minutes.get("ia", 0) or 0)
        online = int(minutes.get("online", 0) or 0)
        warming = int(minutes.get("warming", 0) or 0)
        degraded = int(minutes.get("degraded", 0) or 0)
        reconnecting = int(minutes.get("reconnecting", 0) or 0)
        offline = int(minutes.get("offline", 0) or 0)
        stopped = int(minutes.get("stopped", 0) or 0)
        unknown = int(minutes.get("unknown", 0) or 0)
        total = max(0, ia + online + warming + degraded + reconnecting + offline + stopped + unknown)
        unavailable = degraded + reconnecting + offline + stopped
        instability = unavailable + unknown
        rows.append(
            {
                "camera_id": int(camera.get("id") or 0),
                "camera_name": str(camera.get("name") or f"Camera {camera.get('id') or '-'}"),
                "total_minutes": total,
                "ia_minutes": ia,
                "online_minutes": online,
                "warming_minutes": warming,
                "degraded_minutes": degraded,
                "reconnecting_minutes": reconnecting,
                "offline_minutes": offline,
                "stopped_minutes": stopped,
                "unknown_minutes": unknown,
                "ia_percent": round((ia / total) * 100.0, 2) if total else None,
                "unavailable_minutes": unavailable,
                "unavailable_percent": round((unavailable / total) * 100.0, 2) if total else None,
                "instability_score": instability,
            }
        )
    return sorted(rows, key=lambda item: (-int(item.get("instability_score") or 0), str(item.get("camera_name") or "")))


def build_camera_stop_classification(
    camera_availability: list[dict[str, Any]],
    findings: list[Finding],
    worker_lifecycle_summary: list[WorkerLifecycleSummary],
) -> list[dict[str, Any]]:
    findings_by_camera: dict[int, list[Finding]] = {}
    for finding in findings:
        if finding.camera_id is None:
            continue
        findings_by_camera.setdefault(int(finding.camera_id), []).append(finding)

    lifecycle_by_camera = {
        int(item.camera_id): item
        for item in worker_lifecycle_summary
        if item.camera_id is not None
    }

    rows: list[dict[str, Any]] = []
    for item in camera_availability:
        camera_id = int(item.get("camera_id") or 0)
        camera_findings = findings_by_camera.get(camera_id, [])
        lifecycle = lifecycle_by_camera.get(camera_id)
        text_blob = " ".join(
            " ".join([finding.action, finding.status, finding.reason, finding.category, finding.message]).lower()
            for finding in camera_findings
        )

        manual_signals = sum(1 for finding in camera_findings if finding.category == "operacao_manual")
        gateway_signals = sum(1 for finding in camera_findings if finding.category == "gateway_video")
        gateway_timeout_signals = sum(
            1
            for finding in camera_findings
            if finding.category == "gateway_video"
            and ("timeout" in " ".join([finding.action, finding.status, finding.reason, finding.message]).lower()
                 or "queued" in " ".join([finding.action, finding.status, finding.reason, finding.message]).lower())
        )
        server_worker_signals = sum(1 for finding in camera_findings if finding.category == "servidor_worker")

        manual_stops = int(lifecycle.manual_stops if lifecycle else 0)
        process_exits = int(lifecycle.process_exits if lifecycle else 0)
        fatal_errors = int(lifecycle.fatal_errors if lifecycle else 0)
        watchdog_restarts = int(lifecycle.watchdog_restarts if lifecycle else 0)

        total = int(item.get("total_minutes") or 0)
        ia = int(item.get("ia_minutes") or 0)
        stopped = int(item.get("stopped_minutes") or 0)
        warming = int(item.get("warming_minutes") or 0)
        degraded = int(item.get("degraded_minutes") or 0)
        reconnecting = int(item.get("reconnecting_minutes") or 0)
        offline = int(item.get("offline_minutes") or 0)
        unknown = int(item.get("unknown_minutes") or 0)
        ia_percent = _safe_float(item.get("ia_percent"))

        evidence: list[str] = []
        primary_state = "healthy_or_active"
        recommended_action = "Sem acao; camera ficou majoritariamente ativa na janela."

        if manual_stops > 0 or manual_signals > 0 or "stopped_manual" in text_blob or "manual_stop" in text_blob:
            primary_state = "manual_stop"
            recommended_action = "Nao contar como falha operacional; confirmar se a parada manual era esperada."
            evidence.append(f"manual_stops={manual_stops}")
            evidence.append(f"manual_signals={manual_signals}")
        elif gateway_timeout_signals > 0 or (gateway_signals > 0 and warming > 0 and ia == 0):
            primary_state = "gateway_timeout_or_queued"
            recommended_action = "Investigar RTSP/NVR/canal ou limite/fila do camera-gateway."
            evidence.append(f"gateway_signals={gateway_signals}")
            evidence.append(f"gateway_timeout_signals={gateway_timeout_signals}")
            evidence.append(f"warming_minutes={warming}")
        elif process_exits > 0 or fatal_errors > 0 or watchdog_restarts > 0 or server_worker_signals > 0:
            primary_state = "system_worker_issue"
            recommended_action = "Contar como instabilidade do servidor/worker; olhar logs e watchdog."
            evidence.append(f"process_exits={process_exits}")
            evidence.append(f"fatal_errors={fatal_errors}")
            evidence.append(f"watchdog_restarts={watchdog_restarts}")
            evidence.append(f"server_worker_signals={server_worker_signals}")
        elif stopped > 0 and ia == 0:
            primary_state = "not_started_or_not_restored"
            recommended_action = "Nao cravar falha; camera ficou parada sem evidencia de parada manual nem crash."
            evidence.append(f"stopped_minutes={stopped}")
        elif stopped > 0:
            primary_state = "partial_stop_without_cause"
            recommended_action = "Separar da indisponibilidade critica; falta evidencia para manual ou falha."
            evidence.append(f"stopped_minutes={stopped}")
            evidence.append(f"ia_minutes={ia}")
        elif offline > 0 or reconnecting > 0 or degraded > 0:
            primary_state = "camera_or_network_degraded"
            recommended_action = "Investigar RTSP/rede/camera se repetir; nao parece parada manual."
            evidence.append(f"offline_minutes={offline}")
            evidence.append(f"reconnecting_minutes={reconnecting}")
            evidence.append(f"degraded_minutes={degraded}")
        elif warming > 0 and ia == 0:
            primary_state = "warming_without_worker"
            recommended_action = "Verificar fila de start/gateway; camera aqueceu mas nao virou worker ativo."
            evidence.append(f"warming_minutes={warming}")
        elif unknown > 0 and ia == 0:
            primary_state = "no_history_or_unknown"
            recommended_action = "Historico insuficiente; nao contar como falha sem logs adicionais."
            evidence.append(f"unknown_minutes={unknown}")

        if not evidence:
            evidence.append(f"ia_minutes={ia}")
            evidence.append(f"total_minutes={total}")

        rows.append(
            {
                "camera_id": camera_id,
                "camera_name": str(item.get("camera_name") or f"Camera {camera_id}"),
                "primary_state": primary_state,
                "recommended_action": recommended_action,
                "evidence": "; ".join(evidence),
                "total_minutes": total,
                "ia_minutes": ia,
                "ia_percent": item.get("ia_percent"),
                "stopped_minutes": stopped,
                "warming_minutes": warming,
                "degraded_minutes": degraded,
                "reconnecting_minutes": reconnecting,
                "offline_minutes": offline,
                "unknown_minutes": unknown,
                "manual_stops": manual_stops,
                "process_exits": process_exits,
                "fatal_errors": fatal_errors,
                "watchdog_restarts": watchdog_restarts,
                "gateway_signals": gateway_signals,
                "gateway_timeout_signals": gateway_timeout_signals,
                "server_worker_signals": server_worker_signals,
                "manual_signals": manual_signals,
            }
        )

    state_order = {
        "system_worker_issue": 0,
        "gateway_timeout_or_queued": 1,
        "camera_or_network_degraded": 2,
        "not_started_or_not_restored": 3,
        "partial_stop_without_cause": 4,
        "manual_stop": 5,
        "warming_without_worker": 6,
        "no_history_or_unknown": 7,
        "healthy_or_active": 8,
    }
    return sorted(
        rows,
        key=lambda row: (
            state_order.get(str(row.get("primary_state")), 99),
            -int(row.get("stopped_minutes") or 0),
            -int(row.get("gateway_signals") or 0),
            str(row.get("camera_name") or ""),
        ),
    )


def build_resource_pressure_notes(resource_summary: dict[str, Any]) -> list[str]:
    if (resource_summary or {}).get("error"):
        return [f"- Historico de recursos indisponivel: `{resource_summary['error']}`"]

    samples = int(((resource_summary or {}).get("summary") or {}).get("samples") or 0)
    if samples <= 0:
        return ["- Sem amostras de historico de recursos para esta janela."]

    notes = [f"- Amostras de recursos: `{samples}`"]
    workers_peak = _metric_value(resource_summary, "workers", "peak")
    running_peak = _metric_value(resource_summary, "running", "peak")
    host_cpu_peak = _metric_value(resource_summary, "host_cpu", "peak")
    host_ram_peak = _metric_value(resource_summary, "host_ram", "peak")
    cpu_peak = _metric_value(resource_summary, "cpu", "peak")
    ram_peak = _metric_value(resource_summary, "ram_mb", "peak")
    gpu_peak = _metric_value(resource_summary, "gpu", "peak")
    gpu_mem_peak = _metric_value(resource_summary, "gpu_mem_mb", "peak")
    gpu_mem_total = _safe_float(((resource_summary or {}).get("summary") or {}).get("gpu_mem_total_mb"))
    gpu_mem_pct_peak = (gpu_mem_peak / gpu_mem_total * 100.0) if gpu_mem_peak is not None and gpu_mem_total else None

    notes.append(
        "- Picos: "
        f"workers `{_fmt_metric(workers_peak)}`, cameras rodando `{_fmt_metric(running_peak)}`, "
        f"CPU workers `{_fmt_metric(cpu_peak, '%')}`, CPU host `{_fmt_metric(host_cpu_peak, '%')}`, "
        f"RAM workers `{_fmt_metric(ram_peak, ' MB')}`, RAM host `{_fmt_metric(host_ram_peak, '%')}`, "
        f"GPU `{_fmt_metric(gpu_peak, '%')}`, VRAM `{_fmt_metric(gpu_mem_peak, ' MB')}`"
    )

    warnings: list[str] = []
    if host_cpu_peak is not None and host_cpu_peak >= 85.0:
        warnings.append("CPU do host chegou perto do teto; reduzir decode/scale/FPS ou cameras por lote.")
    if gpu_mem_pct_peak is not None and gpu_mem_pct_peak >= 90.0:
        warnings.append("VRAM passou de 90%; risco de queda de IA/engine e reinicios de workers.")
    if workers_peak is not None and running_peak is not None and workers_peak < running_peak:
        warnings.append("Houve menos workers ativos que cameras rodando; investigar fila de start, watchdog ou guard.")
    if warnings:
        notes.append("- Alertas de capacidade: " + " ".join(warnings))
    else:
        notes.append("- Nenhum teto obvio apareceu nas amostras agregadas; olhar incidentes por camera para gargalos localizados.")
    return notes


def build_markdown(
    *,
    since: datetime,
    until: datetime,
    events: list[dict],
    findings: list[Finding],
    incidents: list[Incident],
    worker_lifecycle_events: list[WorkerLifecycleEvent],
    worker_lifecycle_summary: list[WorkerLifecycleSummary],
    resource_summary: dict[str, Any],
    camera_availability: list[dict[str, Any]],
    camera_stop_classification: list[dict[str, Any]],
    cameras: dict[int, str],
    csv_paths: dict[str, Path],
) -> str:
    events_by_camera = Counter(str(row.get("camera_name") or cameras.get(int(row.get("camera_id") or 0), row.get("camera_id"))) for row in events)
    events_by_type = Counter(str(row.get("event_type") or "-") for row in events)
    events_by_status = Counter(str(row.get("status") or "-") for row in events)
    incidents_by_category = Counter(item.category for item in incidents)
    findings_by_category = Counter(item.category for item in findings)

    active_alarm_count = sum(1 for row in events if str(row.get("is_alarm_active")).lower() in {"1", "true"})
    eligible_count = sum(1 for row in events if str(row.get("alarm_eligible")).lower() in {"1", "true"})
    worker_lifecycle_by_kind = Counter(item.kind for item in worker_lifecycle_events)
    open_worker_sessions = sum(item.open_sessions_at_window_end for item in worker_lifecycle_summary)
    terminal_without_start = sum(item.terminal_sessions_without_start_in_window for item in worker_lifecycle_summary)
    unstable_cameras = [item for item in camera_availability if int(item.get("instability_score") or 0) > 0]
    stop_state_counts = Counter(str(item.get("primary_state") or "unknown") for item in camera_stop_classification)
    actionable_stop_rows = [
        item
        for item in camera_stop_classification
        if str(item.get("primary_state") or "") != "healthy_or_active"
    ]

    lines = [
        "# Relatorio de estabilidade",
        "",
        f"- Janela analisada: `{_fmt_dt(since)}` ate `{_fmt_dt(until)}`",
        f"- Eventos no banco: `{len(events)}`",
        f"- Eventos elegiveis para alarme: `{eligible_count}`",
        f"- Eventos com alarme ativo: `{active_alarm_count}`",
        f"- Sinais relevantes em logs: `{len(findings)}`",
        f"- Incidentes agrupados: `{len(incidents)}`",
        f"- Cameras com indisponibilidade/historico desconhecido na janela: `{len(unstable_cameras)}`",
        "",
        "## Leitura rapida",
    ]

    if not incidents:
        lines.append("- Nenhuma queda/degradacao relevante apareceu nos logs da janela.")
    else:
        common_count = sum(1 for item in incidents if item.common_cause_hint)
        lines.append(f"- Incidentes com indicio de causa comum entre cameras: `{common_count}`")
        lines.append("- Quando a causa aparece como rede_camera, isso indica caminho servidor-camera/NVR/gateway; nao prova queda da internet publica sem ping externo no periodo.")

    lines.extend(["", "## Visao geral operacional"])
    if not camera_stop_classification:
        lines.append("- Sem historico operacional suficiente para classificar cameras nesta janela.")
    else:
        total_classified = len(camera_stop_classification)
        lines.append(f"- Cameras classificadas: `{total_classified}`")
        lines.append(f"- Saudaveis/ativas: `{stop_state_counts.get('healthy_or_active', 0)}`")
        lines.append(f"- Parada manual: `{stop_state_counts.get('manual_stop', 0)}`")
        lines.append(f"- Nao iniciada/nao restaurada: `{stop_state_counts.get('not_started_or_not_restored', 0)}`")
        lines.append(f"- Timeout/fila gateway: `{stop_state_counts.get('gateway_timeout_or_queued', 0)}`")
        lines.append(f"- Problema worker/sistema: `{stop_state_counts.get('system_worker_issue', 0)}`")
        lines.append(f"- Degradacao camera/rede: `{stop_state_counts.get('camera_or_network_degraded', 0)}`")
        lines.append("")
        lines.append("| Estado separado | Cameras |")
        lines.append("|---|---:|")
        for state, count in stop_state_counts.most_common():
            lines.append(f"| {state} | {count} |")

    lines.extend(["", "## Reparticao de paradas e indisponibilidade"])
    if not actionable_stop_rows:
        lines.append("- Nenhuma camera precisou de reparticao especial; as cameras classificadas ficaram majoritariamente ativas.")
    else:
        lines.append("| Camera | Estado separado | IA % | Stop min | Warm min | Offline | Evidencia | Acao sugerida |")
        lines.append("|---|---|---:|---:|---:|---:|---|---|")
        for item in actionable_stop_rows[:40]:
            lines.append(
                f"| {item['camera_name']} | {item['primary_state']} | "
                f"{_fmt_metric(_safe_float(item.get('ia_percent')), '%')} | "
                f"{item.get('stopped_minutes', 0)} | {item.get('warming_minutes', 0)} | "
                f"{item.get('offline_minutes', 0)} | {item.get('evidence', '-')} | "
                f"{item.get('recommended_action', '-')} |"
            )

    lines.extend(["", "## Eventos por camera"])
    lines.extend(_counter_table(events_by_camera, "Camera", "Eventos"))
    lines.extend(["", "## Eventos por tipo"])
    lines.extend(_counter_table(events_by_type, "Tipo", "Eventos"))
    lines.extend(["", "## Status dos eventos"])
    lines.extend(_counter_table(events_by_status, "Status", "Eventos"))
    lines.extend(["", "## Incidentes por causa provavel"])
    lines.extend(_counter_table(incidents_by_category, "Causa provavel", "Incidentes"))
    lines.extend(["", "## Sinais de log por categoria"])
    lines.extend(_counter_table(findings_by_category, "Categoria", "Sinais"))
    lines.extend(["", "## Pressao de recursos e limite operacional"])
    lines.extend(build_resource_pressure_notes(resource_summary))
    lines.extend(["", "## Cameras mais instaveis por historico operacional"])
    if not camera_availability:
        lines.append("- Sem historico operacional para esta janela.")
    else:
        lines.append("| Camera | IA % | Indisp. min | Indisp. % | Offline | Reconn. | Degrad. | Stopped | Sem amostra |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for item in camera_availability[:30]:
            lines.append(
                f"| {item['camera_name']} | {_fmt_metric(_safe_float(item.get('ia_percent')), '%')} | "
                f"{item['unavailable_minutes']} | {_fmt_metric(_safe_float(item.get('unavailable_percent')), '%')} | "
                f"{item['offline_minutes']} | {item['reconnecting_minutes']} | {item['degraded_minutes']} | "
                f"{item['stopped_minutes']} | {item['unknown_minutes']} |"
            )
    lines.extend(["", "## Ciclo de vida dos workers"])
    lines.extend(_counter_table(worker_lifecycle_by_kind, "Evento", "Ocorrencias"))
    lines.append("")
    if not worker_lifecycle_summary:
        lines.append("- Nenhum evento de ciclo de vida de worker apareceu na janela.")
    else:
        lines.append(f"- Sessoes abertas no fim da janela a partir de starts vistos no periodo: `{open_worker_sessions}`")
        lines.append(f"- Sessoes encerradas sem start visto dentro da janela: `{terminal_without_start}`")
        lines.append(
            "- `open_sessions_at_window_end` aponta processos iniciados na janela sem terminal observado depois; "
            "isso pode significar worker ainda rodando no fim da coleta, nao necessariamente vazamento."
        )
        lines.append("")
        lines.append(
            "| Camera | Starts | Stops | Paradas manuais | Fatal | Process exits | Watchdog restarts | "
            "Watchdog ok | Watchdog falhou | Sessoes abertas |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for item in worker_lifecycle_summary[:80]:
            camera = "global" if item.camera_id is None else cameras.get(item.camera_id, f"Camera {item.camera_id}")
            lines.append(
                f"| {camera} | {item.starts} | {item.stops} | {item.manual_stops} | {item.fatal_errors} | "
                f"{item.process_exits} | {item.watchdog_restarts} | {item.watchdog_restart_successes} | "
                f"{item.watchdog_restart_failures} | {item.open_sessions_at_window_end} |"
            )

    lines.extend(["", "## Incidentes principais"])
    if not incidents:
        lines.append("- Nenhum incidente agrupado.")
    else:
        for incident in incidents[:80]:
            camera = "global" if incident.camera_id is None else cameras.get(incident.camera_id, f"Camera {incident.camera_id}")
            duration = int((incident.last_signal_at - incident.started_at).total_seconds())
            first = incident.findings[0] if incident.findings else None
            lines.append(
                f"- `{_fmt_dt(incident.started_at)}` camera `{camera}` categoria `{incident.category}` "
                f"sinais `{len(incident.findings)}` janela `{duration}s` motivo `{incident.cause}`"
            )
            if first:
                lines.append(f"  - primeiro sinal: action=`{first.action}` status=`{first.status}` reason=`{first.reason}`")
            if incident.common_cause_hint:
                lines.append(f"  - alerta: {incident.common_cause_hint}")

    lines.extend(
        [
            "",
            "## Arquivos gerados",
            f"- Eventos CSV: `{csv_paths['events']}`",
            f"- Quedas/incidentes CSV: `{csv_paths['incidents']}`",
            f"- Sinais de log CSV: `{csv_paths['findings']}`",
            f"- Ciclo de vida dos workers CSV: `{csv_paths['worker_lifecycle_events']}`",
            f"- Resumo dos workers CSV: `{csv_paths['worker_lifecycle_summary']}`",
            f"- Disponibilidade por camera CSV: `{csv_paths['camera_availability']}`",
            f"- Reparticao de paradas CSV: `{csv_paths['camera_stop_classification']}`",
            f"- Recursos por bucket CSV: `{csv_paths['resource_buckets']}`",
            "",
            "## Observacao sobre causa raiz",
            "A classificacao e baseada em logs do sistema, gateway, worker e RTSP. Para separar com certeza internet publica, rede local, NVR e servidor, o proximo passo e registrar ping continuo para gateway, NVR/cameras e um host externo durante o teste.",
        ]
    )
    return "\n".join(lines) + "\n"


def generate_report(args: argparse.Namespace) -> dict[str, Path]:
    until = _parse_datetime(args.until) if args.until else datetime.now()
    since = _parse_datetime(args.since) if args.since else until - timedelta(hours=float(args.hours))

    logs_dir = Path(args.logs_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    entries = load_log_entries(logs_dir, since, until)
    findings = [finding for entry in entries if (finding := finding_from_entry(entry)) is not None]
    incidents = build_incidents(findings, gap_minutes=int(args.incident_gap_minutes), common_window_minutes=int(args.common_window_minutes))
    worker_lifecycle_events = [
        event for entry in entries if (event := worker_lifecycle_event_from_entry(entry)) is not None
    ]
    worker_lifecycle_summary = build_worker_lifecycle_summary(worker_lifecycle_events)
    resource_summary = load_resource_history_summary(since, until, bucket_minutes=1)
    camera_availability = load_operational_availability(since, until, bucket_minutes=1)
    events, cameras = load_events(args.database_url, since, until)
    camera_stop_classification = build_camera_stop_classification(
        camera_availability,
        findings,
        worker_lifecycle_summary,
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    events_csv = output_dir / f"stability_events_{stamp}.csv"
    incidents_csv = output_dir / f"stability_camera_incidents_{stamp}.csv"
    findings_csv = output_dir / f"stability_log_findings_{stamp}.csv"
    worker_lifecycle_events_csv = output_dir / f"stability_worker_lifecycle_events_{stamp}.csv"
    worker_lifecycle_summary_csv = output_dir / f"stability_worker_lifecycle_summary_{stamp}.csv"
    camera_availability_csv = output_dir / f"stability_camera_availability_{stamp}.csv"
    camera_stop_classification_csv = output_dir / f"stability_camera_stop_classification_{stamp}.csv"
    resource_buckets_csv = output_dir / f"stability_resource_buckets_{stamp}.csv"
    report_md = output_dir / f"stability_summary_{stamp}.md"

    _csv_write(
        events_csv,
        events,
        [
            "id",
            "camera_id",
            "camera_name",
            "event_type",
            "started_at",
            "ended_at",
            "track_id",
            "detector_score",
            "confidence",
            "event_score",
            "severity",
            "status",
            "alarm_eligible",
            "lifecycle_action",
            "is_alarm_active",
            "snapshot_path",
            "clip_path",
        ],
    )

    incident_rows = []
    for incident in incidents:
        incident_rows.append(
            {
                "camera_id": "" if incident.camera_id is None else incident.camera_id,
                "camera_name": "global" if incident.camera_id is None else cameras.get(incident.camera_id, f"Camera {incident.camera_id}"),
                "started_at": _fmt_dt(incident.started_at),
                "last_signal_at": _fmt_dt(incident.last_signal_at),
                "duration_seconds": int((incident.last_signal_at - incident.started_at).total_seconds()),
                "category": incident.category,
                "cause": incident.cause,
                "signals": len(incident.findings),
                "common_cause_hint": incident.common_cause_hint,
                "first_action": incident.findings[0].action if incident.findings else "",
                "first_status": incident.findings[0].status if incident.findings else "",
                "first_reason": incident.findings[0].reason if incident.findings else "",
                "first_message": incident.findings[0].message if incident.findings else "",
            }
        )
    _csv_write(
        incidents_csv,
        incident_rows,
        [
            "camera_id",
            "camera_name",
            "started_at",
            "last_signal_at",
            "duration_seconds",
            "category",
            "cause",
            "signals",
            "common_cause_hint",
            "first_action",
            "first_status",
            "first_reason",
            "first_message",
        ],
    )

    finding_rows = [
        {
            "timestamp": _fmt_dt(item.timestamp),
            "camera_id": "" if item.camera_id is None else item.camera_id,
            "camera_name": "global" if item.camera_id is None else cameras.get(item.camera_id, f"Camera {item.camera_id}"),
            "level": item.level,
            "logger": item.logger,
            "action": item.action,
            "status": item.status,
            "reason": item.reason,
            "category": item.category,
            "cause": item.cause,
            "message": item.message,
        }
        for item in findings
    ]
    _csv_write(
        findings_csv,
        finding_rows,
        ["timestamp", "camera_id", "camera_name", "level", "logger", "action", "status", "reason", "category", "cause", "message"],
    )

    worker_lifecycle_event_rows = [
        {
            "timestamp": _fmt_dt(item.timestamp),
            "camera_id": "" if item.camera_id is None else item.camera_id,
            "camera_name": "global" if item.camera_id is None else cameras.get(item.camera_id, f"Camera {item.camera_id}"),
            "worker_pid": item.worker_pid,
            "kind": item.kind,
            "logger": item.logger,
            "action": item.action,
            "status": item.status,
            "reason": item.reason,
            "message": item.message,
        }
        for item in worker_lifecycle_events
    ]
    _csv_write(
        worker_lifecycle_events_csv,
        worker_lifecycle_event_rows,
        ["timestamp", "camera_id", "camera_name", "worker_pid", "kind", "logger", "action", "status", "reason", "message"],
    )

    worker_lifecycle_summary_rows = [
        {
            "camera_id": "" if item.camera_id is None else item.camera_id,
            "camera_name": "global" if item.camera_id is None else cameras.get(item.camera_id, f"Camera {item.camera_id}"),
            "starts": item.starts,
            "stops": item.stops,
            "manual_stops": item.manual_stops,
            "fatal_errors": item.fatal_errors,
            "process_exits": item.process_exits,
            "watchdog_restarts": item.watchdog_restarts,
            "watchdog_restart_successes": item.watchdog_restart_successes,
            "watchdog_restart_failures": item.watchdog_restart_failures,
            "open_sessions_at_window_end": item.open_sessions_at_window_end,
            "terminal_sessions_without_start_in_window": item.terminal_sessions_without_start_in_window,
            "last_start_at": _fmt_dt(item.last_start_at) if item.last_start_at else "",
            "last_terminal_at": _fmt_dt(item.last_terminal_at) if item.last_terminal_at else "",
        }
        for item in worker_lifecycle_summary
    ]
    _csv_write(
        worker_lifecycle_summary_csv,
        worker_lifecycle_summary_rows,
        [
            "camera_id",
            "camera_name",
            "starts",
            "stops",
            "manual_stops",
            "fatal_errors",
            "process_exits",
            "watchdog_restarts",
            "watchdog_restart_successes",
            "watchdog_restart_failures",
            "open_sessions_at_window_end",
            "terminal_sessions_without_start_in_window",
            "last_start_at",
            "last_terminal_at",
        ],
    )
    _csv_write(camera_availability_csv, camera_availability, AVAILABILITY_CSV_FIELDS)
    _csv_write(camera_stop_classification_csv, camera_stop_classification, STOP_CLASSIFICATION_CSV_FIELDS)
    _csv_write(
        resource_buckets_csv,
        resource_summary.get("buckets", []) if isinstance(resource_summary, dict) else [],
        RESOURCE_CSV_FIELDS,
    )

    markdown = build_markdown(
        since=since,
        until=until,
        events=events,
        findings=findings,
        incidents=incidents,
        worker_lifecycle_events=worker_lifecycle_events,
        worker_lifecycle_summary=worker_lifecycle_summary,
        resource_summary=resource_summary,
        camera_availability=camera_availability,
        camera_stop_classification=camera_stop_classification,
        cameras=cameras,
        csv_paths={
            "events": events_csv,
            "incidents": incidents_csv,
            "findings": findings_csv,
            "worker_lifecycle_events": worker_lifecycle_events_csv,
            "worker_lifecycle_summary": worker_lifecycle_summary_csv,
            "camera_availability": camera_availability_csv,
            "camera_stop_classification": camera_stop_classification_csv,
            "resource_buckets": resource_buckets_csv,
        },
    )
    report_md.write_text(markdown, encoding="utf-8")

    metadata = {
        "report": str(report_md),
        "events_csv": str(events_csv),
        "incidents_csv": str(incidents_csv),
        "findings_csv": str(findings_csv),
        "worker_lifecycle_events_csv": str(worker_lifecycle_events_csv),
        "worker_lifecycle_summary_csv": str(worker_lifecycle_summary_csv),
        "camera_availability_csv": str(camera_availability_csv),
        "camera_stop_classification_csv": str(camera_stop_classification_csv),
        "resource_buckets_csv": str(resource_buckets_csv),
        "since": _fmt_dt(since),
        "until": _fmt_dt(until),
        "events": len(events),
        "findings": len(findings),
        "incidents": len(incidents),
        "worker_lifecycle_events": len(worker_lifecycle_events),
        "worker_lifecycle_summaries": len(worker_lifecycle_summary),
        "resource_samples": int(((resource_summary.get("summary") or {}).get("samples") if isinstance(resource_summary, dict) else 0) or 0),
        "camera_availability_rows": len(camera_availability),
        "camera_stop_classification_rows": len(camera_stop_classification),
    }
    (output_dir / f"stability_manifest_{stamp}.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "report": report_md,
        "events": events_csv,
        "incidents": incidents_csv,
        "findings": findings_csv,
        "worker_lifecycle_events": worker_lifecycle_events_csv,
        "worker_lifecycle_summary": worker_lifecycle_summary_csv,
        "camera_availability": camera_availability_csv,
        "camera_stop_classification": camera_stop_classification_csv,
        "resource_buckets": resource_buckets_csv,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gera relatorio de estabilidade, eventos e quedas de cameras.")
    parser.add_argument("--hours", type=float, default=48.0, help="Quantidade de horas para olhar para tras quando --since nao for informado.")
    parser.add_argument("--since", default="", help="Inicio da janela. Ex: '2026-05-08 15:00:00'.")
    parser.add_argument("--until", default="", help="Fim da janela. Padrao: agora.")
    parser.add_argument("--database-url", default=_default_database_url(), help="URL sqlite do banco analytics.")
    parser.add_argument("--logs-dir", default=str(_default_logs_dir()), help="Pasta dos logs.")
    parser.add_argument("--output-dir", default=str(_default_base_dir() / "reports" / "stability"), help="Pasta de saida.")
    parser.add_argument("--incident-gap-minutes", type=int, default=5, help="Agrupa sinais da mesma camera dentro deste intervalo.")
    parser.add_argument("--common-window-minutes", type=int, default=3, help="Marca causa comum quando cameras caem proximas nesta janela.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = generate_report(args)
    print(f"Relatorio: {paths['report']}")
    print(f"Eventos CSV: {paths['events']}")
    print(f"Incidentes CSV: {paths['incidents']}")
    print(f"Sinais CSV: {paths['findings']}")
    print(f"Ciclo de vida dos workers CSV: {paths['worker_lifecycle_events']}")
    print(f"Resumo dos workers CSV: {paths['worker_lifecycle_summary']}")
    print(f"Disponibilidade por camera CSV: {paths['camera_availability']}")
    print(f"Reparticao de paradas CSV: {paths['camera_stop_classification']}")
    print(f"Recursos por bucket CSV: {paths['resource_buckets']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
