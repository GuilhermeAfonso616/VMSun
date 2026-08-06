import docker_control


class _ProbeSocket:
    def __init__(self, address):
        self.address = address

    def connect(self, _target):
        return None

    def getsockname(self):
        return (self.address, 49152)

    def close(self):
        return None


def test_detect_primary_lan_ipv4_uses_routed_host_address(monkeypatch):
    monkeypatch.setattr(
        docker_control.socket,
        "socket",
        lambda *_args, **_kwargs: _ProbeSocket("192.168.2.62"),
    )

    assert docker_control.detect_primary_lan_ipv4() == "192.168.2.62"


def test_compose_environment_preserves_manual_override(monkeypatch):
    monkeypatch.setenv("MTX_WEBRTCADDITIONALHOSTS", "camera.example.test,192.168.1.33")
    monkeypatch.setattr(
        docker_control,
        "detect_primary_lan_ipv4",
        lambda: (_ for _ in ()).throw(AssertionError("nao deve detectar")),
    )

    environment = docker_control.compose_environment()

    assert environment["MTX_WEBRTCADDITIONALHOSTS"] == "camera.example.test,192.168.1.33"


def test_compose_environment_uses_detected_address_when_unset(monkeypatch):
    monkeypatch.delenv("MTX_WEBRTCADDITIONALHOSTS", raising=False)
    monkeypatch.setattr(docker_control, "detect_primary_lan_ipv4", lambda: "10.20.30.40")

    environment = docker_control.compose_environment()

    assert environment["MTX_WEBRTCADDITIONALHOSTS"] == "10.20.30.40"
