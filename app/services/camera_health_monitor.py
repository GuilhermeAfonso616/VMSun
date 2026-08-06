"""Monitora o estado das cameras e reconcilia workers no startup.

Este servico observa frame, inferencia e status de processo para manter a UI e
o backend alinhados com o estado real de cada camera.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.db.models import Camera
from app.services.camera_registry import registry
from app.services.frame_store import frame_store
from app.core.timezone import utc_now_naive as _shared_utc_now_naive
from app.services.metrics_store import metrics_store
from app.services.operational_history_store import operational_history_store
from app.services.resource_history_store import resource_history_store
from app.services.camera_gateway_client import fetch_camera_status, gateway_is_enabled, stop_camera_source
from app.services.stream_topology import resolve_stream_url
from app.services.reconnect_policy import (
    HEALTH_DEGRADED,
    HEALTH_OFFLINE,
    HEALTH_RECONNECTING,
    HEALTH_RUNNING,
    HEALTH_STARTING,
    HEALTH_STOPPED,
    HEALTH_WARMING_UP,
    RUNNING_STATES,
    REASON_UNKNOWN_ERROR,
    normalize_reason,
)
from app.services.worker_lifecycle import WorkerLifecycleError, WorkerStartBlocked, worker_lifecycle_manager


MAX_DEFERRED_STALE_SECONDS = 120.0
MAX_STALL_CHECKS_BEFORE_FORCE_RESTART = 20
WATCHDOG_RESTART_COOLDOWN_SECONDS = 60.0
WATCHDOG_INACTIVE_STATUSES = {"disabled", "inactive", "deleted", "archived", "stopped_manual"}
STARTUP_INACTIVE_STATUSES = {"disabled", "inactive", "deleted", "archived", "stopped_manual"}
STARTUP_RUNNING_STATUSES = {"running", "running_motion_test"}


def _startup_reconcile_action(camera, *, restore_running_workers: bool) -> str:
    current = str(getattr(camera, "status", None) or "idle")
    auto_start_enabled = bool(getattr(camera, "auto_start_enabled", False))

    if current in STARTUP_INACTIVE_STATUSES:
        return "skip"
    if auto_start_enabled:
        return "restore"
    if current in STARTUP_RUNNING_STATUSES:
        return "restore" if restore_running_workers else "normalize"
    return "skip"


def _utc_now_naive() -> datetime:
    return _shared_utc_now_naive()


def _parse_iso_datetime(raw_value) -> datetime | None:
    if not raw_value:
        return None

    text = str(raw_value).strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)

    return parsed


def _max_dt(*values: datetime | None) -> datetime | None:
    candidates = [value for value in values if value is not None]
    if not candidates:
        return None
    return max(candidates)


def _health_status_class(health_status: str) -> str:
    if health_status in {"running", "running_motion_test"}:
        return "monitor-running"
    if health_status in {HEALTH_STARTING, HEALTH_WARMING_UP}:
        return "monitor-reconnecting"
    if health_status == HEALTH_DEGRADED:
        return "monitor-degraded"
    if health_status == HEALTH_RECONNECTING:
        return "monitor-reconnecting"
    if health_status == HEALTH_OFFLINE:
        return "monitor-offline"
    if health_status == HEALTH_STOPPED:
        return "monitor-stopped"
    return "monitor-idle"


def _monitor_stream_for_status(camera_id: int, health_status: str) -> str:
    if health_status in {"running", "running_motion_test"}:
        return resolve_stream_url(f"/cameras/{camera_id}/stream/processed")
    # Keep showing live RTSP whenever analytics is not running.
    # If the source is truly down, the raw endpoint already renders an unavailable frame.
    if health_status in {HEALTH_STARTING, HEALTH_WARMING_UP, HEALTH_DEGRADED, HEALTH_RECONNECTING, HEALTH_OFFLINE, HEALTH_STOPPED}:
        return resolve_stream_url(f"/cameras/{camera_id}/stream/raw")
    return resolve_stream_url(f"/cameras/{camera_id}/stream/raw")


def _camera_is_watchdog_ignored(camera: Camera) -> bool:
    if bool(getattr(camera, "is_deleted", False)):
        return True
    status = str(getattr(camera, "status", "") or "").strip().lower()
    return status in WATCHDOG_INACTIVE_STATUSES


def _query_watchdog_cameras(db):
    query = db.query(Camera)
    is_deleted_column = getattr(Camera, "is_deleted", None)
    if is_deleted_column is not None:
        query = query.filter(is_deleted_column == False)  # noqa: E712
    return query


@dataclass(slots=True)
class CameraHealthState:
    health_status: str = "stopped"
    consecutive_stall_checks: int = 0
    restart_count: int = 0
    last_restart_at: datetime | None = None
    last_restart_reason: str | None = None
    last_status_change_at: datetime | None = None
    last_status_reason: str | None = None
    last_seen_at: datetime | None = None
    worker_pid: int | None = None
    last_dead_worker_pid: int | None = None


class CameraHealthMonitor:
    # O monitor roda em background e tenta manter a visao de estado coerente com o runtime.
    def __init__(self):
        self.logger = get_logger("app.camera_health_monitor")
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._probe_cache: dict[int, tuple[float, bool]] = {}
        self._stopped_cleanup_at: dict[int, float] = {}
        self._probe_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._camera_states: dict[int, CameraHealthState] = {}
        self._startup_reconciled = False

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._reconcile_startup_state()
        self._run_once(force_probe=True)

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="camera-health-monitor",
        )
        self._thread.start()
        self.logger.info(
            "Camera health monitor started",
            extra={"action": "health_monitor_start", "status": "running", "reason": "startup"},
        )

    def stop(self, timeout: float = 5.0):
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self.logger.info(
            "Camera health monitor stopped",
            extra={"action": "health_monitor_stop", "status": "stopped", "reason": "shutdown"},
        )

    def get_camera_state(self, camera_id: int) -> CameraHealthState:
        with self._state_lock:
            state = self._camera_states.get(camera_id)
            if state is None:
                state = CameraHealthState()
                self._camera_states[camera_id] = state
            return state

    def get_snapshot(self) -> dict:
        db = SessionLocal()
        now_utc = _utc_now_naive()

        try:
            cameras = _query_watchdog_cameras(db).all()
            items = [self._collect_camera_snapshot(camera, now_utc) for camera in cameras]
            gpu = read_gpu_snapshot()
            snapshot = {
                "generated_at": now_utc.isoformat(),
                "camera_count": len(items),
                "running_count": sum(1 for item in items if item["health_status"] in RUNNING_STATES),
                "degraded_count": sum(1 for item in items if item["health_status"] == "degraded"),
                "warming_up_count": sum(1 for item in items if item["health_status"] in {HEALTH_STARTING, HEALTH_WARMING_UP}),
                "reconnecting_count": sum(1 for item in items if item["health_status"] == "reconnecting"),
                "offline_count": sum(1 for item in items if item["health_status"] == "offline"),
                "stopped_count": sum(1 for item in items if item["health_status"] == "stopped"),
                "cameras": items,
                "gpu": {"available": False},
                "runtime_tuning": {"mode": "vms"},
                "detector_engine": {"status": "disabled"},
            }
            self._record_operational_history(snapshot)
            self._record_resource_history(snapshot)
            return snapshot
        finally:
            db.close()

    def _snapshot_from_items(self, items: list[dict], now_utc: datetime) -> dict:
        gpu = {"available": False}
        return {
            "generated_at": now_utc.isoformat(),
            "camera_count": len(items),
            "running_count": sum(1 for item in items if item["health_status"] in RUNNING_STATES),
            "degraded_count": sum(1 for item in items if item["health_status"] == HEALTH_DEGRADED),
            "warming_up_count": sum(1 for item in items if item["health_status"] in {HEALTH_STARTING, HEALTH_WARMING_UP}),
            "reconnecting_count": sum(1 for item in items if item["health_status"] == HEALTH_RECONNECTING),
            "offline_count": sum(1 for item in items if item["health_status"] == HEALTH_OFFLINE),
            "stopped_count": sum(1 for item in items if item["health_status"] == HEALTH_STOPPED),
            "cameras": items,
            "gpu": gpu,
            "runtime_tuning": {"mode": "vms"},
            "detector_engine": {"status": "disabled"},
        }

    def _record_operational_history(self, snapshot: dict) -> None:
        try:
            operational_history_store.record_snapshot(snapshot)
        except Exception:
            self.logger.exception(
                "Failed to record operational history",
                extra={"action": "operational_history_record", "status": "degraded", "reason": "history_write_failed"},
            )

    def _record_resource_history(self, snapshot: dict) -> None:
        try:
            resource_history_store.record_snapshot(self._build_resource_usage(snapshot))
        except Exception:
            self.logger.exception(
                "Failed to record resource history",
                extra={"action": "resource_history_record", "status": "degraded", "reason": "history_write_failed"},
            )

    def _build_resource_usage(self, snapshot: dict) -> dict:
        def _coerce(value) -> float:
            try:
                result = float(value)
            except (TypeError, ValueError):
                return 0.0
            return result if result == result else 0.0  # descarta NaN

        cpu_total = 0.0
        ram_total = 0.0
        fps_total = 0.0
        raw_fps_total = 0.0
        workers = 0
        for item in snapshot.get("cameras", []):
            if not isinstance(item, dict):
                continue
            camera_id = item.get("camera_id")
            if camera_id is None:
                continue
            metrics = metrics_store.get_metrics(camera_id)
            if not metrics:
                continue
            workers += 1
            cpu_total += _coerce(metrics.get("process_cpu_percent"))
            ram_total += _coerce(metrics.get("process_rss_mb"))
            fps_total += _coerce(metrics.get("processed_fps"))
            raw_fps_total += _coerce(metrics.get("raw_fps"))

        gpu = snapshot.get("gpu") if isinstance(snapshot.get("gpu"), dict) else {}

        host_cpu = None
        host_ram = None
        try:
            import psutil

            host_cpu = float(psutil.cpu_percent(interval=None))
            host_ram = float(psutil.virtual_memory().percent)
        except Exception:
            pass

        return {
            "generated_at": snapshot.get("generated_at"),
            "cpu": round(cpu_total, 2),
            "ram_mb": round(ram_total, 2),
            "gpu": gpu.get("utilization_percent"),
            "gpu_mem_mb": gpu.get("memory_used_mb"),
            "gpu_mem_total_mb": gpu.get("memory_total_mb"),
            "fps": round(fps_total, 2),
            "raw_fps": round(raw_fps_total, 2),
            "workers": workers,
            "running": int(snapshot.get("running_count", 0) or 0),
            "host_cpu": round(host_cpu, 2) if host_cpu is not None else None,
            "host_ram": round(host_ram, 2) if host_ram is not None else None,
        }

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self._run_once()
            except Exception:
                self.logger.exception("Health check loop failed")

            interval = max(3, int(settings.watchdog_poll_interval_seconds or settings.camera_health_check_interval_seconds))
            self._stop_event.wait(timeout=interval)

    def _reconcile_startup_state(self):
        if self._startup_reconciled:
            return

        restored = 0
        normalized = 0
        db = SessionLocal()

        try:
            cameras = _query_watchdog_cameras(db).order_by(Camera.id.asc()).all()

            for index, camera in enumerate(cameras):
                action = _startup_reconcile_action(
                    camera,
                    restore_running_workers=bool(settings.camera_health_restore_workers_on_startup),
                )

                if action == "skip":
                    continue

                if action == "normalize":
                    camera.status = "stopped"
                    normalized += 1
                    continue

                if not camera.rtsp_url:
                    camera.status = "stopped"
                    normalized += 1
                    continue

                existing = registry.get_worker(camera.id)
                if existing is not None:
                    restored += 1
                    continue

                delay_seconds = index * max(0.0, float(settings.camera_startup_stagger_seconds))
                if delay_seconds > 0:
                    self._stop_event.wait(timeout=delay_seconds)
                try:
                    worker_lifecycle_manager.start(
                        camera,
                        restart_existing=False,
                        reason="startup_reconcile",
                    )
                except WorkerStartBlocked as exc:
                    camera.status = "stopped"
                    normalized += 1
                    self.logger.warning(
                        "Startup worker restore blocked camera_id=%s reason=%s",
                        camera.id,
                        exc,
                        extra={
                            "camera_id": camera.id,
                            "action": "startup_reconcile_blocked",
                            "status": "blocked",
                            "reason": str(exc),
                        },
                    )
                    continue
                except WorkerLifecycleError:
                    camera.status = "stopped"
                    normalized += 1
                    self.logger.exception(
                        "Startup worker restore failed camera_id=%s",
                        camera.id,
                        extra={
                            "camera_id": camera.id,
                            "action": "startup_reconcile_failed",
                            "status": "error",
                            "reason": "lifecycle_error",
                        },
                    )
                    continue
                camera.status = "running_motion_test"
                restored += 1

            db.commit()
            self.logger.info(
                "Startup reconciliation finished restored=%s normalized=%s",
                restored,
                normalized,
                extra={"action": "startup_reconcile", "status": "running", "reason": "startup"},
            )
            self._startup_reconciled = True

        except Exception:
            db.rollback()
            self.logger.exception(
                "Failed to reconcile startup camera state",
                extra={"action": "startup_reconcile", "status": "error", "reason": "startup"},
            )
            raise
        finally:
            db.close()

    def _extract_probe_target(self, camera: Camera) -> tuple[str | None, int]:
        if camera.rtsp_url:
            try:
                parsed = urlparse(camera.rtsp_url)
                host = parsed.hostname
                port = parsed.port or 554
                if host:
                    return host, int(port)
            except Exception:
                pass

        host = (camera.ip or "").strip() or None
        return host, 554

    def _probe_camera_reachable(self, camera: Camera, force: bool = False) -> bool:
        if not camera:
            return False

        now = time.time()
        probe_interval = max(5, int(settings.camera_health_probe_interval_seconds))
        if camera.id == 1:
            probe_interval = max(probe_interval, 45)

        with self._probe_lock:
            cached = self._probe_cache.get(camera.id)
            if not force and cached is not None:
                cached_ts, cached_value = cached
                if (now - cached_ts) < probe_interval:
                    return bool(cached_value)

        if gateway_is_enabled():
            reachable = False
            try:
                timeout = max(0.5, float(settings.camera_health_probe_timeout_seconds))
                gateway_status = fetch_camera_status(camera.id, source_url=camera.rtsp_url, timeout_seconds=timeout)
                reachable = gateway_status is not None and str(gateway_status.get("state") or "").lower() not in {"offline", "stopped_manual"}
            except Exception:
                reachable = False

            with self._probe_lock:
                self._probe_cache[camera.id] = (now, reachable)

            return reachable

        host, port = self._extract_probe_target(camera)
        reachable = False

        if host:
            try:
                timeout = max(0.2, float(settings.camera_health_probe_timeout_seconds))
                if camera.id == 1:
                    timeout = max(timeout, 3.0)
                with socket.create_connection((host, port), timeout=timeout):
                    reachable = True
            except Exception:
                reachable = False

        with self._probe_lock:
            self._probe_cache[camera.id] = (now, reachable)

        return reachable

    def _restart_worker_controller(
        self,
        camera: Camera,
        worker,
        reason: str,
        now_utc: datetime,
        mode_name: str = "normal",
        stale_seconds: float | None = None,
        stall_checks: int | None = None,
    ) -> bool:
        controller = worker
        is_stop_requested = getattr(controller, "is_stop_requested", False) if controller is not None else False
        if callable(is_stop_requested):
            try:
                if bool(is_stop_requested()):
                    return False
            except Exception:
                pass
        elif bool(is_stop_requested):
            return False

        if str(camera.status or "").startswith("stopped"):
            return False

        if str(reason or "").strip().lower() == HEALTH_STOPPED:
            return False

        try:
            result = worker_lifecycle_manager.start(
                camera,
                restart_existing=True,
                stop_timeout=max(2.0, float(settings.camera_health_probe_timeout_seconds)),
                reason=normalize_reason(reason, "manual_restart"),
            )
        except WorkerStartBlocked as exc:
            self.logger.warning(
                "Watchdog worker restart blocked camera_id=%s reason=%s",
                camera.id,
                exc,
                extra={
                    "camera_id": camera.id,
                    "action": "watchdog_restart_blocked",
                    "status": "blocked",
                    "reason": str(exc),
                },
            )
            return False
        except WorkerLifecycleError:
            self.logger.exception(
                "Failed to restart worker controller camera_id=%s",
                camera.id,
                extra={
                    "camera_id": camera.id,
                    "pid": getattr(controller, "process_pid", getattr(controller, "pid", "-")) or "-",
                    "worker_pid": getattr(controller, "process_pid", getattr(controller, "pid", "-")) or "-",
                    "stale_seconds": stale_seconds,
                    "stall_checks": stall_checks,
                    "action": "watchdog_restart_failed",
                    "status": HEALTH_OFFLINE,
                    "reason": normalize_reason(reason, "manual_restart"),
                },
            )
            return False

        self.logger.warning(
            "Watchdog restarted worker controller camera_id=%s reason=%s",
            camera.id,
            reason,
            extra={
                "camera_id": camera.id,
                "pid": result.record.pid if result.record is not None else "-",
                "worker_pid": result.record.pid if result.record is not None else "-",
                "worker_generation": result.record.generation if result.record is not None else "-",
                "stale_seconds": stale_seconds,
                "stall_checks": stall_checks,
                "action": "watchdog_restart_success",
                "status": HEALTH_RECONNECTING,
                "reason": normalize_reason(reason, "manual_restart"),
            },
        )
        return True

    def _watchdog_force_restart_worker(
        self,
        camera: Camera,
        worker,
        reason: str,
        now_utc: datetime,
        mode_name: str,
        stale_seconds: float,
        stall_checks: int,
    ) -> bool:
        old_pid = getattr(worker, "process_pid", getattr(worker, "pid", None)) if worker is not None else None
        self.logger.warning(
            "Watchdog force restart camera_id=%s pid=%s stale_seconds=%.1f stall_checks=%s",
            camera.id,
            old_pid or "-",
            stale_seconds,
            stall_checks,
            extra={
                "camera_id": camera.id,
                "pid": old_pid or "-",
                "worker_pid": old_pid or "-",
                "stale_seconds": stale_seconds,
                "stall_checks": stall_checks,
                "action": "watchdog_force_restart",
                "status": HEALTH_RECONNECTING,
                "reason": normalize_reason(reason, "stale_worker"),
            },
        )

        try:
            frame_store.remove_frame(camera.id)
        except Exception:
            pass
        try:
            metrics_store.remove_metrics(camera.id)
        except Exception:
            pass
        with self._probe_lock:
            self._probe_cache.pop(camera.id, None)

        try:
            result = worker_lifecycle_manager.start(
                camera,
                restart_existing=True,
                stop_timeout=max(2.0, float(settings.camera_health_probe_timeout_seconds)),
                reason=normalize_reason(reason, "stale_worker"),
            )
        except WorkerStartBlocked as exc:
            self.logger.warning(
                "Watchdog force restart blocked camera_id=%s reason=%s",
                camera.id,
                exc,
                extra={
                    "camera_id": camera.id,
                    "pid": old_pid or "-",
                    "worker_pid": old_pid or "-",
                    "action": "watchdog_force_restart_blocked",
                    "status": "blocked",
                    "reason": str(exc),
                },
            )
            return False
        except WorkerLifecycleError:
            self.logger.exception(
                "Watchdog force restart failed camera_id=%s old_pid=%s",
                camera.id,
                old_pid or "-",
                extra={
                    "camera_id": camera.id,
                    "pid": old_pid or "-",
                    "worker_pid": old_pid or "-",
                    "stale_seconds": stale_seconds,
                    "stall_checks": stall_checks,
                    "action": "watchdog_restart_failed",
                    "status": HEALTH_OFFLINE,
                    "reason": normalize_reason(reason, "stale_worker"),
                },
            )
            return False

        new_pid = result.record.pid if result.record is not None else None
        new_generation = result.record.generation if result.record is not None else None
        self.logger.warning(
            "Watchdog force restart succeeded camera_id=%s old_pid=%s new_pid=%s",
            camera.id,
            old_pid or "-",
            new_pid or "-",
            extra={
                "camera_id": camera.id,
                "pid": new_pid or "-",
                "worker_pid": new_pid or "-",
                "worker_generation": new_generation or "-",
                "old_pid": old_pid or "-",
                "stale_seconds": stale_seconds,
                "stall_checks": stall_checks,
                "action": "watchdog_restart_success",
                "status": HEALTH_RECONNECTING,
                "reason": normalize_reason(reason, "stale_worker"),
            },
        )
        return True

    def _collect_camera_snapshot(self, camera: Camera, now_utc: datetime) -> dict:
        if _camera_is_watchdog_ignored(camera):
            worker = registry.get_worker(camera.id)
            if worker is not None:
                try:
                    worker_lifecycle_manager.stop(
                        camera.id,
                        timeout=max(2.0, float(settings.camera_health_probe_timeout_seconds)),
                        reason="watchdog_ignored",
                    )
                except WorkerLifecycleError:
                    self.logger.exception("Failed stopping ignored camera worker camera_id=%s", camera.id)
            state = self.get_camera_state(camera.id)
            state.health_status = HEALTH_STOPPED
            state.consecutive_stall_checks = 0
            state.last_status_reason = "watchdog_ignored"
            state.worker_pid = None
            return {
                "camera_id": camera.id,
                "camera_name": camera.name,
                "camera_status": camera.status,
                "worker_pid": None,
                "worker_mode": "normal",
                "health_status": HEALTH_STOPPED,
                "monitor_class": _health_status_class(HEALTH_STOPPED),
                "is_running": False,
                "consecutive_stall_checks": 0,
                "restart_count": state.restart_count,
                "last_restart_at": state.last_restart_at.isoformat() if state.last_restart_at else None,
                "last_restart_reason": state.last_restart_reason,
                "last_status_change_at": state.last_status_change_at.isoformat() if state.last_status_change_at else None,
                "last_status_reason": state.last_status_reason,
                "last_frame_at": None,
                "last_processed_frame_at": None,
                "last_successful_inference_at": None,
                "last_metrics_at": None,
                "latest_activity_at": None,
                "latest_activity_age_seconds": None,
                "gateway_state": None,
                "gateway_source_url": None,
                "gateway_failure_count": 0,
                "gateway_last_frame_at": None,
                "gateway_last_frame_age_seconds": None,
                "gateway_last_reconnect_at": None,
                "gateway_last_reconnect_age_seconds": None,
                "reconnect_count": 0,
                "dropped_frames_count": 0,
                "metrics_age_seconds": None,
            }

        worker_record = registry.get_record(camera.id)
        worker = worker_record.worker if worker_record is not None else None
        worker_generation = worker_record.generation if worker_record is not None else None
        state = self.get_camera_state(camera.id)
        metrics = metrics_store.get_metrics(camera.id) or {}
        raw_frame_meta = frame_store.get_raw_frame_metadata(camera.id) or {}
        processed_frame_meta = frame_store.get_processed_frame_metadata(camera.id) or {}
        current = str(camera.status or "idle")

        worker_pid = None
        worker_alive = False
        worker_stop_requested = False
        if worker is not None:
            worker_pid = getattr(worker, "process_pid", getattr(worker, "pid", None))
            try:
                worker_alive = bool(worker.is_alive())
            except Exception:
                worker_alive = True
            if not worker_alive:
                worker_exitcode = getattr(worker, "exitcode", None)
                if worker_exitcode is not None and getattr(state, "last_dead_worker_pid", None) != worker_pid:
                    state.last_dead_worker_pid = worker_pid
                    self.logger.error(
                        "Worker process exited camera_id=%s pid=%s exitcode=%s",
                        camera.id,
                        worker_pid or "-",
                        worker_exitcode,
                        extra={
                            "camera_id": camera.id,
                            "worker_pid": worker_pid or "-",
                            "action": "worker_process_exit",
                            "status": "error",
                            "reason": f"exitcode_{worker_exitcode}",
                        },
                    )
            is_stop_requested = getattr(worker, "is_stop_requested", False)
            if callable(is_stop_requested):
                try:
                    worker_stop_requested = bool(is_stop_requested())
                except Exception:
                    worker_stop_requested = False
            else:
                worker_stop_requested = bool(is_stop_requested)

        if current == "stopped_manual":
            if worker is not None:
                try:
                    worker_lifecycle_manager.stop(
                        camera.id,
                        timeout=max(2.0, float(settings.camera_health_probe_timeout_seconds)),
                        reason="manual_stop_reconcile",
                    )
                except WorkerLifecycleError:
                    self.logger.exception("Failed reconciling manual stop camera_id=%s", camera.id)
                worker = None
                worker_record = None
                worker_generation = None
                worker_pid = None
                worker_alive = False
                worker_stop_requested = True

            cleanup_due = (
                camera.id not in self._stopped_cleanup_at
                or time.time() - float(self._stopped_cleanup_at.get(camera.id, 0.0) or 0.0) >= 60.0
            )
            if cleanup_due:
                self._stopped_cleanup_at[camera.id] = time.time()
                try:
                    stop_camera_source(camera.id, timeout_seconds=max(0.5, float(settings.camera_health_probe_timeout_seconds)))
                except Exception:
                    pass
                try:
                    metrics_store.remove_metrics(camera.id)
                except Exception:
                    pass
                try:
                    frame_store.remove_frame(camera.id)
                except Exception:
                    pass
            metrics = {}
            raw_frame_meta = {}
            processed_frame_meta = {}
        state.worker_pid = worker_pid

        worker_health_status = str(metrics.get("health_status") or getattr(worker, "health_status", "") or camera.status or "idle")
        last_frame_at = raw_frame_meta.get("updated_at") or _parse_iso_datetime(metrics.get("last_frame_at"))
        last_processed_frame_at = processed_frame_meta.get("updated_at") or _parse_iso_datetime(metrics.get("last_processed_frame_at"))
        last_successful_inference_at = _parse_iso_datetime(metrics.get("last_successful_inference_at"))
        last_metrics_at = _parse_iso_datetime(metrics.get("updated_at"))
        latest_activity_at = _max_dt(last_frame_at, last_processed_frame_at, last_successful_inference_at, last_metrics_at)

        gateway_status = None
        gateway_state = ""
        gateway_source_url = None
        gateway_last_frame_at = None
        gateway_last_reconnect_at = None
        gateway_failure_count = 0
        gateway_last_frame_age_seconds = None
        gateway_last_reconnect_age_seconds = None
        should_poll_gateway = not (worker is None and current in {"stopped", "idle", "disabled"})
        if gateway_is_enabled() and should_poll_gateway:
            try:
                gateway_status = fetch_camera_status(
                    camera.id,
                    source_url=camera.rtsp_url,
                    timeout_seconds=max(0.5, float(settings.camera_health_probe_timeout_seconds)),
                )
            except Exception:
                gateway_status = None

            if isinstance(gateway_status, dict):
                gateway_state = str(gateway_status.get("state") or "").strip().lower()
                gateway_source_url = str(gateway_status.get("source_url") or "").strip() or None
                gateway_failure_count = int(gateway_status.get("failure_count", 0) or 0)
                gateway_last_frame_at = _parse_iso_datetime(gateway_status.get("last_frame_at"))
                gateway_last_reconnect_at = _parse_iso_datetime(gateway_status.get("last_reconnect_at"))
                last_frame_at = _max_dt(last_frame_at, gateway_last_frame_at)
                latest_activity_at = _max_dt(latest_activity_at, gateway_last_frame_at)
                if gateway_last_frame_at is not None:
                    gateway_last_frame_age_seconds = max(0.0, (now_utc - gateway_last_frame_at).total_seconds())
                if gateway_last_reconnect_at is not None:
                    gateway_last_reconnect_age_seconds = max(0.0, (now_utc - gateway_last_reconnect_at).total_seconds())
                if gateway_state in {"running", "live", "snapshot"} and not (worker is not None and not worker_alive):
                    worker_health_status = HEALTH_RUNNING
                elif gateway_state in {HEALTH_STARTING, HEALTH_WARMING_UP, HEALTH_DEGRADED, HEALTH_RECONNECTING, HEALTH_OFFLINE, HEALTH_STOPPED, "stopped_manual"}:
                    worker_health_status = HEALTH_STOPPED if gateway_state == "stopped_manual" else gateway_state

        published_mode = str(metrics.get("worker_mode") or current or "normal")
        mode_name = getattr(worker, "mode_name", "motion_test" if published_mode == "running_motion_test" else "normal") if worker is not None else ("motion_test" if published_mode == "running_motion_test" else "normal")
        running_status = "running_motion_test" if mode_name == "motion_test" else "running"
        degraded_after = max(1.0, float(settings.camera_health_degraded_after_seconds))
        offline_after = max(degraded_after, float(settings.camera_health_offline_after_seconds))
        restart_after = max(1, int(settings.camera_health_restart_after_stall_checks))
        restart_cooldown = max(0.0, float(settings.camera_health_restart_cooldown_seconds))
        force_stale_after = max(1.0, float(getattr(settings, "watchdog_max_deferred_stale_seconds", MAX_DEFERRED_STALE_SECONDS)))
        force_restart_after = max(1, int(getattr(settings, "watchdog_max_stall_checks_before_force_restart", MAX_STALL_CHECKS_BEFORE_FORCE_RESTART)))
        force_restart_cooldown = max(0.0, float(getattr(settings, "watchdog_restart_cooldown_seconds", WATCHDOG_RESTART_COOLDOWN_SECONDS)))
        startup_grace = max(5.0, float(settings.camera_health_startup_grace_seconds))
        reconnect_grace = max(startup_grace, float(getattr(settings, "camera_health_reconnect_grace_seconds", 45.0)))
        if camera.id == 1:
            degraded_after = max(degraded_after, 20.0)
            offline_after = max(offline_after, 60.0)
            restart_after = max(restart_after, 10)
            restart_cooldown = max(restart_cooldown, 180.0)
            startup_grace = max(startup_grace, 90.0)
            reconnect_grace = max(reconnect_grace, 120.0)

        health_status = worker_health_status if worker_health_status in {
            HEALTH_RUNNING,
            "running_motion_test",
            HEALTH_STARTING,
            HEALTH_WARMING_UP,
            HEALTH_DEGRADED,
            HEALTH_RECONNECTING,
            HEALTH_OFFLINE,
            HEALTH_STOPPED,
        } else current
        reason = "idle"
        state_age_seconds = None
        if state.last_status_change_at is not None:
            state_age_seconds = max(0.0, (now_utc - state.last_status_change_at).total_seconds())
        restart_age_seconds = None
        if state.last_restart_at is not None:
            restart_age_seconds = max(0.0, (now_utc - state.last_restart_at).total_seconds())

        metrics_generation = str(metrics.get("worker_generation") or "").strip()
        current_error_owned_by_live_worker = bool(
            worker_alive
            and worker_generation
            and metrics_generation == worker_generation
            and latest_activity_at is not None
        )

        if current.startswith("error:") and not current_error_owned_by_live_worker:
            health_status = current
            reason = REASON_UNKNOWN_ERROR
        elif worker is not None and not worker_alive:
            state.consecutive_stall_checks += 1
            health_status = HEALTH_RECONNECTING if camera.rtsp_url else HEALTH_OFFLINE
            reason = "worker_process_exit"
            if (
                state.last_restart_at is None
                or (now_utc - state.last_restart_at).total_seconds() >= restart_cooldown
            ):
                state.restart_count += 1
                state.last_restart_at = now_utc
                state.last_restart_reason = reason
                self._restart_worker_controller(
                    camera,
                    worker,
                    reason,
                    now_utc,
                    mode_name=mode_name,
                    stall_checks=state.consecutive_stall_checks,
                )
        elif worker is None:
            reachable = self._probe_camera_reachable(camera)
            if current == "stopped_manual":
                health_status = HEALTH_STOPPED
                reason = "manual_stop"
                state.consecutive_stall_checks = 0
            elif current == "stopped":
                health_status = HEALTH_STOPPED
                reason = "not_running"
                state.consecutive_stall_checks = 0
            elif current in {HEALTH_STARTING, HEALTH_WARMING_UP} or worker_health_status in {HEALTH_STARTING, HEALTH_WARMING_UP}:
                if state_age_seconds is None or state_age_seconds <= startup_grace:
                    health_status = HEALTH_WARMING_UP
                    reason = "startup_grace"
                    state.consecutive_stall_checks = 0
                else:
                    state.consecutive_stall_checks += 1
                    if reachable and state.consecutive_stall_checks < restart_after:
                        health_status = HEALTH_DEGRADED
                        reason = "starting_waiting"
                    else:
                        health_status = HEALTH_OFFLINE
                        reason = "reconnect_failed"
                    if state.consecutive_stall_checks >= restart_after and (
                        state.last_restart_at is None
                        or (now_utc - state.last_restart_at).total_seconds() >= restart_cooldown
                    ):
                        state.restart_count += 1
                        state.last_restart_at = now_utc
                        state.last_restart_reason = reason
                        self._restart_worker_controller(
                            camera,
                            None,
                            reason,
                            now_utc,
                            mode_name=mode_name,
                            stall_checks=state.consecutive_stall_checks,
                        )
                        health_status = HEALTH_RECONNECTING
            elif current.startswith("running") or worker_health_status in {HEALTH_DEGRADED, HEALTH_RECONNECTING, HEALTH_OFFLINE}:
                if restart_age_seconds is not None and restart_age_seconds <= reconnect_grace:
                    health_status = HEALTH_WARMING_UP
                    reason = "reconnect_grace"
                    state.consecutive_stall_checks = 0
                else:
                    state.consecutive_stall_checks += 1
                    if reachable and state.consecutive_stall_checks < restart_after:
                        health_status = HEALTH_DEGRADED
                        reason = "worker_missing_waiting"
                    else:
                        health_status = HEALTH_OFFLINE
                        reason = "reconnect_failed"
                    if state.consecutive_stall_checks >= restart_after and (
                        state.last_restart_at is None
                        or (now_utc - state.last_restart_at).total_seconds() >= restart_cooldown
                    ):
                        state.restart_count += 1
                        state.last_restart_at = now_utc
                        state.last_restart_reason = reason
                        self._restart_worker_controller(
                            camera,
                            None,
                            reason,
                            now_utc,
                            mode_name=mode_name,
                            stall_checks=state.consecutive_stall_checks,
                        )
                        health_status = HEALTH_RECONNECTING
            else:
                health_status = HEALTH_STOPPED
                reason = "not_running"
            if health_status in {HEALTH_STOPPED, "idle"}:
                state.consecutive_stall_checks = 0
        else:
            worker_starting = worker_health_status in {HEALTH_STARTING, HEALTH_WARMING_UP} or current in {HEALTH_STARTING, HEALTH_WARMING_UP}
            if worker_starting and (state_age_seconds is None or state_age_seconds <= startup_grace):
                health_status = HEALTH_WARMING_UP
                reason = "startup_grace"
                state.consecutive_stall_checks = 0
            elif worker_starting:
                health_status = HEALTH_WARMING_UP
                reason = "warming_up"
                state.consecutive_stall_checks = 0
            elif worker_stop_requested or (
                (worker_health_status in {HEALTH_STOPPED, "stopped"} or current == "stopped")
                and not worker_alive
            ):
                health_status = HEALTH_STOPPED
                reason = "manual_stop"
                state.consecutive_stall_checks = 0
            elif worker_health_status == HEALTH_OFFLINE:
                health_status = HEALTH_OFFLINE
                reason = "reconnect_failed"
                state.consecutive_stall_checks += 1
            elif worker_health_status == HEALTH_RECONNECTING:
                if restart_age_seconds is not None and restart_age_seconds <= reconnect_grace:
                    health_status = HEALTH_WARMING_UP
                    reason = "reconnect_grace"
                    state.consecutive_stall_checks = 0
                else:
                    health_status = HEALTH_RECONNECTING
                    reason = "reconnecting"
                    state.consecutive_stall_checks += 1
            elif latest_activity_at is None:
                if state_age_seconds is not None and state_age_seconds <= startup_grace:
                    health_status = HEALTH_WARMING_UP
                    reason = "startup_grace"
                    state.consecutive_stall_checks = 0
                else:
                    health_status = HEALTH_DEGRADED
                    reason = "no_activity"
                    state.consecutive_stall_checks += 1
            else:
                age_seconds = (now_utc - latest_activity_at).total_seconds()
                if age_seconds <= degraded_after:
                    health_status = running_status
                    reason = "healthy"
                    state.consecutive_stall_checks = 0
                elif age_seconds <= offline_after:
                    state.consecutive_stall_checks += 1
                    health_status = HEALTH_DEGRADED
                    reason = f"stale_for_{age_seconds:.1f}s"
                    self.logger.warning(
                        "Stall detected camera_id=%s age_seconds=%.1f stall_checks=%s",
                        camera.id,
                        age_seconds,
                        state.consecutive_stall_checks,
                        extra={
                            "camera_id": camera.id,
                            "pid": worker_pid or "-",
                            "worker_pid": worker_pid or "-",
                            "stale_seconds": age_seconds,
                            "stall_checks": state.consecutive_stall_checks,
                            "action": "watchdog_stall_detected",
                            "status": "degraded",
                            "reason": reason,
                        },
                    )
                    if state.consecutive_stall_checks >= restart_after:
                        health_status = HEALTH_RECONNECTING
                        if worker_alive:
                            if state.health_status != HEALTH_RECONNECTING:
                                self.logger.info(
                                    "Watchdog kept live worker in reconnect cycle camera_id=%s age_seconds=%.1f stall_checks=%s",
                                    camera.id,
                                    age_seconds,
                                    state.consecutive_stall_checks,
                                    extra={
                                        "camera_id": camera.id,
                                        "pid": worker_pid or "-",
                                        "worker_pid": worker_pid or "-",
                                        "stale_seconds": age_seconds,
                                        "stall_checks": state.consecutive_stall_checks,
                                        "action": "watchdog_reconnect_hold",
                                        "status": HEALTH_RECONNECTING,
                                        "reason": reason,
                                    },
                                )
                            cooldown_elapsed = (
                                state.last_restart_at is None
                                or (now_utc - state.last_restart_at).total_seconds() >= force_restart_cooldown
                            )
                            if camera.rtsp_url and cooldown_elapsed and (
                                age_seconds >= force_stale_after
                                or state.consecutive_stall_checks >= force_restart_after
                            ):
                                state.restart_count += 1
                                state.last_restart_at = now_utc
                                state.last_restart_reason = reason
                                restarted = self._watchdog_force_restart_worker(
                                    camera,
                                    worker,
                                    reason,
                                    now_utc,
                                    mode_name,
                                    age_seconds,
                                    state.consecutive_stall_checks,
                                )
                                if restarted:
                                    state.consecutive_stall_checks = 0
                        elif state.health_status != HEALTH_RECONNECTING and (
                            state.last_restart_at is None
                            or (now_utc - state.last_restart_at).total_seconds() >= restart_cooldown
                        ):
                            state.restart_count += 1
                            state.last_restart_at = now_utc
                            state.last_restart_reason = reason
                            self._restart_worker_controller(
                                camera,
                                worker,
                                reason,
                                now_utc,
                                mode_name=mode_name,
                                stale_seconds=age_seconds,
                                stall_checks=state.consecutive_stall_checks,
                            )
                else:
                    state.consecutive_stall_checks += 1
                    reason = f"stale_for_{age_seconds:.1f}s"
                    if state.consecutive_stall_checks >= restart_after:
                        health_status = HEALTH_RECONNECTING
                        if worker_alive:
                            cooldown_elapsed = (
                                state.last_restart_at is None
                                or (now_utc - state.last_restart_at).total_seconds() >= force_restart_cooldown
                            )
                            should_force_restart = (
                                camera.rtsp_url
                                and cooldown_elapsed
                                and (
                                    age_seconds >= force_stale_after
                                    or state.consecutive_stall_checks >= force_restart_after
                                )
                            )
                            self.logger.info(
                                "Watchdog deferred restart while worker is alive camera_id=%s age_seconds=%.1f stall_checks=%s",
                                camera.id,
                                age_seconds,
                                state.consecutive_stall_checks,
                                extra={
                                    "camera_id": camera.id,
                                    "pid": worker_pid or "-",
                                    "worker_pid": worker_pid or "-",
                                    "stale_seconds": age_seconds,
                                    "stall_checks": state.consecutive_stall_checks,
                                    "action": "watchdog_restart_deferred",
                                    "status": HEALTH_RECONNECTING,
                                    "reason": reason,
                                },
                            )
                            if should_force_restart:
                                state.restart_count += 1
                                state.last_restart_at = now_utc
                                state.last_restart_reason = reason
                                restarted = self._watchdog_force_restart_worker(
                                    camera,
                                    worker,
                                    reason,
                                    now_utc,
                                    mode_name,
                                    age_seconds,
                                    state.consecutive_stall_checks,
                                )
                                if restarted:
                                    state.consecutive_stall_checks = 0
                        elif state.health_status != HEALTH_RECONNECTING and (
                            state.last_restart_at is None
                            or (now_utc - state.last_restart_at).total_seconds() >= restart_cooldown
                        ):
                            state.restart_count += 1
                            state.last_restart_at = now_utc
                            state.last_restart_reason = reason
                            self._restart_worker_controller(
                                camera,
                                worker,
                                reason,
                                now_utc,
                                mode_name=mode_name,
                                stale_seconds=age_seconds,
                                stall_checks=state.consecutive_stall_checks,
                            )
                    else:
                        health_status = HEALTH_OFFLINE
                        reason = "stream_stalled"

        if state.health_status != health_status:
            state.last_status_change_at = now_utc
            state.last_status_reason = reason
            self.logger.info(
                "Health status changed camera_id=%s old=%s new=%s reason=%s",
                camera.id,
                state.health_status,
                health_status,
                reason,
                extra={
                    "camera_id": camera.id,
                    "worker_pid": worker_pid or "-",
                    "action": "health_status_change",
                    "status": health_status,
                    "reason": reason,
                },
            )
            state.health_status = health_status

        if camera.status != health_status and not current.startswith("error:"):
            camera.status = health_status

        state.last_seen_at = latest_activity_at

        monitor_class = _health_status_class(state.health_status)
        if getattr(camera, "highest_open_severity", None) == "critical":
            monitor_class = f"alarm-critical {monitor_class}"
        elif getattr(camera, "highest_open_severity", None) == "high":
            monitor_class = f"alarm-high {monitor_class}"
        elif getattr(camera, "has_open_alarm", False):
            monitor_class = f"alarm-medium {monitor_class}"

        return {
            "camera_id": camera.id,
            "camera_name": camera.name,
            "camera_status": camera.status,
            "worker_pid": worker_pid,
            "worker_generation": worker_generation,
            "worker_mode": mode_name,
            "health_status": state.health_status,
            "monitor_class": monitor_class,
            "is_running": state.health_status in RUNNING_STATES,
            "consecutive_stall_checks": state.consecutive_stall_checks,
            "restart_count": state.restart_count,
            "last_restart_at": state.last_restart_at.isoformat() if state.last_restart_at else None,
            "last_restart_reason": state.last_restart_reason,
            "last_status_change_at": state.last_status_change_at.isoformat() if state.last_status_change_at else None,
            "last_status_reason": state.last_status_reason,
            "last_frame_at": last_frame_at.isoformat() if last_frame_at else None,
            "last_processed_frame_at": last_processed_frame_at.isoformat() if last_processed_frame_at else None,
            "last_successful_inference_at": last_successful_inference_at.isoformat() if last_successful_inference_at else None,
            "last_metrics_at": last_metrics_at.isoformat() if last_metrics_at else None,
            "latest_activity_at": latest_activity_at.isoformat() if latest_activity_at else None,
            "latest_activity_age_seconds": (now_utc - latest_activity_at).total_seconds() if latest_activity_at else None,
            "gateway_state": gateway_state or None,
            "gateway_source_url": gateway_source_url,
            "gateway_failure_count": gateway_failure_count,
            "gateway_last_frame_at": gateway_last_frame_at.isoformat() if gateway_last_frame_at else None,
            "gateway_last_frame_age_seconds": gateway_last_frame_age_seconds,
            "gateway_last_reconnect_at": gateway_last_reconnect_at.isoformat() if gateway_last_reconnect_at else None,
            "gateway_last_reconnect_age_seconds": gateway_last_reconnect_age_seconds,
            "reconnect_count": int(metrics.get("reconnect_count", 0) or 0),
            "dropped_frames_count": int(metrics.get("dropped_frames_count", 0) or 0),
            "metrics_age_seconds": (now_utc - last_metrics_at).total_seconds() if last_metrics_at else None,
        }

    def _worker_in_startup_grace(self, worker) -> bool:
        started_at = getattr(worker, "started_at", None)
        if started_at is None:
            return False

        age_seconds = time.time() - float(started_at)
        return age_seconds <= max(5, int(settings.camera_health_startup_grace_seconds))

    def _run_once(self, force_probe: bool = False):
        db = SessionLocal()
        now_utc = _utc_now_naive()
        changed = 0

        try:
            cameras = _query_watchdog_cameras(db).all()
            items = []

            for camera in cameras:
                previous_status = camera.status
                snapshot = self._collect_camera_snapshot(camera, now_utc)
                items.append(snapshot)
                if previous_status != snapshot["camera_status"]:
                    changed += 1
                camera.status = snapshot["camera_status"]

            if changed:
                db.commit()
            self._record_operational_history(self._snapshot_from_items(items, now_utc))

        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


camera_health_monitor = CameraHealthMonitor()
