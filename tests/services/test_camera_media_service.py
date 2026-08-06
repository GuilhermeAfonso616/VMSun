from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.services import camera_media_service as media
from app.services.media_backbone_service import MediaBackboneUnavailable


def test_status_frame_can_be_encoded_as_jpeg():
    frame = media.build_stream_status_frame("OFFLINE", "Aguardando frame", width=320, height=180)
    encoded = media.frame_to_jpeg_bytes(frame)

    assert frame.shape == (180, 320, 3)
    assert encoded is not None
    assert encoded.startswith(b"\xff\xd8")


def test_raw_stream_replaces_stale_local_frame_with_placeholder(monkeypatch):
    monkeypatch.setattr(media, "remote_runtime_enabled", lambda: False)
    monkeypatch.setattr(
        media.frame_store,
        "get_raw_frame_metadata",
        lambda _camera_id: {
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=30)
        },
    )
    monkeypatch.setattr(media.frame_store, "get_raw_jpeg", lambda _camera_id: b"stale")
    monkeypatch.setattr(media, "build_stream_status_frame", lambda *_args, **_kwargs: "placeholder")
    monkeypatch.setattr(media, "frame_to_jpeg_bytes", lambda frame: b"placeholder" if frame else None)

    assert media.get_raw_stream_bytes(7) == b"placeholder"


def test_processed_remote_stream_uses_placeholder_when_runtime_has_no_frame(monkeypatch):
    calls = []
    monkeypatch.setattr(media, "remote_runtime_enabled", lambda: True)
    monkeypatch.setattr(
        media,
        "fetch_runtime_camera_frame",
        lambda camera_id, kind: calls.append((camera_id, kind)) or None,
    )
    monkeypatch.setattr(media, "build_stream_status_frame", lambda *_args, **_kwargs: "placeholder")
    monkeypatch.setattr(media, "frame_to_jpeg_bytes", lambda _frame: b"processed-placeholder")

    assert media.get_processed_stream_bytes(8) == b"processed-placeholder"
    assert calls == [(8, "processed")]


def test_boxed_stream_filters_tracks_below_visual_threshold(monkeypatch):
    rendered = []
    monkeypatch.setattr(media, "get_raw_stream_bytes", lambda _camera_id: b"raw-jpeg")
    monkeypatch.setattr(
        media.track_store,
        "get_tracks",
        lambda *_args, **_kwargs: {
            "tracks": [
                {"track_id": 1, "confidence": 0.8},
                {"track_id": 2, "confidence": 0.3},
                {"track_id": 3, "confidence": "invalid"},
            ]
        },
    )
    monkeypatch.setattr(
        media,
        "render_tracks_on_jpeg",
        lambda jpg, payload: rendered.append((jpg, payload)) or b"boxed-jpeg",
    )

    result = media.get_boxed_stream_bytes(9, 0.5)

    assert result == b"boxed-jpeg"
    assert rendered[0][0] == b"raw-jpeg"
    assert rendered[0][1]["tracks"] == [{"track_id": 1, "confidence": 0.8}]


def test_snapshot_preview_is_acquired_and_released(monkeypatch):
    calls = []
    monkeypatch.setattr(media, "remote_runtime_enabled", lambda: False)
    monkeypatch.setattr(media.registry, "get_worker", lambda _camera_id: None)
    monkeypatch.setattr(
        media.preview_stream_manager,
        "acquire",
        lambda camera_id, url: calls.append(("acquire", camera_id, url)),
    )
    monkeypatch.setattr(
        media.preview_stream_manager,
        "get_jpeg",
        lambda camera_id: calls.append(("jpeg", camera_id)) or b"preview-jpeg",
    )
    monkeypatch.setattr(
        media.preview_stream_manager,
        "release",
        lambda camera_id: calls.append(("release", camera_id)),
    )

    result = media.get_camera_snapshot_bytes(10, "rtsp://camera/main")

    assert result == b"preview-jpeg"
    assert calls == [
        ("acquire", 10, "rtsp://camera/main"),
        ("jpeg", 10),
        ("release", 10),
    ]


