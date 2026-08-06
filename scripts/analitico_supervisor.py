#!/usr/bin/env python3
"""Supervisor externo do runtime Analitico.

Roda no host, fora do Docker. Em modo audit apenas registra as acoes que faria.
Em modo recover solicita reconciliacao ao runtime; reinicio de container e uma
opcao separada e desabilitada por padrao.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import signal
import smtplib
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - only exercised by Windows tests
    fcntl = None


RUNNING_STATES = {"running", "running_motion_test"}
TRANSITIONAL_STATES = {"starting", "warming_up", "reconnecting"}
ALERT_EVENTS = {
    "camera_quarantined",
    "canary_failed",
    "circuit_open",
    "configuration_drift",
    "runtime_unavailable",
}


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)


def env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "true" if default else "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def read_env_file(path: str | Path | None) -> dict[str, str]:
    if not path:
        return {}
    env_path = Path(path)
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def detect_configuration_drift(
    *,
    snapshot: dict[str, Any],
    gateway: dict[str, Any],
    compose_env_file: str | Path | None,
) -> list[dict[str, Any]]:
    expected = read_env_file(compose_env_file)
    runtime_tuning = snapshot.get("runtime_tuning") or {}
    checks = (
        (
            "GATEWAY_NODE_MAX_ACTIVE_CAMERAS",
            gateway.get("node_max_active_cameras"),
            "camera-gateway",
        ),
        (
            "ANALYTIC_GPU_MAX_ACTIVE_WORKERS",
            runtime_tuning.get("max_active_workers"),
            "server-analiticos-runtime",
        ),
        (
            "INFERENCE_POOL_COUNT",
            runtime_tuning.get("inference_pool_count"),
            "server-analiticos-runtime",
        ),
        (
            "INFERENCE_POOL_MAX_CAMERAS_PER_POOL",
            runtime_tuning.get("inference_pool_max_cameras_per_pool"),
            "server-analiticos-runtime",
        ),
    )
    drift: list[dict[str, Any]] = []
    for key, actual, component in checks:
        configured = expected.get(key)
        if configured in {None, ""} or actual is None:
            continue
        try:
            matches = int(configured) == int(actual)
        except (TypeError, ValueError):
            matches = str(configured) == str(actual)
        if not matches:
            drift.append({
                "key": key,
                "expected": configured,
                "actual": actual,
                "component": component,
            })
    return drift


def redact_sensitive_text(value: str) -> str:
    text = re.sub(r"(rtsp://[^:\s/@]+:)[^@\s]+@", r"\1***@", value, flags=re.I)
    text = re.sub(
        r"(?i)(password|passwd|token|secret|client_secret)([=:\"'\s]+)([^\s,;\"']+)",
        r"\1\2***",
        text,
    )
    return text


def request_json(
    url: str,
    *,
    token: str = "",
    method: str = "GET",
    timeout: float = 5.0,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Analitico-Supervisor-Token"] = token
    data = b"" if method != "GET" else None
    request = urllib.request.Request(url, headers=headers, data=data, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


@dataclass(slots=True)
class CameraDecision:
    camera_id: int
    healthy: bool
    reason: str
    force_restart: bool = False
    recoverable: bool = True


def evaluate_camera(camera: dict[str, Any], *, stale_seconds: float, now: float | None = None) -> CameraDecision:
    camera_id = int(camera["camera_id"])
    desired = bool(camera.get("desired_running"))
    worker = camera.get("worker") or None
    health = str(camera.get("health_status") or "")

    if not desired:
        if worker and worker.get("alive"):
            return CameraDecision(camera_id, False, "worker_running_while_desired_stopped")
        return CameraDecision(camera_id, True, "desired_stopped")

    if not worker:
        return CameraDecision(camera_id, False, "worker_missing")
    if not bool(worker.get("alive")):
        return CameraDecision(camera_id, False, "worker_dead")
    if camera.get("ownership_matches") is False:
        return CameraDecision(camera_id, False, "worker_ownership_mismatch", force_restart=True)

    started_at = worker.get("started_at")
    if started_at is not None and now is not None:
        try:
            if now - float(started_at) < min(120.0, stale_seconds):
                return CameraDecision(camera_id, True, "startup_grace")
        except Exception:
            pass

    activity_age = camera.get("latest_activity_age_seconds")
    if activity_age is not None:
        try:
            if float(activity_age) > stale_seconds:
                return CameraDecision(camera_id, False, "activity_stale", force_restart=True)
        except Exception:
            pass

    gateway_state = str(camera.get("gateway_state") or "").strip().lower()
    if gateway_state == "queued" and health in TRANSITIONAL_STATES:
        return CameraDecision(
            camera_id,
            False,
            "gateway_queued",
            force_restart=False,
            recoverable=False,
        )

    if health.startswith("error:"):
        return CameraDecision(camera_id, False, "worker_error_status", force_restart=True)
    if health not in RUNNING_STATES:
        return CameraDecision(camera_id, False, f"health_{health or 'unknown'}", force_restart=True)
    return CameraDecision(camera_id, True, "healthy")


@dataclass
class CameraRuntimeState:
    failures: int = 0
    last_reason: str = ""
    last_action_at: float = 0.0
    actions: collections.deque[float] = field(default_factory=collections.deque)
    recovery_attempts: int = 0
    healthy_cycles: int = 0
    quarantine_until: float = 0.0
    last_log_at: float = 0.0
    last_log_key: str = ""

    def prune(self, now: float) -> None:
        while self.actions and now - self.actions[0] > 3600.0:
            self.actions.popleft()

    def as_dict(self) -> dict[str, Any]:
        return {
            "failures": self.failures,
            "last_reason": self.last_reason,
            "last_action_at": self.last_action_at,
            "actions": list(self.actions),
            "recovery_attempts": self.recovery_attempts,
            "healthy_cycles": self.healthy_cycles,
            "quarantine_until": self.quarantine_until,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CameraRuntimeState":
        state = cls(
            failures=int(payload.get("failures") or 0),
            last_reason=str(payload.get("last_reason") or ""),
            last_action_at=float(payload.get("last_action_at") or 0.0),
            recovery_attempts=int(payload.get("recovery_attempts") or 0),
            healthy_cycles=int(payload.get("healthy_cycles") or 0),
            quarantine_until=float(payload.get("quarantine_until") or 0.0),
        )
        for value in payload.get("actions") or []:
            try:
                state.actions.append(float(value))
            except (TypeError, ValueError):
                continue
        return state


class AnaliticoSupervisor:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.state_dir = Path(args.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.state_dir / "events.jsonl"
        self.status_path = self.state_dir / "status.json"
        self.recovery_state_path = self.state_dir / "recovery_state.json"
        self.incidents_dir = self.state_dir / "incidents"
        self.prometheus_path = Path(args.prometheus_file) if args.prometheus_file else None
        self.camera_states: dict[int, CameraRuntimeState] = {}
        self.runtime_failures = 0
        self.runtime_restart_times: collections.deque[float] = collections.deque()
        self.recovery_total = 0
        self.audit_action_total = 0
        self.circuit_open = False
        self.config_drift: list[dict[str, Any]] = []
        self.canary_status: dict[str, Any] = {"enabled": bool(args.canary_enabled), "ok": None}
        self._last_canary_at = 0.0
        self._last_circuit_open = False
        self._last_circuit_log_at = 0.0
        self._last_drift_key = ""
        self._last_orphans: tuple[int, ...] = ()
        self._orphan_cycles = 0
        self._last_orphan_action_at = 0.0
        self._last_notifications: dict[str, float] = {}
        self._stop = False
        self._lock_handle = None
        self.load_recovery_state()

    def load_recovery_state(self) -> None:
        try:
            payload = json.loads(self.recovery_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        states = payload.get("cameras") if isinstance(payload, dict) else None
        self._last_circuit_open = bool(payload.get("circuit_open", False)) if isinstance(payload, dict) else False
        if not isinstance(states, dict):
            return
        for camera_id, raw_state in states.items():
            if not isinstance(raw_state, dict):
                continue
            try:
                self.camera_states[int(camera_id)] = CameraRuntimeState.from_dict(raw_state)
            except (TypeError, ValueError):
                continue

    def save_recovery_state(self) -> None:
        atomic_json_write(
            self.recovery_state_path,
            {
                "generated_at": utc_iso(),
                "circuit_open": self.circuit_open,
                "cameras": {
                    str(camera_id): state.as_dict()
                    for camera_id, state in self.camera_states.items()
                },
            },
        )

    def acquire_lock(self) -> None:
        lock_path = self.state_dir / "supervisor.lock"
        self._lock_handle = lock_path.open("w", encoding="utf-8")
        if fcntl is not None:
            try:
                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Outra instancia do supervisor ja esta ativa") from exc
        self._lock_handle.write(str(os.getpid()))
        self._lock_handle.flush()

    def stop(self, *_args) -> None:
        self._stop = True

    def log_event(self, event: str, **fields: Any) -> None:
        payload = {"ts": utc_iso(), "event": event, "mode": self.args.mode, **fields}
        append_jsonl(self.events_path, payload)
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        if event in ALERT_EVENTS:
            self.send_webhook(payload)
            self.send_email(payload)

    def send_webhook(self, payload: dict[str, Any]) -> None:
        url = str(self.args.webhook_url or "").strip()
        if not url:
            return
        event = str(payload.get("event") or "event")
        now = time.time()
        notification_key = f"webhook:{event}"
        if now - self._last_notifications.get(notification_key, 0.0) < self.args.webhook_cooldown_seconds:
            return
        self._last_notifications[notification_key] = now
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.args.timeout) as response:
                response.read(1024)
        except Exception as exc:
            failure = {
                "ts": utc_iso(),
                "event": "webhook_failed",
                "mode": self.args.mode,
                "source_event": event,
                "error": f"{type(exc).__name__}: {exc}",
            }
            append_jsonl(self.events_path, failure)
            print(json.dumps(failure, ensure_ascii=False), flush=True)

    def send_email(self, payload: dict[str, Any]) -> None:
        host = str(self.args.smtp_host or "").strip()
        recipients = [
            item.strip()
            for item in str(self.args.smtp_to or "").split(",")
            if item.strip()
        ]
        sender = str(self.args.smtp_from or self.args.smtp_username or "").strip()
        if not host or not recipients or not sender:
            return
        event = str(payload.get("event") or "event")
        now = time.time()
        notification_key = f"email:{event}"
        if now - self._last_notifications.get(notification_key, 0.0) < self.args.smtp_cooldown_seconds:
            return
        self._last_notifications[notification_key] = now

        message = EmailMessage()
        message["Subject"] = f"[SunOrus] Supervisor: {event}"
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message.set_content(redact_sensitive_text(json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=str,
        )))
        smtp_class = smtplib.SMTP_SSL if self.args.smtp_ssl else smtplib.SMTP
        smtp_kwargs: dict[str, Any] = {
            "host": host,
            "port": self.args.smtp_port,
            "timeout": self.args.timeout,
        }
        if self.args.smtp_ssl:
            smtp_kwargs["context"] = ssl.create_default_context()
        try:
            with smtp_class(**smtp_kwargs) as client:
                if self.args.smtp_starttls and not self.args.smtp_ssl:
                    client.starttls(context=ssl.create_default_context())
                if self.args.smtp_username:
                    client.login(self.args.smtp_username, self.args.smtp_password)
                client.send_message(message)
        except Exception as exc:
            failure = {
                "ts": utc_iso(),
                "event": "email_failed",
                "mode": self.args.mode,
                "source_event": event,
                "error": f"{type(exc).__name__}: {exc}",
            }
            append_jsonl(self.events_path, failure)
            print(json.dumps(failure, ensure_ascii=False), flush=True)

    def fetch_gateway(self) -> tuple[bool, dict[str, Any]]:
        try:
            payload = request_json(self.args.gateway_url, timeout=self.args.timeout)
            return bool(payload.get("ok")), payload
        except Exception as exc:
            return False, {"error": f"{type(exc).__name__}: {exc}"}

    def fetch_snapshot(self) -> dict[str, Any]:
        url = self.args.runtime_url.rstrip("/") + "/internal/supervisor/snapshot"
        return request_json(url, token=self.args.token, timeout=self.args.timeout)

    def reconcile_camera(self, decision: CameraDecision) -> dict[str, Any]:
        query = urllib.parse.urlencode({
            "recover": "true",
            "force_restart": "true" if decision.force_restart else "false",
        })
        url = (
            self.args.runtime_url.rstrip("/")
            + f"/internal/supervisor/cameras/{decision.camera_id}/reconcile?{query}"
        )
        return request_json(url, token=self.args.token, method="POST", timeout=max(10.0, self.args.timeout))

    def reconcile_gateway(self, *, recover: bool) -> dict[str, Any]:
        query = urllib.parse.urlencode({"recover": "true" if recover else "false"})
        url = self.args.runtime_url.rstrip("/") + f"/internal/supervisor/gateway/reconcile?{query}"
        return request_json(url, token=self.args.token, method="POST", timeout=max(10.0, self.args.timeout))

    def run_canary(self) -> dict[str, Any]:
        url = self.args.runtime_url.rstrip("/") + "/internal/supervisor/canary"
        return request_json(url, token=self.args.token, method="POST", timeout=max(15.0, self.args.timeout))

    def action_allowed(self, state: CameraRuntimeState, now: float) -> bool:
        state.prune(now)
        backoff_multiplier = 2 ** max(0, state.recovery_attempts - 1)
        cooldown = min(
            self.args.max_backoff_seconds,
            self.args.cooldown_seconds * backoff_multiplier,
        )
        if now - state.last_action_at < cooldown:
            return False
        return len(state.actions) < self.args.max_actions_per_hour

    def log_camera_event_limited(
        self,
        state: CameraRuntimeState,
        event: str,
        *,
        now: float,
        minimum_interval: float = 60.0,
        **fields: Any,
    ) -> None:
        key = f"{event}:{fields.get('reason', '')}:{fields.get('cause', '')}"
        if state.last_log_key == key and now - state.last_log_at < minimum_interval:
            return
        state.last_log_key = key
        state.last_log_at = now
        self.log_event(event, **fields)

    def handle_decision(self, decision: CameraDecision, *, now: float, actions_enabled: bool) -> None:
        state = self.camera_states.setdefault(decision.camera_id, CameraRuntimeState())
        if decision.healthy:
            state.failures = 0
            state.last_reason = decision.reason
            state.healthy_cycles += 1
            if state.healthy_cycles >= self.args.healthy_reset_cycles:
                state.recovery_attempts = 0
                state.quarantine_until = 0.0
            return


        state.healthy_cycles = 0

        if state.last_reason == decision.reason:
            state.failures += 1
        else:
            state.failures = 1
            state.last_reason = decision.reason

        if state.quarantine_until > now:
            self.log_camera_event_limited(
                state,
                "camera_action_suppressed",
                now=now,
                camera_id=decision.camera_id,
                reason=decision.reason,
                failures=state.failures,
                cause="quarantine",
                quarantine_until=datetime.fromtimestamp(
                    state.quarantine_until,
                    tz=timezone.utc,
                ).isoformat(),
            )
            return
        if state.quarantine_until:
            state.quarantine_until = 0.0
            state.recovery_attempts = 0
            self.log_event("camera_quarantine_expired", camera_id=decision.camera_id)

        if state.failures < self.args.failure_threshold:
            return
        if not decision.recoverable:
            self.log_camera_event_limited(
                state,
                "camera_blocked_upstream",
                now=now,
                camera_id=decision.camera_id,
                reason=decision.reason,
                failures=state.failures,
            )
            return
        if not self.action_allowed(state, now):
            self.log_camera_event_limited(
                state,
                "camera_action_suppressed",
                now=now,
                camera_id=decision.camera_id,
                reason=decision.reason,
                failures=state.failures,
                cause="cooldown_or_budget",
            )
            return

        state.last_action_at = now
        state.actions.append(now)
        state.recovery_attempts += 1
        if state.recovery_attempts >= self.args.quarantine_after_actions:
            state.quarantine_until = now + self.args.quarantine_seconds
            self.log_event(
                "camera_quarantined",
                camera_id=decision.camera_id,
                reason=decision.reason,
                failures=state.failures,
                recovery_attempts=state.recovery_attempts,
                quarantine_seconds=self.args.quarantine_seconds,
                simulated=self.args.mode == "audit" or not actions_enabled,
            )
            return
        if not actions_enabled or self.args.mode == "audit":
            self.audit_action_total += 1
            self.log_event(
                "camera_would_reconcile",
                camera_id=decision.camera_id,
                reason=decision.reason,
                force_restart=decision.force_restart,
                failures=state.failures,
                circuit_open=self.circuit_open,
            )
            return

        try:
            result = self.reconcile_camera(decision)
            self.recovery_total += 1
            state.failures = 0
            self.log_event(
                "camera_reconciled",
                camera_id=decision.camera_id,
                reason=decision.reason,
                result=result,
            )
        except Exception as exc:
            self.log_event(
                "camera_reconcile_failed",
                camera_id=decision.camera_id,
                reason=decision.reason,
                error=f"{type(exc).__name__}: {exc}",
            )

    def maybe_restart_runtime(self, now: float, error: str) -> None:
        if self.args.mode != "recover" or not self.args.allow_runtime_restart:
            self.log_event("runtime_unavailable", failures=self.runtime_failures, error=error)
            return
        if self.runtime_failures < self.args.runtime_restart_threshold:
            return
        while self.runtime_restart_times and now - self.runtime_restart_times[0] > 3600.0:
            self.runtime_restart_times.popleft()
        if len(self.runtime_restart_times) >= self.args.max_runtime_restarts_per_hour:
            self.log_event("runtime_restart_suppressed", cause="hourly_budget", error=error)
            return

        env = dict(os.environ)
        env["DOCKER_HOST"] = self.args.docker_host
        command = ["docker", "restart", self.args.runtime_container]
        completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120, check=False)
        self.runtime_restart_times.append(now)
        self.log_event(
            "runtime_restart",
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
        self.runtime_failures = 0

    def diagnostic_command(self, command: list[str], *, timeout: float = 20.0) -> str:
        env = dict(os.environ)
        env["DOCKER_HOST"] = self.args.docker_host
        try:
            completed = subprocess.run(
                command,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
        output = completed.stdout
        if completed.stderr:
            output += "\n[stderr]\n" + completed.stderr
        return redact_sensitive_text(output[-250_000:])

    def capture_incident(
        self,
        *,
        reason: str,
        snapshot: dict[str, Any] | None,
        gateway: dict[str, Any],
    ) -> str | None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        incident_dir = self.incidents_dir / f"{stamp}_{reason}"
        try:
            incident_dir.mkdir(parents=True, exist_ok=False)
            atomic_json_write(
                incident_dir / "snapshot.json",
                {
                    "generated_at": utc_iso(),
                    "reason": reason,
                    "gateway": gateway,
                    "runtime": snapshot,
                    "configuration_drift": self.config_drift,
                    "canary": self.canary_status,
                },
            )
            commands = {
                "docker_stats.txt": [
                    "docker", "stats", "--no-stream",
                    "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}",
                ],
                "nvidia_smi.txt": [
                    "nvidia-smi",
                    "--query-gpu=timestamp,name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw",
                    "--format=csv,noheader",
                ],
                "disk.txt": ["df", "-h", "/", "/mnt/analitico_ssd"],
                "runtime_logs.txt": ["docker", "logs", "--since=10m", self.args.runtime_container],
                "gateway_logs.txt": ["docker", "logs", "--since=10m", "camera-gateway"],
            }
            for filename, command in commands.items():
                (incident_dir / filename).write_text(
                    self.diagnostic_command(command),
                    encoding="utf-8",
                )
            archive_path = incident_dir.with_suffix(".tar.gz")
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(incident_dir, arcname=incident_dir.name)
            shutil.rmtree(incident_dir)
            archives = sorted(
                self.incidents_dir.glob("*.tar.gz"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            retention_count = max(1, int(self.args.incident_retention_count))
            for stale_archive in archives[retention_count:]:
                stale_archive.unlink(missing_ok=True)
            self.log_event(
                "incident_bundle_created",
                reason=reason,
                path=str(archive_path),
            )
            return str(archive_path)
        except Exception as exc:
            self.log_event(
                "incident_bundle_failed",
                reason=reason,
                error=f"{type(exc).__name__}: {exc}",
            )
            return None

    def update_canary(self, now: float) -> None:
        if not self.args.canary_enabled:
            return
        if now - self._last_canary_at < self.args.canary_interval_seconds:
            return
        self._last_canary_at = now
        try:
            result = self.run_canary()
            self.canary_status = {
                "enabled": True,
                "ok": bool(result.get("ok")),
                "checked_at": utc_iso(),
                **result,
            }
            if not self.canary_status["ok"]:
                self.log_event("canary_failed", result=result)
        except Exception as exc:
            self.canary_status = {
                "enabled": True,
                "ok": False,
                "checked_at": utc_iso(),
                "error": f"{type(exc).__name__}: {exc}",
            }
            self.log_event("canary_failed", error=self.canary_status["error"])

    def handle_gateway_orphans(self, snapshot: dict[str, Any], *, gateway_ok: bool) -> None:
        orphan_ids = tuple(
            sorted(
                int(value)
                for value in ((snapshot.get("gateway") or {}).get("orphan_camera_ids") or [])
            )
        )
        if orphan_ids == self._last_orphans and orphan_ids:
            self._orphan_cycles += 1
        elif orphan_ids:
            self._orphan_cycles = 1
        else:
            self._orphan_cycles = 0
        changed = orphan_ids != self._last_orphans
        self._last_orphans = orphan_ids
        if changed and orphan_ids:
            self.log_event("gateway_orphans_detected", camera_ids=list(orphan_ids))
        if not orphan_ids or self._orphan_cycles < self.args.orphan_failure_threshold:
            return
        if len(orphan_ids) > self.args.max_orphan_cleanup_per_cycle:
            if changed or self._orphan_cycles == self.args.orphan_failure_threshold:
                self.log_event(
                    "gateway_orphan_cleanup_suppressed",
                    camera_ids=list(orphan_ids),
                    cause="safety_limit",
                )
            return
        if self.args.mode == "audit":
            if changed or self._orphan_cycles == self.args.orphan_failure_threshold:
                self.log_event("gateway_would_cleanup", camera_ids=list(orphan_ids))
            return
        if not gateway_ok:
            return
        now = time.time()
        if now - self._last_orphan_action_at < self.args.orphan_cleanup_cooldown_seconds:
            return
        self._last_orphan_action_at = now
        try:
            result = self.reconcile_gateway(recover=True)
            self.log_event("gateway_orphans_reconciled", result=result)
            self._orphan_cycles = 0
        except Exception as exc:
            self.log_event(
                "gateway_orphan_cleanup_failed",
                camera_ids=list(orphan_ids),
                error=f"{type(exc).__name__}: {exc}",
            )

    def write_status(
        self,
        *,
        snapshot: dict[str, Any] | None,
        gateway_ok: bool,
        unhealthy: int,
        desired: int,
        error: str | None = None,
    ) -> None:
        payload = {
            "generated_at": utc_iso(),
            "pid": os.getpid(),
            "mode": self.args.mode,
            "runtime_ok": snapshot is not None,
            "runtime_error": error,
            "gateway_ok": gateway_ok,
            "circuit_open": self.circuit_open,
            "desired_workers": desired,
            "unhealthy_workers": unhealthy,
            "recovery_total": self.recovery_total,
            "audit_action_total": self.audit_action_total,
            "runtime_failures": self.runtime_failures,
            "configuration_drift": self.config_drift,
            "canary": self.canary_status,
            "quarantined_cameras": [
                camera_id
                for camera_id, state in self.camera_states.items()
                if state.quarantine_until > time.time()
            ],
            "snapshot_summary": (snapshot or {}).get("summary", {}),
        }
        atomic_json_write(self.status_path, payload)
        self.write_prometheus(payload)

    def write_prometheus(self, status: dict[str, Any]) -> None:
        if self.prometheus_path is None:
            return
        values = {
            "analitico_supervisor_up": 1,
            "analitico_supervisor_runtime_ok": int(bool(status["runtime_ok"])),
            "analitico_supervisor_gateway_ok": int(bool(status["gateway_ok"])),
            "analitico_supervisor_circuit_open": int(bool(status["circuit_open"])),
            "analitico_supervisor_desired_workers": int(status["desired_workers"]),
            "analitico_supervisor_unhealthy_workers": int(status["unhealthy_workers"]),
            "analitico_supervisor_recovery_total": int(status["recovery_total"]),
            "analitico_supervisor_audit_action_total": int(status["audit_action_total"]),
            "analitico_supervisor_configuration_drift": len(status.get("configuration_drift") or []),
            "analitico_supervisor_canary_ok": int((status.get("canary") or {}).get("ok") is True),
            "analitico_supervisor_quarantined_cameras": len(status.get("quarantined_cameras") or []),
        }
        content = "\n".join(f"{key} {value}" for key, value in values.items()) + "\n"
        self.prometheus_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.prometheus_path.with_suffix(self.prometheus_path.suffix + ".tmp")
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, self.prometheus_path)

    def run_once(self) -> None:
        now = time.time()
        gateway_ok, gateway = self.fetch_gateway()
        try:
            snapshot = self.fetch_snapshot()
        except Exception as exc:
            self.runtime_failures += 1
            error = f"{type(exc).__name__}: {exc}"
            self.maybe_restart_runtime(now, error)
            self.circuit_open = True
            self.write_status(snapshot=None, gateway_ok=gateway_ok, unhealthy=0, desired=0, error=error)
            if not self._last_circuit_open:
                self.capture_incident(
                    reason="runtime_unavailable",
                    snapshot=None,
                    gateway=gateway,
                )
            self._last_circuit_open = True
            self.save_recovery_state()
            return

        self.runtime_failures = 0
        self.update_canary(now)
        self.config_drift = detect_configuration_drift(
            snapshot=snapshot,
            gateway=gateway,
            compose_env_file=self.args.compose_env_file,
        )
        drift_key = json.dumps(self.config_drift, sort_keys=True, default=str)
        if drift_key != self._last_drift_key:
            self._last_drift_key = drift_key
            if self.config_drift:
                self.log_event("configuration_drift", differences=self.config_drift)
            else:
                self.log_event("configuration_drift_cleared")
        self.handle_gateway_orphans(snapshot, gateway_ok=gateway_ok)
        cameras = list(snapshot.get("cameras") or [])
        decisions = [
            evaluate_camera(camera, stale_seconds=self.args.stale_seconds, now=now)
            for camera in cameras
        ]
        desired_decisions = [
            decision
            for decision, camera in zip(decisions, cameras)
            if bool(camera.get("desired_running"))
        ]
        unhealthy = sum(1 for decision in desired_decisions if not decision.healthy)
        desired = len(desired_decisions)
        unhealthy_ratio = (unhealthy / desired) if desired else 0.0
        runtime_ready = bool((snapshot.get("runtime") or {}).get("ready"))
        self.circuit_open = (
            not gateway_ok
            or not runtime_ready
            or bool(self.config_drift)
            or (self.args.canary_enabled and self.canary_status.get("ok") is False)
            or unhealthy_ratio >= self.args.broad_failure_ratio
        )
        actions_enabled = not self.circuit_open

        for decision in decisions:
            self.handle_decision(decision, now=now, actions_enabled=actions_enabled)

        self.write_status(
            snapshot=snapshot,
            gateway_ok=gateway_ok,
            unhealthy=unhealthy,
            desired=desired,
        )
        if self.circuit_open:
            if (
                not self._last_circuit_open
                or now - self._last_circuit_log_at >= self.args.circuit_log_interval_seconds
            ):
                self._last_circuit_log_at = now
                self.log_event(
                    "circuit_open",
                    gateway_ok=gateway_ok,
                    runtime_ready=runtime_ready,
                    unhealthy=unhealthy,
                    desired=desired,
                    unhealthy_ratio=round(unhealthy_ratio, 4),
                    gateway_summary={
                        "running": gateway.get("running"),
                        "queued": gateway.get("queued"),
                        "node_active_cameras": gateway.get("node_active_cameras"),
                        "node_max_active_cameras": gateway.get("node_max_active_cameras"),
                        "total_start_failures": gateway.get("total_start_failures"),
                    },
                    configuration_drift=self.config_drift,
                    canary_ok=self.canary_status.get("ok"),
                )
            if not self._last_circuit_open:
                self.capture_incident(
                    reason="circuit_open",
                    snapshot=snapshot,
                    gateway=gateway,
                )
        elif self._last_circuit_open:
            self.log_event("circuit_closed")
        self._last_circuit_open = self.circuit_open
        self.save_recovery_state()

    def run(self) -> None:
        self.acquire_lock()
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        self.log_event("supervisor_started", pid=os.getpid())
        while not self._stop:
            started = time.monotonic()
            try:
                self.run_once()
            except Exception as exc:
                self.log_event("supervisor_cycle_failed", error=f"{type(exc).__name__}: {exc}")
            if self.args.once:
                break
            elapsed = time.monotonic() - started
            time.sleep(max(0.2, self.args.interval_seconds - elapsed))
        self.log_event("supervisor_stopped", pid=os.getpid())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervisor externo do Analitico VMS")
    parser.add_argument("--mode", choices=("audit", "recover"), default=os.getenv("ANALITICO_SUPERVISOR_MODE", "audit"))
    parser.add_argument("--runtime-url", default=os.getenv("ANALITICO_SUPERVISOR_RUNTIME_URL", "http://127.0.0.1:8001"))
    parser.add_argument("--gateway-url", default=os.getenv("ANALITICO_SUPERVISOR_GATEWAY_URL", "http://127.0.0.1:8090/healthz"))
    parser.add_argument("--token", default=os.getenv("SUPERVISOR_API_TOKEN", ""))
    parser.add_argument("--state-dir", default=os.getenv("ANALITICO_SUPERVISOR_STATE_DIR", "/mnt/analitico_ssd/supervisor"))
    parser.add_argument("--prometheus-file", default=os.getenv("ANALITICO_SUPERVISOR_PROMETHEUS_FILE", ""))
    parser.add_argument(
        "--compose-env-file",
        default=os.getenv(
            "ANALITICO_SUPERVISOR_COMPOSE_ENV_FILE",
            "/mnt/analitico_ssd/Analitico_Go_V4/.env.docker",
        ),
    )
    parser.add_argument("--interval-seconds", type=float, default=env_float("ANALITICO_SUPERVISOR_INTERVAL_SECONDS", 10.0))
    parser.add_argument("--timeout", type=float, default=env_float("ANALITICO_SUPERVISOR_TIMEOUT_SECONDS", 5.0))
    parser.add_argument("--stale-seconds", type=float, default=env_float("ANALITICO_SUPERVISOR_STALE_SECONDS", 90.0))
    parser.add_argument("--failure-threshold", type=int, default=env_int("ANALITICO_SUPERVISOR_FAILURE_THRESHOLD", 2))
    parser.add_argument("--cooldown-seconds", type=float, default=env_float("ANALITICO_SUPERVISOR_COOLDOWN_SECONDS", 60.0))
    parser.add_argument("--max-actions-per-hour", type=int, default=env_int("ANALITICO_SUPERVISOR_MAX_ACTIONS_PER_HOUR", 3))
    parser.add_argument("--max-backoff-seconds", type=float, default=env_float("ANALITICO_SUPERVISOR_MAX_BACKOFF_SECONDS", 900.0))
    parser.add_argument("--healthy-reset-cycles", type=int, default=env_int("ANALITICO_SUPERVISOR_HEALTHY_RESET_CYCLES", 6))
    parser.add_argument("--quarantine-after-actions", type=int, default=env_int("ANALITICO_SUPERVISOR_QUARANTINE_AFTER_ACTIONS", 3))
    parser.add_argument("--quarantine-seconds", type=float, default=env_float("ANALITICO_SUPERVISOR_QUARANTINE_SECONDS", 900.0))
    parser.add_argument("--broad-failure-ratio", type=float, default=env_float("ANALITICO_SUPERVISOR_BROAD_FAILURE_RATIO", 0.30))
    parser.add_argument("--orphan-failure-threshold", type=int, default=env_int("ANALITICO_SUPERVISOR_ORPHAN_FAILURE_THRESHOLD", 3))
    parser.add_argument("--max-orphan-cleanup-per-cycle", type=int, default=env_int("ANALITICO_SUPERVISOR_MAX_ORPHAN_CLEANUP_PER_CYCLE", 8))
    parser.add_argument("--orphan-cleanup-cooldown-seconds", type=float, default=env_float("ANALITICO_SUPERVISOR_ORPHAN_CLEANUP_COOLDOWN_SECONDS", 300.0))
    parser.add_argument("--circuit-log-interval-seconds", type=float, default=env_float("ANALITICO_SUPERVISOR_CIRCUIT_LOG_INTERVAL_SECONDS", 300.0))
    parser.add_argument(
        "--canary-enabled",
        action=argparse.BooleanOptionalAction,
        default=env_bool("ANALITICO_SUPERVISOR_CANARY_ENABLED", False),
    )
    parser.add_argument("--canary-interval-seconds", type=float, default=env_float("ANALITICO_SUPERVISOR_CANARY_INTERVAL_SECONDS", 300.0))
    parser.add_argument("--webhook-url", default=os.getenv("ANALITICO_SUPERVISOR_WEBHOOK_URL", ""))
    parser.add_argument("--webhook-cooldown-seconds", type=float, default=env_float("ANALITICO_SUPERVISOR_WEBHOOK_COOLDOWN_SECONDS", 300.0))
    parser.add_argument("--incident-retention-count", type=int, default=env_int("ANALITICO_SUPERVISOR_INCIDENT_RETENTION_COUNT", 20))
    parser.add_argument("--smtp-host", default=os.getenv("ANALITICO_SUPERVISOR_SMTP_HOST", ""))
    parser.add_argument("--smtp-port", type=int, default=env_int("ANALITICO_SUPERVISOR_SMTP_PORT", 587))
    parser.add_argument("--smtp-username", default=os.getenv("ANALITICO_SUPERVISOR_SMTP_USERNAME", ""))
    parser.add_argument("--smtp-password", default=os.getenv("ANALITICO_SUPERVISOR_SMTP_PASSWORD", ""))
    parser.add_argument("--smtp-from", default=os.getenv("ANALITICO_SUPERVISOR_SMTP_FROM", ""))
    parser.add_argument("--smtp-to", default=os.getenv("ANALITICO_SUPERVISOR_SMTP_TO", ""))
    parser.add_argument(
        "--smtp-starttls",
        action=argparse.BooleanOptionalAction,
        default=env_bool("ANALITICO_SUPERVISOR_SMTP_STARTTLS", True),
    )
    parser.add_argument(
        "--smtp-ssl",
        action=argparse.BooleanOptionalAction,
        default=env_bool("ANALITICO_SUPERVISOR_SMTP_SSL", False),
    )
    parser.add_argument("--smtp-cooldown-seconds", type=float, default=env_float("ANALITICO_SUPERVISOR_SMTP_COOLDOWN_SECONDS", 300.0))
    parser.add_argument(
        "--allow-runtime-restart",
        action=argparse.BooleanOptionalAction,
        default=env_bool("ANALITICO_SUPERVISOR_ALLOW_RUNTIME_RESTART", False),
    )
    parser.add_argument("--runtime-restart-threshold", type=int, default=env_int("ANALITICO_SUPERVISOR_RUNTIME_RESTART_THRESHOLD", 6))
    parser.add_argument("--max-runtime-restarts-per-hour", type=int, default=env_int("ANALITICO_SUPERVISOR_MAX_RUNTIME_RESTARTS_PER_HOUR", 2))
    parser.add_argument("--docker-host", default=os.getenv("DOCKER_HOST", "unix:///mnt/analitico_ssd/docker-analitico/docker.sock"))
    parser.add_argument("--runtime-container", default="server-analiticos-runtime")
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        AnaliticoSupervisor(args).run()
    except Exception as exc:
        print(f"Supervisor failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
