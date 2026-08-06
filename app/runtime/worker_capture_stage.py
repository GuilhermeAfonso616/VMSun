"""Estagio concorrente de captura e reconexao do worker de camera."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable

from app.core.timezone import utc_now_naive
from app.runtime.capture import CameraCaptureService
from app.runtime.frame_pipeline import LatestFrameMailbox, QueuedFrame


class WorkerCaptureStage:
    """Produz o frame mais recente sem conhecer o restante do worker."""

    def __init__(
        self,
        *,
        camera_id: int,
        process_pid: int,
        rtsp_url: str,
        worker_mode: str,
        logger,
        is_running: Callable[[], bool],
        stop_requested: Callable[[], bool],
        capture_drop_frames: Callable[[], int],
        get_health_status: Callable[[], str],
        set_health: Callable[[str, str], None],
        on_frame_captured: Callable[[datetime], None],
        on_restart: Callable[[int, datetime, str], None],
        failures_before_reconnect: int,
        capture_service=None,
        mailbox=None,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
        sleep: Callable[[float], None] = time.sleep,
        utcnow: Callable[[], datetime] = utc_now_naive,
    ):
        self.camera_id = int(camera_id)
        self.process_pid = int(process_pid)
        self.logger = logger
        self.is_running = is_running
        self.stop_requested = stop_requested
        self.capture_drop_frames = capture_drop_frames
        self.get_health_status = get_health_status
        self.set_health = set_health
        self.on_frame_captured = on_frame_captured
        self.on_restart = on_restart
        self.thread_factory = thread_factory
        self.sleep = sleep
        self.utcnow = utcnow

        self.capture_service = (
            capture_service
            if capture_service is not None
            else CameraCaptureService(
                rtsp_url,
                camera_id=self.camera_id,
                worker_mode=worker_mode,
                stop_requested=stop_requested,
                failures_before_reconnect=failures_before_reconnect,
            )
        )
        self.mailbox = (
            mailbox
            if mailbox is not None
            else LatestFrameMailbox(
                camera_id=self.camera_id,
                worker_mode=worker_mode,
            )
        )
        self.thread: threading.Thread | None = None
        self.started_at: datetime | None = None

    @property
    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> bool:
        if self.is_alive:
            return False

        self.thread = self.thread_factory(
            target=self.run,
            name=f"camera-capture-{self.camera_id}",
            daemon=True,
        )
        self.thread.start()
        self.started_at = self.utcnow()
        return True

    def stop(self, *, timeout: float = 5.0) -> None:
        self.mailbox.close()
        if self.is_alive:
            self.thread.join(timeout=timeout)

    def release(self) -> None:
        self.capture_service.release()

    def run(self) -> None:
        self.logger.info(
            "Capture stage started",
            extra={
                "action": "capture_stage_start",
                "status": "running",
                "reason": "worker_pipeline_started",
                "worker_pid": self.process_pid,
            },
        )

        capture_opened = False

        try:
            while self.is_running() and not self.stop_requested():
                if not capture_opened:
                    try:
                        self.capture_service.open()
                        capture_opened = True
                        self.set_health("running", "capture_opened")
                    except Exception as exc:
                        self.set_health("reconnecting", "connect_timeout")
                        self.logger.warning(
                            "Capture stage failed to open capture; worker will keep retrying error=%s",
                            exc,
                            extra={
                                "action": "capture_stage_open",
                                "status": "reconnecting",
                                "reason": "connect_timeout",
                                "worker_pid": self.process_pid,
                            },
                        )
                        self.sleep(2.0)
                        continue

                read_result = self.capture_service.read_latest(
                    drop_frames=self.capture_drop_frames()
                )
                if not read_result.ok or read_result.frame is None:
                    read_reason = read_result.reason or "read_timeout"

                    if read_reason == "no_frame_ready":
                        if self.get_health_status() not in {
                            "degraded",
                            "reconnecting",
                            "offline",
                        }:
                            self.set_health("running", "gateway_frames_waiting")
                        self.sleep(0.02)
                        continue

                    self.set_health("degraded", read_reason)
                    if self.stop_requested():
                        break

                    self.logger.warning(
                        "Capture stage frame read failed read_ms=%.2f reconnect_count=%s dropped_frames=%s",
                        read_result.read_ms,
                        self.capture_service.reconnect_count,
                        self.capture_service.dropped_frames_count,
                        extra={
                            "action": "capture_stage_read",
                            "status": "degraded",
                            "reason": read_reason,
                            "worker_pid": self.process_pid,
                        },
                    )

                    reconnect_result = self.capture_service.handle_capture_failure()
                    self.on_restart(
                        self.capture_service.reconnect_count,
                        self.utcnow(),
                        reconnect_result.reason,
                    )

                    if reconnect_result.recovered:
                        capture_opened = True
                        self.set_health("running", "stream_recovered")
                    elif reconnect_result.status == "reconnecting":
                        self.set_health("reconnecting", reconnect_result.reason)
                    elif reconnect_result.status == "stopped":
                        self.set_health("stopped", reconnect_result.reason)
                        break
                    elif reconnect_result.status == "offline":
                        self.set_health("offline", reconnect_result.reason)
                    else:
                        self.set_health("degraded", reconnect_result.reason)

                    self.sleep(0.02)
                    continue

                captured_at = self.utcnow()
                metadata = dict(read_result.metadata or {})
                metadata["worker_received_at_ns"] = time.time_ns()
                metadata["worker_received_monotonic_ns"] = time.monotonic_ns()
                self.on_frame_captured(captured_at)
                self.mailbox.put_latest(
                    QueuedFrame(
                        frame=read_result.frame,
                        captured_at=captured_at,
                        read_ms=read_result.read_ms,
                        reason=read_result.reason,
                        metadata=metadata,
                    )
                )

                if self.get_health_status() not in {
                    "degraded",
                    "reconnecting",
                    "offline",
                }:
                    self.set_health("running", "frame_captured")

                if self.stop_requested():
                    break

        except Exception:
            self.logger.exception(
                "Capture stage fatal failure",
                extra={
                    "action": "capture_stage",
                    "status": "error",
                    "reason": "fatal_exception",
                    "worker_pid": self.process_pid,
                },
            )
            self.set_health("degraded", "capture_stage_failed")
        finally:
            self.mailbox.close()
            self.logger.info(
                "Capture stage finished",
                extra={
                    "action": "capture_stage_stop",
                    "status": "stopped",
                    "reason": "pipeline_shutdown",
                    "worker_pid": self.process_pid,
                },
            )
