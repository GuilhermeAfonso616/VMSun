from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import User
from app.web.infrastructure import get_web_user
from app.web.routes import camera_overview_routes, dashboard_routes


@pytest.fixture
def dashboard_http(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    role = {"value": "admin"}
    application = FastAPI()
    application.include_router(dashboard_routes.router)
    application.include_router(camera_overview_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=51,
        username="dashboard-user",
        role=role["value"],
        is_active=True,
    )
    monkeypatch.setattr(dashboard_routes, "get_scoped_db", session_factory)
    monkeypatch.setattr(camera_overview_routes, "get_scoped_db", session_factory)
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(client=client, role=role)
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def _capture_template(monkeypatch, module):
    captured = []

    def render(*, request, name, context, status_code=200):
        captured.append({"name": name, "context": context})
        return PlainTextResponse(name, status_code=status_code)

    monkeypatch.setattr(module.templates, "TemplateResponse", render)
    return captured


def test_dashboard_shell_preserves_counts_context_and_viewer_access(
    dashboard_http,
    monkeypatch,
):
    captured = _capture_template(monkeypatch, dashboard_routes)
    monkeypatch.setattr(
        dashboard_routes,
        "get_dashboard_camera_counts",
        lambda _db: {"total_cameras": 8, "running_cameras": 5},
    )
    dashboard_http.role["value"] = "viewer"

    response = dashboard_http.client.get("/")

    assert response.status_code == 200
    assert captured[0]["name"] == "dashboard_resources.html"
    assert captured[0]["context"]["total_cameras"] == 8
    assert captured[0]["context"]["running_cameras"] == 5
    assert captured[0]["context"]["recent_events"] == []


def test_health_page_remains_admin_supervisor_only(dashboard_http, monkeypatch):
    _capture_template(monkeypatch, dashboard_routes)
    monkeypatch.setattr(
        dashboard_routes,
        "load_revalidator_policy",
        lambda: {"mode": "block"},
    )
    dashboard_http.role["value"] = "operator"

    denied = dashboard_http.client.get("/health", follow_redirects=False)

    assert denied.status_code == 303
    assert denied.headers["location"] == "/"


def test_camera_metrics_returns_payload_or_404(dashboard_http, monkeypatch):
    monkeypatch.setattr(
        camera_overview_routes.metrics_store,
        "get_metrics",
        lambda camera_id: {"camera_id": camera_id, "fps": 12.5}
        if camera_id == 7
        else None,
    )

    response = dashboard_http.client.get("/cameras/7/metrics")
    missing = dashboard_http.client.get("/cameras/8/metrics")

    assert response.status_code == 200
    assert response.json() == {"camera_id": 7, "fps": 12.5}
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Sem métricas para esta câmera"


def test_camera_metrics_view_renders_extracted_context(dashboard_http, monkeypatch):
    captured = _capture_template(monkeypatch, camera_overview_routes)
    observed = []
    monkeypatch.setattr(
        camera_overview_routes,
        "build_camera_metrics_context",
        lambda _db, camera_id: observed.append(camera_id)
        or {
            "camera": None,
            "metrics": {"camera_id": camera_id},
            "light_profile": {"enabled": False},
            "rtsp_probe": None,
        },
    )

    response = dashboard_http.client.get("/cameras/12/metrics/view")

    assert response.status_code == 200
    assert observed == [12]
    assert captured[0]["name"] == "camera_metrics.html"
    assert captured[0]["context"]["metrics"] == {"camera_id": 12}


def test_camera_overview_passes_query_messages_and_enforces_role(
    dashboard_http,
    monkeypatch,
):
    captured = _capture_template(monkeypatch, camera_overview_routes)
    observed = {}

    def build_context(_db, **kwargs):
        observed.update(kwargs)
        return {
            "cameras": [],
            "site_options": [],
            "group_options": [],
            "message": kwargs["message"],
            "error": kwargs["error"],
            "bulk_delete_enabled": False,
        }

    monkeypatch.setattr(
        camera_overview_routes,
        "build_camera_overview_context",
        build_context,
    )

    response = dashboard_http.client.get("/cameras?message=saved&error=none")
    assert response.status_code == 200
    assert observed == {"message": "saved", "error": "none"}
    assert captured[0]["name"] == "cameras.html"

    dashboard_http.role["value"] = "viewer"
    denied = dashboard_http.client.get("/cameras", follow_redirects=False)
    assert denied.status_code == 303


def test_history_endpoint_preserves_validation_and_service_status(
    dashboard_http,
    monkeypatch,
):
    observed = {}

    def get_history(**kwargs):
        observed.update(kwargs)
        return {"status": "error", "buckets": []}, 503

    monkeypatch.setattr(
        dashboard_routes,
        "get_operational_history_payload",
        get_history,
    )
    response = dashboard_http.client.get(
        "/dashboard/operational-history?hours=12&bucket_minutes=10&camera_id=3"
    )
    invalid = dashboard_http.client.get(
        "/dashboard/operational-history?hours=0"
    )

    assert response.status_code == 503
    assert response.json() == {"status": "error", "buckets": []}
    assert observed == {
        "hours": 12,
        "bucket_minutes": 10,
        "camera_id": 3,
        "start": None,
        "end": None,
    }
    assert invalid.status_code == 422


def test_dashboard_events_disables_browser_cache(dashboard_http, monkeypatch):
    monkeypatch.setattr(
        dashboard_routes,
        "build_dashboard_events_payload",
        lambda _db: {"recent_events": [], "open_events": []},
    )

    response = dashboard_http.client.get("/dashboard/events")

    assert response.status_code == 200
    assert response.headers["cache-control"] == (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"
