from types import SimpleNamespace
from datetime import datetime

from app.services import camera_health_monitor as health_module
from app.services.camera_health_monitor import CameraHealthMonitor, _startup_reconcile_action


def test_startup_restores_camera_with_explicit_auto_start_even_when_stopped():
    camera = SimpleNamespace(status="stopped", auto_start_enabled=True)

    assert _startup_reconcile_action(camera, restore_running_workers=False) == "restore"


def test_startup_restores_legacy_running_camera_when_global_restore_is_enabled():
    camera = SimpleNamespace(status="running_motion_test", auto_start_enabled=False)

    assert _startup_reconcile_action(camera, restore_running_workers=True) == "restore"


def test_startup_normalizes_legacy_running_camera_when_global_restore_is_disabled():
    camera = SimpleNamespace(status="running", auto_start_enabled=False)

    assert _startup_reconcile_action(camera, restore_running_workers=False) == "normalize"


def test_startup_does_not_restore_inactive_camera_even_with_auto_start_flag():
    camera = SimpleNamespace(status="disabled", auto_start_enabled=True)

    assert _startup_reconcile_action(camera, restore_running_workers=True) == "skip"


def test_startup_does_not_restore_manual_stop_even_with_global_restore_enabled():
    camera = SimpleNamespace(status="stopped_manual", auto_start_enabled=False)

    assert _startup_reconcile_action(camera, restore_running_workers=True) == "skip"


def test_dead_worker_restarts_on_first_health_cycle(monkeypatch):
    class DeadWorker:
        camera_id = 99
        process_pid = 1234
        pid = 1234
        exitcode = 1
        mode_name = "motion_test"
        is_stop_requested = False

        def __init__(self):
            self.stop_calls = 0
            self.start_calls = 0

        def is_alive(self):
            return False

        def stop(self, timeout=5.0):
            self.stop_calls += 1

        def start(self):
            self.start_calls += 1

    worker = DeadWorker()
    camera = SimpleNamespace(
        id=99,
        name="Camera Teste",
        status="running_motion_test",
        rtsp_url="rtsp://camera/test",
    )
    monitor = CameraHealthMonitor()

    record = SimpleNamespace(worker=worker, generation="dead-generation")
    restarted_record = SimpleNamespace(pid=5678, generation="new-generation")
    monkeypatch.setattr(health_module.registry, "get_record", lambda camera_id: record)
    monkeypatch.setattr(health_module.registry, "list_workers", lambda: [worker])
    def fake_restart(*_args, **_kwargs):
        worker.stop()
        worker.start()
        return SimpleNamespace(record=restarted_record)

    monkeypatch.setattr(health_module.worker_lifecycle_manager, "start", fake_restart)
    monkeypatch.setattr(health_module.metrics_store, "get_metrics", lambda camera_id: {})
    monkeypatch.setattr(health_module.frame_store, "get_raw_frame_metadata", lambda camera_id: {})
    monkeypatch.setattr(health_module.frame_store, "get_processed_frame_metadata", lambda camera_id: {})
    monkeypatch.setattr(health_module, "gateway_is_enabled", lambda: False)
    monkeypatch.setattr(health_module.settings, "camera_health_restart_after_stall_checks", 8)
    monkeypatch.setattr(health_module.settings, "camera_health_restart_cooldown_seconds", 0.0)

    snapshot = monitor._collect_camera_snapshot(camera, datetime(2026, 7, 9, 21, 0, 0))

    assert worker.stop_calls == 1
    assert worker.start_calls == 1
    assert snapshot["health_status"] == "reconnecting"
    assert snapshot["last_restart_reason"] == "worker_process_exit"
    assert snapshot["restart_count"] == 1
