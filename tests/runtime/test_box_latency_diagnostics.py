from app.runtime.box_latency_diagnostics import (
    BoxLatencyDiagnostics,
    camera_is_selected,
)


def test_camera_selector_requires_explicit_canary():
    assert camera_is_selected(7, enabled=False, camera_ids="*") is False
    assert camera_is_selected(7, enabled=True, camera_ids="7,9") is True
    assert camera_is_selected(8, enabled=True, camera_ids="7,9") is False
    assert camera_is_selected(8, enabled=True, camera_ids="*") is True


def test_latency_snapshot_calculates_mean_p95_and_max():
    diagnostics = BoxLatencyDiagnostics(max_samples=10)
    for value in range(1, 11):
        diagnostics.record(
            {
                "camera_id": 7,
                "frame_id": value,
                "inference_ms": value,
                "event_pipeline_ms": value * 2,
            }
        )

    snapshot = diagnostics.snapshot()

    assert snapshot["latest"]["frame_id"] == 10
    assert snapshot["summary"]["inference_ms"] == {
        "count": 10,
        "mean": 5.5,
        "p50": 5.0,
        "p90": 9.0,
        "p95": 10.0,
        "p99": 10.0,
        "max": 10.0,
    }


def test_latency_counters_are_bounded_to_known_names():
    diagnostics = BoxLatencyDiagnostics(max_samples=10)

    diagnostics.increment("visual_fast_path_published_total")
    diagnostics.increment("visual_updates_coalesced_total", 2)

    counters = diagnostics.snapshot()["counters"]
    assert counters["visual_fast_path_published_total"] == 1
    assert counters["visual_updates_coalesced_total"] == 2
