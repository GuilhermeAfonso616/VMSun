from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.internal import routes


def test_internal_camera_frame_rejects_stale_runtime_frame(monkeypatch):
    monkeypatch.setattr(
        routes.frame_store,
        "get_raw_frame_metadata",
        lambda _camera_id: {
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(seconds=30)
        },
    )
    monkeypatch.setattr(
        routes.frame_store,
        "get_raw_jpeg",
        lambda _camera_id: b"stale-jpeg",
    )

    with pytest.raises(HTTPException) as exc:
        routes.internal_camera_frame(36, "raw")

    assert exc.value.status_code == 404
    assert exc.value.detail == "Frame expirado"
