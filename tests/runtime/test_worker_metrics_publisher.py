from __future__ import annotations

from app.runtime import worker_metrics_publisher


class _Frame:
    shape = (480, 640, 3)


class _MetricsStoreStub:
    def __init__(self):
        self.payloads = []

    def set_metrics(self, camera_id: int, data: dict):
        payload = {**data, "updated_at": "2026-06-18T00:00:00"}
        self.payloads.append((camera_id, payload))
        return payload


def _publish_required_payload(
    publisher: worker_metrics_publisher.WorkerMetricsPublisher,
    *,
    roi_polygon=None,
):
    return publisher.publish(
        123,
        read_ms=1.0,
        infer_ms=2.0,
        plot_ms=3.0,
        jpeg_ms=4.0,
        loop_ms=5.0,
        current_fps=6.0,
        frame=_Frame(),
        infer_input_width=640,
        infer_input_height=480,
        tracks_count=0,
        tracks=[],
        reconnect_count=0,
        dropped_frames_count=0,
        last_successful_inference_at=None,
        roi_polygon=roi_polygon,
    )


def test_worker_metrics_publish_accepts_camera_without_roi(monkeypatch):
    store = _MetricsStoreStub()
    monkeypatch.setattr(worker_metrics_publisher, "metrics_store", store)

    publisher = worker_metrics_publisher.WorkerMetricsPublisher()

    payload = _publish_required_payload(publisher, roi_polygon=None)

    assert payload["roi_enabled"] is False
    assert store.payloads[0][0] == 123


def test_worker_metrics_publish_marks_roi_enabled_with_three_points(monkeypatch):
    store = _MetricsStoreStub()
    monkeypatch.setattr(worker_metrics_publisher, "metrics_store", store)

    publisher = worker_metrics_publisher.WorkerMetricsPublisher()

    payload = _publish_required_payload(
        publisher,
        roi_polygon=[(0, 0), (10, 0), (10, 10)],
    )

    assert payload["roi_enabled"] is True
