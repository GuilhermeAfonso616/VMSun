from app.api.routers import drive_routes
from app.db.models import Camera
from app.services import operator_service


def test_system_contracts_are_available(api_context):
    health = api_context.client.get("/api/health")
    version = api_context.client.get("/api/version")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert version.status_code == 200
    assert version.json()["server_version"]


def test_operator_bootstrap_http_uses_isolated_service(api_context, monkeypatch):
    camera = Camera(
        name="Entrada",
        ip="10.0.0.10",
        username="camera",
        password="secret",
        rtsp_url="rtsp://10.0.0.10/main",
        status="idle",
        is_deleted=False,
    )
    api_context.db.add(camera)
    api_context.db.commit()
    api_context.db.refresh(camera)
    monkeypatch.setattr(operator_service, "get_runtime_health_snapshot", lambda: {"cameras": []})
    monkeypatch.setattr(operator_service, "webrtc_gateway_is_enabled", lambda: False)
    monkeypatch.setattr(operator_service, "webrtc_rtsp_public_base_url", lambda: "")
    monkeypatch.setattr(operator_service, "webrtc_public_base_url", lambda: "")
    monkeypatch.setattr(operator_service, "build_webrtc_player_url", lambda _path: "")

    response = api_context.client.get("/api/operator/bootstrap?register_paths=false")

    assert response.status_code == 200
    assert response.json()["camera_count"] == 1
    assert response.json()["cameras"][0]["registration_reason"] == "webrtc_gateway_disabled"


def test_operator_performance_http_validates_and_persists(api_context, tmp_path, monkeypatch):
    monkeypatch.setattr(operator_service.settings, "runtime_state_dir", str(tmp_path))
    monkeypatch.setattr(operator_service.onedrive_client, "enabled", lambda: False)

    invalid = api_context.client.post("/api/operator/performance-log", json=["invalid"])
    valid = api_context.client.post(
        "/api/operator/performance-log",
        json={"machine_name": "OPS-01", "captured_at_utc": "2026-07-17T15:30:00Z"},
    )

    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "Payload de performance deve ser um objeto JSON"}
    assert valid.status_code == 200
    assert valid.json()["filename"] == "operator_perf_OPS-01_20260717T153000Z.json"
    assert (tmp_path / "operator_performance" / valid.json()["filename"]).exists()


def test_drive_routes_delegate_and_translate_errors(api_context, monkeypatch):
    saved_tokens = []
    monkeypatch.setattr(
        drive_routes.onedrive_client,
        "status",
        lambda **_kwargs: {"enabled": True, "connected": True},
    )
    monkeypatch.setattr(
        drive_routes.onedrive_client,
        "save_token_text",
        lambda token: saved_tokens.append(token),
    )
    monkeypatch.setattr(
        drive_routes.onedrive_client,
        "set_archive_enabled",
        lambda enabled: {"enabled": enabled},
    )
    monkeypatch.setattr(
        drive_routes,
        "upload_reviewed_events_pending_onedrive",
        lambda _db, limit: {"uploaded": limit},
    )

    status = api_context.client.get("/api/operator/drive-token/status")
    token = api_context.client.post("/api/operator/drive-token", json={"token": "refresh-token"})
    toggle = api_context.client.post("/api/operator/drive-upload", json={"enabled": False})
    upload = api_context.client.post("/api/operator/drive-reviewed-events/upload?limit=3")

    assert status.json()["connected"] is True
    assert token.status_code == 200
    assert saved_tokens == ["refresh-token"]
    assert toggle.json()["onedrive"] == {"enabled": False}
    assert upload.json()["onedrive"] == {"uploaded": 3}

    monkeypatch.setattr(
        drive_routes.onedrive_client,
        "save_token_text",
        lambda _token: (_ for _ in ()).throw(ValueError("token invalido")),
    )
    failed = api_context.client.post("/api/operator/drive-token", json={"token": "bad"})
    assert failed.status_code == 400
    assert failed.json() == {"detail": "token invalido"}
