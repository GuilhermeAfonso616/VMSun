from app.services.camera_discovery_cache import CameraDiscoveryCache


def test_camera_discovery_cache_expires_sensitive_payload():
    now = [50.0]
    cache = CameraDiscoveryCache(ttl_seconds=5, clock=lambda: now[0])
    token = cache.store({"password": "secret", "profiles": [{"rtsp_url": "rtsp://private"}]})

    assert cache.get(token)["password"] == "secret"

    now[0] = 56.0
    assert cache.get(token) is None


def test_camera_discovery_cache_returns_deep_copy():
    cache = CameraDiscoveryCache()
    token = cache.store({"profiles": [{"suggested_name": "Original"}]})

    first = cache.get(token)
    first["profiles"][0]["suggested_name"] = "Alterado"

    assert cache.get(token)["profiles"][0]["suggested_name"] == "Original"
