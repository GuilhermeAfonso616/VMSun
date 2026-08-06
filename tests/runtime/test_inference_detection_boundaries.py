from app.core.config import settings
from app.runtime import inference, inference_detection


def test_legacy_inference_module_reexports_detection_contracts():
    assert inference.DetectionService is inference_detection.DetectionService
    assert (
        inference.InferenceBackpressureError
        is inference_detection.InferenceBackpressureError
    )
    assert (
        inference.is_detector_engine_failure
        is inference_detection.is_detector_engine_failure
    )


def test_detection_module_resolves_local_pool_only_when_inference_is_requested(
    monkeypatch,
):
    captured = []

    class FakePool:
        def infer(self, **kwargs):
            captured.append(kwargs)
            return [], 1.25

        def stats(self):
            return {"enabled": True, "mode": "pool", "backend": "local"}

    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr(inference_detection, "_get_inference_pool", lambda: FakePool())
    service = inference_detection.DetectionService(camera_id=19, use_pool=True)
    service.pool_backend = "local"

    result = service.infer(object(), offset_x=3, offset_y=4, scale_x=1.5, scale_y=2.0)

    assert result == ([], 1.25)
    assert captured == [
        {
            "camera_id": 19,
            "infer_frame": captured[0]["infer_frame"],
            "offset_x": 3,
            "offset_y": 4,
            "scale_x": 1.5,
            "scale_y": 2.0,
        }
    ]
    assert service.runtime_stats()["backend"] == "local"


def test_central_release_uses_camera_release_endpoint(monkeypatch):
    requests = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr(settings, "inference_pool_enabled", True)
    monkeypatch.setattr(settings, "inference_pool_backend", "central")
    monkeypatch.setattr(
        settings,
        "inference_pool_central_url",
        "http://runtime:8000/internal/inference/track",
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    service = inference_detection.DetectionService(camera_id=19, use_pool=True)

    result = service.release_camera()

    assert result["ok"] is True
    assert requests[0][0].get_method() == "DELETE"
    assert requests[0][0].full_url == "http://runtime:8000/internal/inference/cameras/19"
