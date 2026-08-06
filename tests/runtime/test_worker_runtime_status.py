from __future__ import annotations

from app.runtime.worker_runtime_status import WorkerRuntimeStatus


class FakeCamera:
    id = None

    def __init__(self, status: str):
        self.status = status


class FakeQuery:
    def __init__(self, camera):
        self._camera = camera

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._camera


class FakeDb:
    def __init__(self, camera=None):
        self.camera = camera
        self.commit_calls = 0

    def query(self, model):
        return FakeQuery(self.camera)

    def commit(self):
        self.commit_calls += 1


class StubLogger:
    def __init__(self):
        self.info_calls = []
        self.warning_calls = []

    def info(self, *args, **kwargs):
        self.info_calls.append((args, kwargs))

    def warning(self, *args, **kwargs):
        self.warning_calls.append((args, kwargs))


def build_status(
    *,
    owns_runtime_state=lambda: True,
    stop_capture_stage=None,
    release_capture_stage=None,
    stop_event_persistence=None,
    clear_published_state=None,
):
    calls = []

    def recorder(name):
        def _call():
            calls.append(name)
        return _call

    logger = StubLogger()
    status = WorkerRuntimeStatus(
        camera_id=41,
        logger=logger,
        worker_pid=100,
        worker_generation="current-generation",
        owns_runtime_state=owns_runtime_state,
        stop_capture_stage=stop_capture_stage or recorder("stop_capture_stage"),
        release_capture_stage=release_capture_stage or recorder("release_capture_stage"),
        stop_event_persistence=stop_event_persistence or recorder("stop_event_persistence"),
        clear_published_state=clear_published_state or recorder("clear_published_state"),
        camera_model=FakeCamera,
    )
    return status, logger, calls


def test_mark_starting_sets_status_and_commits_when_camera_present():
    status, logger, _ = build_status()
    camera = FakeCamera("stopped")
    db = FakeDb(camera)

    status.mark_starting(db, camera)

    assert camera.status == "starting"
    assert db.commit_calls == 1
    assert logger.info_calls


def test_mark_starting_is_noop_without_camera():
    status, logger, _ = build_status()
    db = FakeDb(None)

    status.mark_starting(db, None)

    assert db.commit_calls == 0
    assert not logger.info_calls


def test_mark_warming_up_sets_status_and_commits():
    status, _, _ = build_status()
    camera = FakeCamera("starting")
    db = FakeDb(camera)

    status.mark_warming_up(db, camera)

    assert camera.status == "warming_up"
    assert db.commit_calls == 1


def test_mark_running_on_first_frame_is_idempotent():
    status, _, _ = build_status()
    camera = FakeCamera("running")
    db = FakeDb(camera)

    result = status.mark_running_on_first_frame(db, camera)

    assert result is camera
    assert db.commit_calls == 0


def test_mark_running_on_first_frame_commits_on_transition():
    status, logger, _ = build_status()
    camera = FakeCamera("warming_up")
    db = FakeDb(camera)

    status.mark_running_on_first_frame(db, camera)

    assert camera.status == "running"
    assert db.commit_calls == 1
    assert logger.info_calls


def test_mark_fatal_error_writes_status_when_owner():
    status, _, _ = build_status(owns_runtime_state=lambda: True)
    camera = FakeCamera("running")
    db = FakeDb(camera)

    status.mark_fatal_error(db)

    assert camera.status == "error: worker fatal"
    assert db.commit_calls == 1


def test_mark_fatal_error_skips_write_when_not_owner():
    status, logger, _ = build_status(owns_runtime_state=lambda: False)
    camera = FakeCamera("running")
    db = FakeDb(camera)

    status.mark_fatal_error(db)

    assert camera.status == "running"
    assert db.commit_calls == 0
    assert logger.warning_calls


def test_cleanup_stops_and_clears_state_in_order_when_owner():
    status, _, calls = build_status(owns_runtime_state=lambda: True)
    camera = FakeCamera("running")
    db = FakeDb(camera)

    status.cleanup(db)

    assert calls == [
        "stop_capture_stage",
        "stop_event_persistence",
        "release_capture_stage",
        "clear_published_state",
    ]
    assert camera.status == "stopped"
    assert db.commit_calls == 1


def test_cleanup_skips_clear_and_status_write_when_not_owner():
    status, logger, calls = build_status(owns_runtime_state=lambda: False)
    camera = FakeCamera("running")
    db = FakeDb(camera)

    status.cleanup(db)

    assert calls == [
        "stop_capture_stage",
        "stop_event_persistence",
        "release_capture_stage",
    ]
    assert camera.status == "running"
    assert db.commit_calls == 0
    assert logger.info_calls


def test_cleanup_does_not_overwrite_existing_error_status():
    status, _, _ = build_status(owns_runtime_state=lambda: True)
    camera = FakeCamera("error: worker fatal")
    db = FakeDb(camera)

    status.cleanup(db)

    assert camera.status == "error: worker fatal"
    assert db.commit_calls == 0
