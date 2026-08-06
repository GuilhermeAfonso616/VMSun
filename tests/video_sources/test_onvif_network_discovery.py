import ipaddress

import pytest

from app.services import onvif_network_discovery as discovery


WS_RESPONSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <e:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <d:Scopes>
          onvif://www.onvif.org/name/Entrada%20Principal
          onvif://www.onvif.org/hardware/DS-2CD2043
        </d:Scopes>
        <d:XAddrs>http://192.168.10.25:8000/onvif/device_service</d:XAddrs>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </e:Body>
</e:Envelope>"""


def test_parse_ws_discovery_response_extracts_device_metadata():
    devices = discovery.parse_ws_discovery_response(
        WS_RESPONSE,
        network=ipaddress.ip_network("192.168.10.0/24"),
    )

    assert devices == [
        discovery.OnvifNetworkDevice(
            ip="192.168.10.25",
            port=8000,
            name="Entrada Principal",
            model="DS-2CD2043",
            xaddr="http://192.168.10.25:8000/onvif/device_service",
            source="ws_discovery",
        )
    ]


def test_parse_ws_discovery_response_filters_other_networks():
    devices = discovery.parse_ws_discovery_response(
        WS_RESPONSE,
        network=ipaddress.ip_network("192.168.20.0/24"),
    )

    assert devices == []


def test_parse_ws_discovery_response_ignores_invalid_port():
    payload = WS_RESPONSE.replace(b":8000/", b":99999/")

    assert discovery.parse_ws_discovery_response(payload) == []


@pytest.mark.parametrize(
    "value",
    ["", "not-a-network", "8.8.8.0/24", "192.168.0.0/23", "2001:db8::/120"],
)
def test_parse_private_ipv4_network_rejects_unsafe_ranges(value):
    with pytest.raises(ValueError):
        discovery.parse_private_ipv4_network(value)


def test_parse_private_ipv4_network_normalizes_host_address():
    network = discovery.parse_private_ipv4_network("192.168.7.18/24")

    assert str(network) == "192.168.7.0/24"


def test_network_for_camera_ip_suggests_private_slash_24():
    assert discovery.network_for_camera_ip("10.20.30.44") == "10.20.30.0/24"
    assert discovery.network_for_camera_ip("1.1.1.1") is None


def test_probe_onvif_endpoint_accepts_authentication_challenge(monkeypatch):
    class FakeResponse:
        status = 401

        @staticmethod
        def read(_limit):
            return b""

        @staticmethod
        def getheaders():
            return [("WWW-Authenticate", 'Digest realm="ONVIF"')]

    class FakeConnection:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs):
            pass

        @staticmethod
        def getresponse():
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr(discovery.http.client, "HTTPConnection", FakeConnection)

    device = discovery.probe_onvif_endpoint("192.168.1.25", 80)

    assert device is not None
    assert device.ip == "192.168.1.25"
    assert device.port == 80


def test_probe_onvif_endpoint_ignores_regular_not_found(monkeypatch):
    class FakeResponse:
        status = 404

        @staticmethod
        def read(_limit):
            return b"not found"

        @staticmethod
        def getheaders():
            return [("Server", "nginx")]

    class FakeConnection:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs):
            pass

        @staticmethod
        def getresponse():
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr(discovery.http.client, "HTTPConnection", FakeConnection)

    assert discovery.probe_onvif_endpoint("192.168.1.1", 80) is None


def test_discover_onvif_network_merges_ws_and_scan_results(monkeypatch):
    ws_device = discovery.OnvifNetworkDevice(
        ip="192.168.1.20",
        port=80,
        name="Portaria",
        source="ws_discovery",
    )
    scanned_device = discovery.OnvifNetworkDevice(
        ip="192.168.1.20",
        port=8000,
        model="Camera XPTO",
        source="port_scan",
    )
    monkeypatch.setattr(discovery, "discover_via_ws_discovery", lambda *_args, **_kwargs: [ws_device])
    monkeypatch.setattr(discovery, "discover_via_port_scan", lambda *_args, **_kwargs: [scanned_device])

    result = discovery.discover_onvif_network("192.168.1.0/24")

    assert result.ws_discovery_count == 1
    assert result.port_scan_count == 1
    assert len(result.devices) == 1
    assert result.devices[0].name == "Portaria"
    assert result.devices[0].model == "Camera XPTO"
    assert result.devices[0].source == "ws_discovery"