def test_snapshot_remote_runtime_without_frame_uses_backbone_preview(monkeypatch):
    calls = []
    monkeypatch.setattr(media, "remote_runtime_enabled", lambda: True)
    monkeypatch.setattr(media, "fetch_runtime_camera_frame", lambda *_args: None)
    monkeypatch.setattr(media.registry, "get_worker", lambda _camera_id: None)
    monkeypatch.setattr(
        media,
        "resolve_camera_gateway_source_url",
        lambda camera_id, _url: f"rtsp://webrtc-gateway:8554/cam_{camera_id}",
    )
    monkeypatch.setattr(
        media.preview_stream_manager,
        "acquire",
        lambda camera_id, url: calls.append(("acquire", camera_id, url)),
    )
    monkeypatch.setattr(
        media.preview_stream_manager,
        "get_jpeg",
        lambda camera_id: calls.append(("jpeg", camera_id)) or b"backbone-preview",
    )
    monkeypatch.setattr(
        media.preview_stream_manager,
        "release",
        lambda camera_id: calls.append(("release", camera_id)),
    )

    result = media.get_camera_snapshot_bytes(10, "rtsp://origin/private")

    assert result == b"backbone-preview"
    assert calls == [
        ("acquire", 10, "rtsp://webrtc-gateway:8554/cam_10"),
        ("jpeg", 10),
        ("release", 10),
    ]


def test_snapshot_propagates_strict_backbone_failure(monkeypatch):
    monkeypatch.setattr(media, "remote_runtime_enabled", lambda: True)
    monkeypatch.setattr(media, "fetch_runtime_camera_frame", lambda *_args: None)
    monkeypatch.setattr(media.registry, "get_worker", lambda _camera_id: None)

    def unavailable(*_args, **_kwargs):
        raise MediaBackboneUnavailable(
            "media_backbone_unavailable",
            "MediaMTX indisponivel",
        )

    monkeypatch.setattr(media, "resolve_camera_gateway_source_url", unavailable)

    with pytest.raises(MediaBackboneUnavailable):
        media.get_camera_snapshot_bytes(10, "rtsp://origin/private")


def test_mjpeg_generator_wraps_bytes_and_uses_expected_boundary(monkeypatch):
    monkeypatch.setattr(media, "remote_runtime_enabled", lambda: False)
    monkeypatch.setattr(media.time, "sleep", lambda _seconds: None)
    generator = media.generate_mjpeg_bytes(lambda camera_id: f"jpeg-{camera_id}".encode(), 11)

    chunk = next(generator)
    generator.close()

    assert chunk == b"--frame\r\nContent-Type: image/jpeg\r\n\r\njpeg-11\r\n"


def test_raw_preview_generator_releases_preview_when_client_disconnects(monkeypatch):
    calls = []
    monkeypatch.setattr(media, "remote_runtime_enabled", lambda: False)
    monkeypatch.setattr(media.registry, "get_worker", lambda _camera_id: None)
    monkeypatch.setattr(media, "build_stream_status_frame", lambda *_args, **_kwargs: np.zeros((1, 1, 3)))
    monkeypatch.setattr(
        media.preview_stream_manager,
        "acquire",
        lambda camera_id, url: calls.append(("acquire", camera_id, url)),
    )
    monkeypatch.setattr(media.preview_stream_manager, "get_jpeg", lambda _camera_id: b"preview")
    monkeypatch.setattr(
        media.preview_stream_manager,
        "release",
        lambda camera_id: calls.append(("release", camera_id)),
    )
    monkeypatch.setattr(media.time, "sleep", lambda _seconds: None)
    generator = media.generate_camera_raw_mjpeg(12, "rtsp://camera/preview")

    assert b"preview" in next(generator)
    generator.close()

    assert calls == [
        ("acquire", 12, "rtsp://camera/preview"),
        ("release", 12),
    ]


def test_raw_remote_preview_falls_back_only_to_resolved_backbone(monkeypatch):
    calls = []
    monkeypatch.setattr(media, "remote_runtime_enabled", lambda: True)
    monkeypatch.setattr(media, "get_raw_stream_bytes", lambda _camera_id: None)
    monkeypatch.setattr(
        media,
        "resolve_camera_gateway_source_url",
        lambda camera_id, _url: f"rtsp://webrtc-gateway:8554/cam_{camera_id}",
    )
    monkeypatch.setattr(media, "build_stream_status_frame", lambda *_args, **_kwargs: np.zeros((1, 1, 3)))
    monkeypatch.setattr(
        media.preview_stream_manager,
        "acquire",
        lambda camera_id, url: calls.append(("acquire", camera_id, url)),
    )
    monkeypatch.setattr(media.preview_stream_manager, "get_jpeg", lambda _camera_id: b"preview")
    monkeypatch.setattr(
        media.preview_stream_manager,
        "release",
        lambda camera_id: calls.append(("release", camera_id)),
    )
    monkeypatch.setattr(media.time, "sleep", lambda _seconds: None)
    generator = media.generate_camera_raw_mjpeg(13, "rtsp://origin/private")

    assert b"preview" in next(generator)
    generator.close()

    assert calls == [
        ("acquire", 13, "rtsp://webrtc-gateway:8554/cam_13"),
        ("release", 13),
    ]
