from pathlib import Path

from app.services.track_store import TrackStore


def test_track_store_preserves_frame_identity_and_empty_result(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.track_store.settings.runtime_state_dir",
        str(tmp_path),
    )
    monkeypatch.setattr(
        "app.services.track_store.settings.track_store_memory_cache_ttl_seconds",
        0.0,
    )
    store = TrackStore()

    store.set_tracks(
        7,
        [],
        frame_width=1280,
        frame_height=720,
        frame_context={
            "frame_id": 101,
            "generation_id": 12,
            "gateway_received_at_ns": 123,
            "capture_clock": "gateway_receive_wall_clock",
        },
        latency_diagnostics={"latest": {"inference_ms": 50.0}},
    )

    payload = store.get_tracks(7, max_age_seconds=2.0)

    assert payload["frame_id"] == 101
    assert payload["generation_id"] == 12
    assert payload["tracks"] == []
    assert payload["stale"] is False
    assert payload["track_store_read_ms"] >= 0
    assert Path(tmp_path, "tracks", "camera_7.json").exists()


def test_reader_cache_observes_new_file_version_from_another_process(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "app.services.track_store.settings.runtime_state_dir",
        str(tmp_path),
    )
    monkeypatch.setattr(
        "app.services.track_store.settings.track_store_memory_cache_ttl_seconds",
        60.0,
    )
    writer = TrackStore()
    reader = TrackStore()

    writer.set_tracks(7, [{"bbox": [1, 2, 3, 4]}], frame_context={"frame_id": 1})
    assert reader.get_tracks(7)["frame_id"] == 1

    writer.set_tracks(7, [], frame_context={"frame_id": 2})
    refreshed = reader.get_tracks(7)

    assert refreshed["frame_id"] == 2
    assert refreshed["tracks"] == []
    assert refreshed["track_store_cache_hit"] is False


def test_latency_update_keeps_tracks_timestamp_and_rejects_wrong_frame(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "app.services.track_store.settings.runtime_state_dir",
        str(tmp_path),
    )
    store = TrackStore()
    original = store.set_tracks(
        7,
        [{"bbox": [1, 2, 3, 4]}],
        frame_context={
            "frame_id": 101,
            "generation_id": 12,
            "tracks_published_at_ns": 456,
        },
    )

    assert store.update_latency_diagnostics(
        7,
        {"latest": {"event_pipeline_ms": 25.0}},
        expected_frame_id=999,
        expected_generation_id=12,
    ) is False
    assert store.update_latency_diagnostics(
        7,
        {"latest": {"event_pipeline_ms": 25.0}},
        expected_frame_id=101,
        expected_generation_id=12,
    ) is True

    updated = store.get_tracks(7)
    assert updated["tracks"] == original["tracks"]
    assert updated["updated_at"] == original["updated_at"]
    assert updated["tracks_published_at_ns"] == 456
    assert updated["latency_diagnostics"]["latest"]["event_pipeline_ms"] == 25.0
