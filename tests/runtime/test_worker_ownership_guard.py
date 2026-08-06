from __future__ import annotations

from app.runtime.worker_ownership_guard import WorkerOwnershipGuard


class StubOwnershipStore:
    def __init__(self, *, record=None, is_owner_result=None, raise_on_get=False):
        self._record = record
        self._is_owner_result = is_owner_result
        self._raise_on_get = raise_on_get

    def get(self, camera_id):
        if self._raise_on_get:
            raise RuntimeError("boom")
        return self._record

    def is_owner(self, camera_id, *, generation, pid):
        return self._is_owner_result


class StubMetricsStore:
    def __init__(self, *, metrics=None):
        self._metrics = metrics or {}

    def get_metrics(self, camera_id):
        return self._metrics


def build_guard(*, ownership_store, metrics_store_backend):
    return WorkerOwnershipGuard(
        camera_id=41,
        process_pid=100,
        worker_generation="current-generation",
        ownership_store=ownership_store,
        metrics_store_backend=metrics_store_backend,
    )


def test_owner_when_ownership_record_matches():
    guard = build_guard(
        ownership_store=StubOwnershipStore(
            record={"generation": "current-generation", "pid": 100},
            is_owner_result=True,
        ),
        metrics_store_backend=StubMetricsStore(),
    )
    assert guard.is_owner() is True


def test_not_owner_when_ownership_record_diverges():
    guard = build_guard(
        ownership_store=StubOwnershipStore(
            record={"generation": "newer-generation", "pid": 200},
            is_owner_result=False,
        ),
        metrics_store_backend=StubMetricsStore(),
    )
    assert guard.is_owner() is False


def test_fallback_owner_when_no_record_and_metrics_match():
    guard = build_guard(
        ownership_store=StubOwnershipStore(record=None),
        metrics_store_backend=StubMetricsStore(
            metrics={"worker_generation": "current-generation", "worker_pid": 100}
        ),
    )
    assert guard.is_owner() is True


def test_fallback_not_owner_when_metrics_generation_diverges():
    guard = build_guard(
        ownership_store=StubOwnershipStore(record=None),
        metrics_store_backend=StubMetricsStore(
            metrics={"worker_generation": "newer-generation", "worker_pid": 100}
        ),
    )
    assert guard.is_owner() is False


def test_fallback_not_owner_when_metrics_pid_diverges():
    guard = build_guard(
        ownership_store=StubOwnershipStore(record=None),
        metrics_store_backend=StubMetricsStore(
            metrics={"worker_generation": "current-generation", "worker_pid": 999}
        ),
    )
    assert guard.is_owner() is False


def test_fallback_owner_when_no_record_and_no_metrics():
    guard = build_guard(
        ownership_store=StubOwnershipStore(record=None),
        metrics_store_backend=StubMetricsStore(metrics={}),
    )
    assert guard.is_owner() is True


def test_fail_open_when_ownership_store_raises():
    guard = build_guard(
        ownership_store=StubOwnershipStore(raise_on_get=True),
        metrics_store_backend=StubMetricsStore(),
    )
    assert guard.is_owner() is True
