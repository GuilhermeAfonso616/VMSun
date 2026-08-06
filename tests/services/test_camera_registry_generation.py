from app.services.camera_registry import CameraRegistry


class FakeWorker:
    def __init__(self, generation: str, *, alive: bool = True, pid: int = 100):
        self.generation = generation
        self._alive = alive
        self.pid = pid
        self.exitcode = None if alive else 1
        self.mode_name = "motion_test"
        self.is_stop_requested = False

    def is_alive(self):
        return self._alive


def test_dead_record_is_retained_for_diagnostics():
    registry = CameraRegistry()
    worker = FakeWorker("dead-generation", alive=False)

    registry.set_worker(7, worker)

    assert registry.get_worker(7) is worker
    assert registry.list_workers() == []
    assert registry.snapshot()[0]["exitcode"] == 1


def test_stale_generation_cannot_remove_current_worker():
    registry = CameraRegistry()
    old = FakeWorker("generation-old", pid=101)
    current = FakeWorker("generation-current", pid=202)

    registry.set_worker(7, old)
    registry.set_worker(7, current)

    assert registry.remove_worker(7, expected_generation="generation-old") is False
    assert registry.get_worker(7) is current
    assert registry.remove_worker(7, expected_generation="generation-current") is True
    assert registry.get_worker(7) is None
