from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from app.core.config import settings
from app.services.event_snapshot_store import EventSnapshotStore
from app.web.routes.event_actions_routes import _browser_compatible_clip_file, _snapshot_display_response


def _has_green_bbox_pixels(frame) -> bool:
    mask = (frame[:, :, 1] > 200) & (frame[:, :, 0] < 60) & (frame[:, :, 2] < 60)
    return bool(mask.any())


def _has_orange_bbox_pixels(frame) -> bool:
    mask = (frame[:, :, 2] > 180) & (frame[:, :, 1] > 90) & (frame[:, :, 0] < 80)
    return bool(mask.any())


def test_event_snapshot_store_saves_clean_frame_for_revalidation(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "event_snapshots_dir", str(tmp_path))
    monkeypatch.setattr(settings, "save_event_snapshots", True)

    store = EventSnapshotStore()
    frame = np.zeros((48, 64, 3), dtype=np.uint8)

    snapshot_path = store.save(
        camera_id=1,
        frame=frame,
        event_type="person_entered",
        track_id=42,
        bbox=[8, 10, 40, 42],
    )

    assert snapshot_path
    saved = cv2.imread(snapshot_path)
    assert saved is not None
    assert not _has_green_bbox_pixels(saved)


def test_snapshot_route_draws_bbox_only_for_display(tmp_path):
    snapshot_path = tmp_path / "clean.jpg"
    clean_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    assert cv2.imwrite(str(snapshot_path), clean_frame)

    response = _snapshot_display_response(
        snapshot_path,
        json.dumps([8, 10, 40, 42]),
        clean=False,
    )

    annotated = cv2.imdecode(np.frombuffer(response.body, dtype=np.uint8), cv2.IMREAD_COLOR)
    saved_after_display = cv2.imread(str(snapshot_path))

    assert annotated is not None
    assert _has_orange_bbox_pixels(annotated)
    assert saved_after_display is not None
    assert not _has_orange_bbox_pixels(saved_after_display)


def test_clip_pair_saves_temporal_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "event_snapshots_dir", str(tmp_path))
    monkeypatch.setattr(settings, "save_event_snapshots", True)

    store = EventSnapshotStore()
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    event_at = datetime(2026, 5, 12, 7, 1, 26)

    clip_path = store.save_clip_pair(
        camera_id=1,
        frame_before=frame,
        frame_after=frame,
        event_type="person_entered",
        track_id=42,
        bbox=[8, 10, 40, 42],
        captured_at_before=event_at - timedelta(seconds=3),
        captured_at_event=event_at,
        captured_at_after=event_at + timedelta(seconds=3),
        video_frames=[frame, frame],
    )

    assert clip_path
    clip_dir = tmp_path / "camera_1" / next((tmp_path / "camera_1").iterdir()).name
    metadata = json.loads((clip_dir / "metadata.json").read_text())
    assert metadata["before_offset_seconds"] == -3.0
    assert metadata["after_offset_seconds"] == 3.0
    assert metadata["video_file"] == "clip.mp4"
    assert metadata["video_frame_count"] == 2
    assert (clip_dir / "clip.mp4").exists()


def test_clip_timeline_preserves_real_event_duration(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "event_snapshots_dir", str(tmp_path))
    monkeypatch.setattr(settings, "save_event_snapshots", True)
    monkeypatch.setattr(settings, "event_clip_video_fps", 5.0)

    store = EventSnapshotStore()
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    # Amostragem irregular, como o ring entrega na pratica (worker com jitter).
    offsets = [0.0, 1.1, 1.7, 3.0]

    clip_path = store.save_clip_pair(
        camera_id=3,
        frame_before=frame,
        frame_after=frame,
        event_type="person_entered",
        track_id=7,
        video_frames=[frame] * len(offsets),
        video_frame_offsets=offsets,
    )

    assert clip_path
    metadata = json.loads((Path(clip_path) / "metadata.json").read_text(encoding="utf-8"))
    # 3s de evento a 5 fps: o clipe dura o mesmo que o trecho gravado, em vez
    # dos 0,8s acelerados que 4 frames a 5 fps dariam.
    assert metadata["video_timeline"] == "real"
    assert metadata["video_source_frame_count"] == 4
    assert metadata["video_frame_count"] == 16
    assert metadata["video_duration_seconds"] == 3.2


