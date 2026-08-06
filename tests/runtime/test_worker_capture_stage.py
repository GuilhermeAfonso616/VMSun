from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

from app.runtime.capture import CaptureReadResult, ReconnectResult
from app.runtime.frame_pipeline import LatestFrameMailbox
from app.runtime.worker_capture_stage import WorkerCaptureStage


FIXED_NOW = datetime(2026, 7, 20, 12, 30, 0)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class StubLogger:
    def __init__(self):
        self.info_calls = []
        self.warning_calls = []
        self.exception_calls = []

    def info(self, *args, **kwargs):
        self.info_calls.append((args, kwargs))

    def warning(self, *args, **kwargs):
        self.warning_calls.append((args, kwargs))

    def exception(self, *args, **kwargs):
        self.exception_calls.append((args, kwargs))


class StubCaptureService:
    def __init__(self, read_latest, reconnect_result=None):
        self._read_latest = read_latest
        self._reconnect_result = reconnect_result
        self.open_calls = 0
        self.read_drop_frames = []
        self.reconnect_calls = 0
        self.release_calls = 0
        self.reconnect_count = 0
        self.dropped_frames_count = 0

    def open(self):
        self.open_calls += 1

    def read_latest(self, *, drop_frames):
        self.read_drop_frames.append(drop_frames)
        return self._read_latest()

    def handle_capture_failure(self):
        self.reconnect_calls += 1
        self.reconnect_count += 1
        return self._reconnect_result

    def release(self):
        self.release_calls += 1


def build_stage(
    capture_service,
    *,
    running=None,
    stop_requested=lambda: False,
    sleep=lambda _seconds: None,
    thread_factory=None,
):
    running = running or {"value": True}
    health = {"status": "starting", "transitions": []}
    captured_at = []
    restarts = []
    logger = StubLogger()
    mailbox = LatestFrameMailbox(camera_id=7, worker_mode="normal")

    def set_health(status, reason):
        health["status"] = status
        health["transitions"].append((status, reason))

    kwargs = {}
    if thread_factory is not None:
        kwargs["thread_factory"] = thread_factory

    stage = WorkerCaptureStage(
        camera_id=7,
        process_pid=1234,
        rtsp_url="rtsp://camera/stream",
        worker_mode="normal",
        logger=logger,
        is_running=lambda: running["value"],
        stop_requested=stop_requested,
        capture_drop_frames=lambda: 3,
        get_health_status=lambda: health["status"],
        set_health=set_health,
        on_frame_captured=captured_at.append,
        on_restart=lambda count, restarted_at, reason: restarts.append(
            (count, restarted_at, reason)
        ),
        failures_before_reconnect=12,
        capture_service=capture_service,
        mailbox=mailbox,
        sleep=sleep,
        utcnow=lambda: FIXED_NOW,
        **kwargs,
    )
    return stage, running, health, captured_at, restarts, logger, mailbox


def test_capture_stage_publishes_latest_frame_and_updates_state():
    running = {"value": True}
    frame = object()

    def read_latest():
        running["value"] = False
        return CaptureReadResult(ok=True, frame=frame, read_ms=4.25, reason="frame_ready")

    capture = StubCaptureService(read_latest)
    stage, _, health, captured_at, restarts, _, mailbox = build_stage(
        capture,
        running=running,
    )

    stage.run()

    queued = mailbox.get_latest()
    assert queued is not None
    assert queued.frame is frame
    assert queued.captured_at == FIXED_NOW
    assert queued.read_ms == pytest.approx(4.25)
    assert queued.reason == "frame_ready"
    assert capture.open_calls == 1
    assert capture.read_drop_frames == [3]
    assert captured_at == [FIXED_NOW]
    assert restarts == []
    assert ("running", "capture_opened") in health["transitions"]
    assert mailbox.is_closed is True


def test_capture_stage_waits_for_gateway_frame_without_reconnecting():
    running = {"value": True}
    capture = StubCaptureService(
        lambda: CaptureReadResult(
            ok=False,
            frame=None,
            read_ms=1.5,
            reason="no_frame_ready",
        )
    )
    stage, _, health, _, restarts, logger, mailbox = build_stage(
        capture,
        running=running,
        sleep=lambda _seconds: running.update(value=False),
    )

    stage.run()

    assert capture.reconnect_calls == 0
    assert restarts == []
    assert ("running", "gateway_frames_waiting") in health["transitions"]
    assert logger.warning_calls == []
    assert mailbox.is_closed is True


def test_capture_stage_reports_reconnect_result_and_restart_metadata():
    running = {"value": True}
    reconnect = ReconnectResult(
        recovered=False,
        exhausted=True,
        attempts=4,
        reason="reconnect_failed",
        delay_seconds=0.0,
        status="offline",
    )
    capture = StubCaptureService(
        lambda: CaptureReadResult(
            ok=False,
            frame=None,
            read_ms=9.0,
            reason="read_timeout",
        ),
        reconnect_result=reconnect,
    )
    stage, _, health, _, restarts, logger, _ = build_stage(
        capture,
        running=running,
        sleep=lambda _seconds: running.update(value=False),
    )

    stage.run()

    assert capture.reconnect_calls == 1
    assert restarts == [(1, FIXED_NOW, "reconnect_failed")]
    assert ("degraded", "read_timeout") in health["transitions"]
    assert health["transitions"][-1] == ("offline", "reconnect_failed")
    assert len(logger.warning_calls) == 1


def test_capture_stage_contains_fatal_failure_and_closes_mailbox():
    def fail_read():
        raise RuntimeError("decoder failed")

    capture = StubCaptureService(fail_read)
    stage, _, health, _, _, logger, mailbox = build_stage(capture)

    stage.run()

    assert health["transitions"][-1] == ("degraded", "capture_stage_failed")
    assert len(logger.exception_calls) == 1
    assert mailbox.is_closed is True


def test_capture_stage_start_is_idempotent_and_stop_joins_owned_thread():
    created_threads = []

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            self.alive = False
            self.join_timeout = None
            created_threads.append(self)

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            self.join_timeout = timeout
            self.alive = False

    capture = StubCaptureService(
        lambda: CaptureReadResult(ok=False, frame=None, read_ms=0.0)
    )
    stage, _, _, _, _, _, mailbox = build_stage(
        capture,
        thread_factory=FakeThread,
    )

    assert stage.start() is True
    assert stage.start() is False
    assert len(created_threads) == 1
    assert created_threads[0].name == "camera-capture-7"
    assert created_threads[0].daemon is True
    assert stage.started_at == FIXED_NOW

    stage.stop(timeout=2.5)
    stage.release()

    assert created_threads[0].join_timeout == pytest.approx(2.5)
    assert mailbox.is_closed is True
    assert capture.release_calls == 1


def test_worker_base_delegates_capture_loop_without_reverse_dependency():
    worker_tree = ast.parse(
        (PROJECT_ROOT / "app/runtime/worker_base.py").read_text(encoding="utf-8")
    )
    worker_class = next(
        node
        for node in worker_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BaseCameraWorker"
    )
    worker_methods = {
        node.name for node in worker_class.body if isinstance(node, ast.FunctionDef)
    }
    assert "_capture_stage_loop" not in worker_methods

    stage_tree = ast.parse(
        (PROJECT_ROOT / "app/runtime/worker_capture_stage.py").read_text(
            encoding="utf-8"
        )
    )
    imported_modules = {
        node.module
        for node in ast.walk(stage_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "app.runtime.worker_base" not in imported_modules
