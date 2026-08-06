from types import SimpleNamespace

from app.services import media_backbone_reconciler as reconciler_module
from app.services.media_backbone_reconciler import MediaBackboneReconciler


class _Query:
    def __init__(self, cameras):
        self._cameras = cameras

    def filter(self, *_args):
        return self

    def all(self):
        return self._cameras


class _Session:
    def __init__(self, cameras):
        self._cameras = cameras

    def query(self, *_args):
        return _Query(self._cameras)

    def close(self):
        return None


def test_missing_mediamtx_path_invalidates_registration_cache(monkeypatch):
    camera = SimpleNamespace(id=36, rtsp_url="rtsp://origin/sub")
    invalidated = []
    ensured = []
    monkeypatch.setattr(
        reconciler_module,
        "SessionLocal",
        lambda: _Session([camera]),
    )
    monkeypatch.setattr(
        reconciler_module,
        "list_webrtc_camera_paths",
        lambda: {"ok": True, "items": []},
    )
    monkeypatch.setattr(
        reconciler_module,
        "invalidate_webrtc_camera_path_cache",
        lambda camera_id: invalidated.append(camera_id),
    )
    monkeypatch.setattr(
        reconciler_module,
        "ensure_camera_media_path",
        lambda camera_id, source: ensured.append((camera_id, source))
        or SimpleNamespace(ok=True, created=True, updated=False),
    )
    monkeypatch.setattr(
        reconciler_module.settings,
        "media_backbone_reconcile_enabled",
        True,
    )
    monkeypatch.setattr(
        reconciler_module.settings,
        "media_backbone_remove_orphan_paths",
        True,
    )

    result = MediaBackboneReconciler().reconcile_once()

    assert result["ok"] is True
    assert result["created_or_updated"] == 1
    assert invalidated == [36]
    assert ensured == [(36, "rtsp://origin/sub")]
