from app.services.nvr_discovery_cache import NvrDiscoveryCache


def test_nvr_discovery_cache_expires_credentials_and_profiles():
    now = [100.0]
    cache = NvrDiscoveryCache(ttl_seconds=5, clock=lambda: now[0])
    token = cache.store(
        host="nvr.local",
        username="admin",
        password="secret",
        profiles=[{"index": 0, "rtsp_url": "rtsp://nvr.local/1"}],
    )

    assert cache.get(token)["password"] == "secret"

    now[0] = 106.0
    assert cache.get(token) is None


def test_nvr_discovery_cache_returns_defensive_profile_copies():
    cache = NvrDiscoveryCache()
    token = cache.store(
        host="nvr.local",
        username="admin",
        password="secret",
        profiles=[{"index": 0, "name": "Canal original"}],
    )

    first = cache.get(token)
    first["profiles"][0]["name"] = "Alterado fora do cache"

    assert cache.get(token)["profiles"][0]["name"] == "Canal original"
