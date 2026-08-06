from io import BytesIO
from urllib.error import URLError

import pytest
from fastapi import HTTPException

from app.web.routes import gateway_routes


class _GatewayResponse(BytesIO):
    def __init__(self, content: bytes, content_type: str = "image/jpeg"):
        super().__init__(content)
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_snapshot_route_preserves_gateway_content_type(monkeypatch):
    monkeypatch.setattr(
        gateway_routes,
        "_gateway_proxy_request",
        lambda *_args, **_kwargs: _GatewayResponse(b"jpeg-data"),
    )

    response = gateway_routes.monitor_gateway_camera_snapshot(42)

    assert response.body == b"jpeg-data"
    assert response.media_type == "image/jpeg"
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"


def test_gateway_proxy_rejects_requests_when_gateway_is_disabled(monkeypatch):
    monkeypatch.setattr(gateway_routes, "gateway_is_enabled", lambda: False)

    with pytest.raises(HTTPException) as error:
        gateway_routes._gateway_proxy_request(42, "/snapshot.jpg")

    assert error.value.status_code == 503
    assert error.value.detail == "Gateway de cameras desabilitado"


def test_gateway_proxy_translates_network_failure(monkeypatch):
    monkeypatch.setattr(gateway_routes, "gateway_is_enabled", lambda: True)
    monkeypatch.setattr(gateway_routes, "gateway_camera_url", lambda *_args: "http://gateway/snapshot")
    monkeypatch.setattr(gateway_routes, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")))

    with pytest.raises(HTTPException) as error:
        gateway_routes._gateway_proxy_request(42, "/snapshot.jpg")

    assert error.value.status_code == 503
    assert error.value.detail == "Gateway indisponivel para o monitor"
