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
from app.web import monitor_presenter
from app.web.infrastructure import get_web_user
from app.web.routes import diagnostics_routes


@pytest.fixture
def diagnostics_http(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    role = {"value": "admin"}

    application = FastAPI()
    application.include_router(diagnostics_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=31,
        username="diagnostics-user",
        role=role["value"],
        is_active=True,
    )
    monkeypatch.setattr(diagnostics_routes, "get_scoped_db", session_factory)
    monkeypatch.setattr(diagnostics_routes, "SessionLocal", session_factory)
    monitor_presenter._MONITOR_RESPONSE_CACHE.clear()
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(client=client, role=role)
    finally:
        application.dependency_overrides.clear()
        monitor_presenter._MONITOR_RESPONSE_CACHE.clear()
        engine.dispose()


def _shell_payload():
    return {
        "summary": {"camera_total": 0},
        "cameras": [],
        "runtime_tuning": {},
        "detector_engine": {},
    }


def test_diagnostics_page_preserves_roles_and_renders_shell(diagnostics_http, monkeypatch):
    captured = {}

    def render_template(*, request, name, context):
        captured.update({"name": name, "context": context})
        return PlainTextResponse("diagnostics")

    monkeypatch.setattr(diagnostics_routes, "build_diagnostics_shell_payload", _shell_payload)
    monkeypatch.setattr(diagnostics_routes.templates, "TemplateResponse", render_template)
    monkeypatch.setattr(
        diagnostics_routes,
        "count_reviewed_events_pending_onedrive",
        lambda _db: 4,
    )
    monkeypatch.setattr(
        diagnostics_routes,
        "diagnostics_docker_control_is_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        diagnostics_routes.onedrive_client,
        "status",
        lambda **_kwargs: {"connected": False},
    )

    response = diagnostics_http.client.get("/diagnostics")
    assert response.status_code == 200
    assert captured["name"] == "diagnostics.html"
    assert captured["context"]["onedrive_pending_reviewed_events"] == 4

    diagnostics_http.role["value"] = "operator"
    denied = diagnostics_http.client.get("/diagnostics", follow_redirects=False)
    assert denied.status_code == 303
    assert denied.headers["location"] == "/"


def test_diagnostics_data_preserves_alias_cache_and_no_cache_headers(
    diagnostics_http,
    monkeypatch,
):
    calls = []

    def build_payload(_db, **kwargs):
        calls.append(kwargs)
        return {"summary": {"camera_total": 0}, "cameras": []}

    monkeypatch.setattr(diagnostics_routes, "build_diagnostics_payload", build_payload)
    url = "/monitor/diagnostics/data?include_logs=true&include_gateway=true"
    first = diagnostics_http.client.get(url)
    second = diagnostics_http.client.get(url)

    assert first.status_code == 200
    assert first.json()["summary"]["camera_total"] == 0
    assert first.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert first.headers["x-server-cache"] in {"MISS", "BYPASS"}
    if first.headers["x-server-cache"] == "MISS":
        assert second.headers["x-server-cache"] == "HIT"
        assert len(calls) == 1
    assert calls[0] == {"include_logs": True, "include_gateway": True}


def test_gateway_mode_returns_selected_mode_and_restart_count(diagnostics_http, monkeypatch):
    monkeypatch.setattr(
        diagnostics_routes,
        "set_gateway_capture_mode",
        lambda mode: "gateway_only" if mode == "gateway" else "hybrid",
    )
    monkeypatch.setattr(
        diagnostics_routes,
        "restart_active_camera_workers",
        lambda: 3,
    )
    monkeypatch.setattr(
        diagnostics_routes.settings,
        "camera_gateway_worker_rtsp_fallback_enabled",
        False,
    )

    response = diagnostics_http.client.post(
        "/diagnostics/gateway-mode",
        data={"mode": "gateway"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "mode": "gateway_only",
        "label": "So Gateway",
        "rtsp_fallback_enabled": False,
        "restarted_workers": 3,
    }


def test_runtime_tuning_translates_form_checkboxes_to_typed_payload(
    diagnostics_http,
    monkeypatch,
):
    observed = {}

    def update(payload):
        observed.update(payload)
        return {"source": "local"}, None

    monkeypatch.setattr(diagnostics_routes, "update_runtime_tuning_configuration", update)

    response = diagnostics_http.client.post(
        "/diagnostics/runtime-tuning",
        data={
            "gpu_guard_enabled": "on",
            "max_gpu_memory_mb": "4096",
            "max_active_workers": "6",
            "inference_pool_enabled": "on",
            "inference_pool_count": "2",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/diagnostics?message=runtime_tuning_saved"
    assert observed["gpu_guard_enabled"] is True
    assert observed["detector_fp16_enabled"] is False
    assert observed["inference_pool_enabled"] is True
    assert observed["max_gpu_memory_mb"] == 4096
    assert observed["max_active_workers"] == 6
    assert observed["inference_pool_count"] == 2


def test_docker_rejection_never_starts_background_thread(diagnostics_http, monkeypatch):
    started = []
    monkeypatch.setattr(
        diagnostics_routes,
        "validate_docker_stack_request",
        lambda *_args: ("restart", "docker_bad_password"),
    )
    monkeypatch.setattr(
        diagnostics_routes,
        "Thread",
        lambda **_kwargs: started.append(True),
    )

    response = diagnostics_http.client.post(
        "/diagnostics/docker-stack",
        data={
            "action": "restart",
            "docker_control_password": "wrong",
            "confirm_text": "REINICIAR DOCKER",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/diagnostics?error=docker_bad_password"
    assert started == []


def test_onedrive_token_file_decodes_utf8_bom_before_saving(diagnostics_http, monkeypatch):
    saved = []
    monkeypatch.setattr(
        diagnostics_routes.onedrive_client,
        "save_token_text",
        lambda token: saved.append(token),
    )
    monkeypatch.setattr(
        diagnostics_routes.onedrive_client,
        "status",
        lambda **_kwargs: {"connected": True},
    )

    response = diagnostics_http.client.post(
        "/diagnostics/onedrive-token-file",
        files={"onedrive_token_file": ("token.txt", b"\xef\xbb\xbf token-value \n")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/diagnostics?message=onedrive_token_saved"
    assert saved == ["token-value"]


def test_backup_export_remains_admin_only_and_returns_encrypted_bytes(
    diagnostics_http,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        diagnostics_routes,
        "resolve_backup_paths",
        lambda: (tmp_path / "analytics.db", tmp_path / ".env"),
    )
    monkeypatch.setattr(
        diagnostics_routes.BackupService,
        "create_backup",
        lambda *_args: b"encrypted-backup",
    )

    response = diagnostics_http.client.post(
        "/diagnostics/backup/export",
        data={"password": "safe"},
    )
    assert response.status_code == 200
    assert response.content == b"encrypted-backup"
    assert response.headers["content-disposition"].startswith(
        "attachment; filename=vms_backup_"
    )

    diagnostics_http.role["value"] = "supervisor"
    denied = diagnostics_http.client.post(
        "/diagnostics/backup/export",
        data={"password": "safe"},
        follow_redirects=False,
    )
    assert denied.status_code == 303
    assert denied.headers["location"] == "/"
