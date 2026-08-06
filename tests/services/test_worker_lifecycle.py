import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.services import worker_lifecycle as lifecycle_module
from app.services.camera_registry import CameraRegistry
from app.services.worker_lifecycle import WorkerLifecycleError, WorkerLifecycleManager
from app.services.worker_ownership_store import WorkerOwnershipStore


class AllowedGuard:
    allowed = True

    @staticmethod
    def as_dict():
        return {"allowed": True}


class FakeController:
    created = []
    created_lock = threading.Lock()
    next_pid = 1000

    def __init__(self, camera_id, rtsp_url, use_motion_test=True, *, stop_fails=False):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.generation = uuid.uuid4().hex
        self.pid = None
        self.exitcode = None
        self.mode_name = "motion_test"
        self.is_stop_requested = False
        self._alive = False
        self.stop_fails = stop_fails
        with self.created_lock:
            self.created.append(self)

    def start(self):
        time.sleep(0.005)
        with self.created_lock:
            type(self).next_pid += 1
            self.pid = type(self).next_pid
        self._alive = True

    def stop(self, timeout=5.0):
        self.is_stop_requested = True
        if not self.stop_fails:
            self._alive = False

    def is_alive(self):
        return self._alive


@pytest.fixture(autouse=True)
def allow_worker_start(monkeypatch):
    monkeypatch.setattr(lifecycle_module, "evaluate_worker_start_guard", lambda **_kwargs: AllowedGuard())
    FakeController.created = []


def build_manager(tmp_path, registry=None, factory=FakeController):
    return WorkerLifecycleManager(
        worker_registry=registry or CameraRegistry(),
        controller_factory=factory,
        ownership_store=WorkerOwnershipStore(tmp_path),
    )


def test_concurrent_start_creates_exactly_one_worker(tmp_path):
    manager = build_manager(tmp_path)
    camera = SimpleNamespace(id=41, rtsp_url="rtsp://camera/41", status="running", auto_start_enabled=True)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: manager.start(camera, restart_existing=False), range(8)))

    assert sum(result.action == "started" for result in results) == 1
    assert sum(result.action == "already_running" for result in results) == 7
    assert len(FakeController.created) == 1
    assert len(manager.registry.list_workers()) == 1


def test_restart_refuses_to_spawn_when_previous_worker_survives(tmp_path):
    registry = CameraRegistry()
    previous = FakeController(41, "rtsp://camera/41", stop_fails=True)
    previous.start()
    registry.set_worker(41, previous)
    manager = build_manager(tmp_path, registry=registry)
    manager.ownership_store.claim(41, generation=previous.generation, pid=previous.pid)
    camera = SimpleNamespace(id=41, rtsp_url="rtsp://camera/41", status="running", auto_start_enabled=True)

    with pytest.raises(WorkerLifecycleError, match="previous_worker_still_alive"):
        manager.start(camera, restart_existing=True)

    assert registry.get_worker(41) is previous
    assert len(FakeController.created) == 1


def test_start_failure_releases_ownership_and_registry(tmp_path):
    class FailingController(FakeController):
        def start(self):
            raise RuntimeError("spawn failed")

    manager = build_manager(tmp_path, factory=FailingController)
    camera = SimpleNamespace(id=41, rtsp_url="rtsp://camera/41", status="running", auto_start_enabled=True)

    with pytest.raises(WorkerLifecycleError, match="worker_start_failed"):
        manager.start(camera)

    assert manager.registry.get_record(41) is None
    assert manager.ownership_store.get(41) is None


def test_stop_releases_camera_from_inference_pool(tmp_path, monkeypatch):
    released: list[int] = []
    monkeypatch.setattr(lifecycle_module, "_release_inference_camera", released.append)
    manager = build_manager(tmp_path)
    camera = SimpleNamespace(id=41, rtsp_url="rtsp://camera/41", status="running", auto_start_enabled=True)
    manager.start(camera)

    result = manager.stop(41, reason="ia_disabled")

    assert result.action == "stopped"
    assert released == [41]
    assert manager.registry.get_record(41) is None


def test_disabled_camera_without_worker_clears_stale_pool_assignment(tmp_path, monkeypatch):
    released: list[int] = []
    monkeypatch.setattr(lifecycle_module, "_release_inference_camera", released.append)
    manager = build_manager(tmp_path)
    camera = SimpleNamespace(id=41, rtsp_url="rtsp://camera/41", status="stopped_manual", auto_start_enabled=False)

    result = manager.reconcile(camera, recover=True)

    assert result.action == "desired_stopped"
    assert released == [41]