def test_clip_timeline_holds_each_frame_until_the_next_one():
    # 1 fps de origem em saida de 4 fps: cada frame ocupa 4 quadros.
    timeline = EventSnapshotStore._build_video_timeline(3, [0.0, 1.0, 2.0], 4.0)

    assert timeline == [0, 0, 0, 0, 1, 1, 1, 1, 2]


def test_clip_timeline_falls_back_to_uniform_without_offsets():
    assert EventSnapshotStore._build_video_timeline(3, None, 5.0) == [0, 1, 2]
    assert EventSnapshotStore._build_video_timeline(3, [0.0, 0.0, 0.0], 5.0) == [0, 1, 2]
    assert EventSnapshotStore._build_video_timeline(3, [0.0, 1.0], 5.0) == [0, 1, 2]


def test_clip_pair_accepts_compressed_jpeg_frames(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "event_snapshots_dir", str(tmp_path))
    monkeypatch.setattr(settings, "save_event_snapshots", True)

    store = EventSnapshotStore()
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:, :, 1] = 120
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    assert ok

    clip_path = store.save_clip_pair(
        camera_id=2,
        frame_before=frame,
        frame_after=frame,
        event_type="person_entered",
        track_id=9,
        video_frames=[encoded.tobytes(), encoded.tobytes()],
    )

    assert clip_path
    clip_dir = Path(clip_path)
    metadata = json.loads((clip_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["video_frame_count"] == 2
    assert (clip_dir / "clip.mp4").exists()


def test_clip_pair_prefers_browser_compatible_h264(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "event_snapshots_dir", str(tmp_path))
    monkeypatch.setattr(settings, "save_event_snapshots", True)
    monkeypatch.setattr(shutil, "which", lambda name: "ffmpeg" if name == "ffmpeg" else None)

    def fake_run(command, capture_output, text, timeout):
        target = command[-1]
        with open(target, "wb") as handle:
            handle.write(b"h264")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("app.services.event_snapshot_store.subprocess.run", fake_run)

    store = EventSnapshotStore()
    frame = np.zeros((49, 65, 3), dtype=np.uint8)

    clip_path = store.save_clip_pair(
        camera_id=1,
        frame_before=frame,
        frame_after=frame,
        event_type="person_entered",
        track_id=42,
        video_frames=[frame, frame],
    )

    assert clip_path
    clip_dir = tmp_path / "camera_1" / next((tmp_path / "camera_1").iterdir()).name
    metadata = json.loads((clip_dir / "metadata.json").read_text())
    assert metadata["video_file"] == "clip.mp4"
    assert metadata["video_codec"] == "h264"
    assert (clip_dir / "clip.mp4").read_bytes() == b"h264"
    assert not (clip_dir / "clip.source.mp4").exists()


def test_event_clip_route_transcodes_legacy_mp4v_for_browser(monkeypatch, tmp_path):
    clip_dir = tmp_path / "clip"
    clip_dir.mkdir()
    source = clip_dir / "clip.mp4"
    source.write_bytes(b"mp4v")
    metadata_path = clip_dir / "metadata.json"
    metadata_path.write_text(json.dumps({"video_file": "clip.mp4", "video_codec": "mpeg4"}), encoding="utf-8")

    def fake_transcode(source_path, target_path):
        assert source_path == source
        target_path.write_bytes(b"h264")
        return True

    monkeypatch.setattr("app.web.routes.event_actions_routes.event_snapshot_store._transcode_browser_mp4", fake_transcode)

    browser_file = _browser_compatible_clip_file(clip_dir, source, metadata_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert browser_file == clip_dir / "clip_browser.mp4"
    assert browser_file.read_bytes() == b"h264"
    assert metadata["browser_video_file"] == "clip_browser.mp4"
    assert metadata["browser_video_codec"] == "h264"
