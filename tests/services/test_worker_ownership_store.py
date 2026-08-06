from app.services.worker_ownership_store import WorkerOwnershipStore


def test_new_claim_fences_old_generation(tmp_path):
    store = WorkerOwnershipStore(tmp_path)
    store.claim(41, generation="old", pid=100)
    assert store.is_owner(41, generation="old", pid=100)

    store.claim(41, generation="new", pid=200)

    assert not store.is_owner(41, generation="old", pid=100)
    assert store.is_owner(41, generation="new", pid=200)
    assert not store.release(41, expected_generation="old", expected_pid=100)
    assert store.is_owner(41, generation="new", pid=200)


def test_release_is_conditional_by_generation_and_pid(tmp_path):
    store = WorkerOwnershipStore(tmp_path)
    store.claim(42, generation="generation", pid=300)

    assert not store.release(42, expected_generation="generation", expected_pid=301)
    assert store.release(42, expected_generation="generation", expected_pid=300)
    assert store.get(42) is None


def test_worker_base_rejects_stale_generation_before_first_new_metric(monkeypatch):
    from app.runtime.worker_base import BaseCameraWorker
    from app.services.worker_ownership_store import worker_ownership_store

    worker = BaseCameraWorker.__new__(BaseCameraWorker)
    worker.camera_id = 41
    worker.worker_generation = "old-generation"
    worker.process_pid = 100

    monkeypatch.setattr(
        worker_ownership_store,
        "get",
        lambda camera_id: {"camera_id": camera_id, "generation": "new-generation", "pid": 200},
    )
    monkeypatch.setattr(worker_ownership_store, "is_owner", lambda *args, **kwargs: False)

    assert worker._owns_published_runtime_state() is False
