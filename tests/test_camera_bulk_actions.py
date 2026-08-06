from pathlib import Path
from types import SimpleNamespace

from app.services import camera_operation_service


def _camera(camera_id: int):
    return SimpleNamespace(
        id=camera_id,
        name=f"Camera {camera_id}",
        rtsp_url=f"rtsp://camera/{camera_id}",
        status="idle",
        is_deleted=False,
    )


def test_normalize_camera_bulk_ids_deduplicates_and_ignores_invalid_values():
    assert camera_operation_service.normalize_camera_bulk_ids(["3", 2, "3", "invalid", 0, -1, "4"]) == [3, 2, 4]


def test_apply_camera_bulk_start_counts_only_started_cameras(monkeypatch):
    cameras = [_camera(1), _camera(2), _camera(3)]
    started = []

    def fake_start(camera, **kwargs):
        started.append((camera.id, kwargs))
        return camera.id != 2

    monkeypatch.setattr(camera_operation_service, "start_camera_worker", fake_start)

    processed = camera_operation_service.apply_camera_bulk_action(None, cameras, "start")

    assert processed == 2
    assert [camera_id for camera_id, _ in started] == [1, 2, 3]
    assert all(options["use_motion_test"] is True for _, options in started)


def test_apply_camera_bulk_stop_updates_status(monkeypatch):
    cameras = [_camera(4), _camera(5)]
    stopped = []
    monkeypatch.setattr(camera_operation_service, "stop_camera_runtime", lambda camera_id: stopped.append(camera_id))

    processed = camera_operation_service.apply_camera_bulk_action(None, cameras, "stop")

    assert processed == 2
    assert stopped == [4, 5]
    assert [camera.status for camera in cameras] == ["stopped_manual", "stopped_manual"]


def test_apply_camera_bulk_delete_reuses_soft_delete(monkeypatch):
    cameras = [_camera(6), _camera(7)]
    deleted = []

    def fake_soft_delete(db, camera, *, deleted_at=None):
        deleted.append((db, camera.id, deleted_at))

    monkeypatch.setattr(camera_operation_service, "soft_delete_camera_record", fake_soft_delete)
    fake_db = object()

    processed = camera_operation_service.apply_camera_bulk_action(fake_db, cameras, "delete")

    assert processed == 2
    assert [camera_id for _, camera_id, _ in deleted] == [6, 7]
    assert deleted[0][2] is not None
    assert deleted[0][2] == deleted[1][2]


def test_cameras_template_has_bulk_selection_and_filters():
    template = Path("templates/cameras.html").read_text(encoding="utf-8")

    assert 'action="/cameras/bulk-action"' in template
    assert 'name="camera_ids"' in template
    assert 'id="cameraSelectVisible"' in template
    assert 'id="cameraSearch"' in template
    assert 'id="cameraSiteFilter"' in template
    assert 'id="cameraGroupFilter"' in template
    assert 'id="cameraStatusFilter"' in template
    assert 'value="EXCLUIR SELECIONADAS"' not in template
    assert 'deleteConfirmation.value = "EXCLUIR SELECIONADAS"' in template
