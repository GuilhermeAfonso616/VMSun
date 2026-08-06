import unittest

from app.core.config import settings
from app.services.stream_topology import resolve_stream_url


class StreamTopologyTests(unittest.TestCase):
    def setUp(self):
        self._gateway_enabled = settings.camera_gateway_enabled
        self._gateway_public_base_url = settings.camera_gateway_public_base_url
        self._gateway_base_url = settings.camera_gateway_base_url

    def tearDown(self):
        settings.camera_gateway_enabled = self._gateway_enabled
        settings.camera_gateway_public_base_url = self._gateway_public_base_url
        settings.camera_gateway_base_url = self._gateway_base_url

    def test_resolve_stream_url_uses_web_proxy_without_public_gateway_url(self):
        settings.camera_gateway_enabled = True
        settings.camera_gateway_public_base_url = ""
        settings.camera_gateway_base_url = "http://camera-gateway:8090"

        self.assertEqual(
            resolve_stream_url("/cameras/42/stream/raw"),
            "/monitor/gateway/cameras/42/stream/live",
        )

    def test_resolve_stream_url_uses_public_gateway_url_when_configured(self):
        settings.camera_gateway_enabled = True
        settings.camera_gateway_public_base_url = "http://10.0.0.10:8090"
        settings.camera_gateway_base_url = "http://camera-gateway:8090"

        self.assertEqual(resolve_stream_url("/cameras/42/stream/raw"), "http://10.0.0.10:8090/cameras/42/stream/live")

    def test_resolve_stream_url_returns_relative_when_gateway_disabled(self):
        settings.camera_gateway_enabled = False
        settings.camera_gateway_public_base_url = "http://10.0.0.10:8090"
        settings.camera_gateway_base_url = "http://camera-gateway:8090"

        self.assertEqual(resolve_stream_url("/cameras/42/snapshot"), "/cameras/42/snapshot")


if __name__ == "__main__":
    unittest.main()
