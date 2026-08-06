from fastapi.testclient import TestClient

from app import bootstrap
from app.application import create_app


def test_create_app_exposes_health_without_running_lifecycle(monkeypatch):
    monkeypatch.setattr(bootstrap.settings, "app_role", "web")
    application = create_app(enable_lifecycle=False)

    route_paths = {route.path for route in application.routes}

    assert "/api/health" in route_paths
    assert "/monitor" in route_paths
    assert "/internal/health/ready" not in route_paths


def test_application_lifespan_starts_and_stops_process_resources(monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap.settings, "app_role", "web")
    monkeypatch.setattr(bootstrap, "startup_application", lambda: calls.append("startup"))
    monkeypatch.setattr(bootstrap, "shutdown_application", lambda: calls.append("shutdown"))

    with TestClient(create_app()) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert calls == ["startup"]

    assert calls == ["startup", "shutdown"]


def test_runtime_role_does_not_mount_web_routes(monkeypatch):
    monkeypatch.setattr(bootstrap.settings, "app_role", "runtime")
    application = create_app(enable_lifecycle=False)

    route_paths = {route.path for route in application.routes}

    assert "/api/health" in route_paths
    assert "/internal/health/ready" in route_paths
    assert "/monitor" not in route_paths
