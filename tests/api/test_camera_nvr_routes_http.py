from app.db.models import AuditLog, Camera, User
from app.services import nvr_health_service


def _admin(api_context) -> User:
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    api_context.db.add(admin)
    api_context.db.commit()
    api_context.db.refresh(admin)
    api_context.selected_user["value"] = admin
    return admin


def test_camera_create_and_list_preserve_http_contract(api_context):
    admin = _admin(api_context)

    created = api_context.client.post(
        "/api/cameras",
        json={
            "name": "Entrada",
            "ip": "10.0.0.10",
            "manufacturer": "Hikvision",
            "model": "DS-2CD",
            "onvif_port": 80,
            "username": "camera",
            "password": "secret",
            "rtsp_url": "rtsp://10.0.0.10/main",
        },
    )
    listed = api_context.client.get("/api/cameras")

    assert created.status_code == 200
    assert created.json()["name"] == "Entrada"
    assert created.json()["status"] == "idle"
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["manufacturer"] == "Hikvision"
    assert listed.json()[0]["model"] == "DS-2CD"
    assert listed.json()[0]["source_type"] == "camera"
    assert listed.json()[0]["analytics_profile_preview"]["analytic_goal"] == "intrusion"

    audit = api_context.db.query(AuditLog).filter(AuditLog.action == "camera_create").one()
    assert audit.user_id == admin.id


def test_camera_create_requires_manufacturer(api_context):
    _admin(api_context)

    response = api_context.client.post(
        "/api/cameras",
        json={
            "name": "Sem marca",
            "ip": "10.0.0.12",
            "username": "camera",
            "password": "secret",
            "rtsp_url": "rtsp://10.0.0.12/main",
        },
    )

    assert response.status_code == 422
    assert api_context.db.query(Camera).count() == 0


def test_camera_create_requires_supervisor_or_admin(api_context):
    operator = User(username="operator", password_hash="hash", role="operator", is_active=True)
    api_context.db.add(operator)
    api_context.db.commit()
    api_context.selected_user["value"] = operator

    response = api_context.client.post(
        "/api/cameras",
        json={
            "name": "Negada",
            "ip": "10.0.0.11",
            "username": "camera",
            "password": "secret",
            "rtsp_url": "rtsp://10.0.0.11/main",
        },
    )

    assert response.status_code == 403
    assert api_context.db.query(Camera).count() == 0


def test_nvr_channel_creation_is_idempotent_by_logical_source(api_context):
    payload = {
        "host": "10.0.0.20",
        "username": "nvr",
        "password": "secret",
        "base_name": "Matriz",
        "brand": "intelbras",
        "provider_type": "generic_nvr",
        "profiles": [
            {
                "channel": 1,
                "stream_kind": "secondary",
                "rtsp_url": "rtsp://10.0.0.20/cam/1/sub",
            }
        ],
    }

    created = api_context.client.post("/api/video-sources/nvr/channels", json=payload)
    repeated_payload = dict(payload)
    repeated_payload["profiles"] = [
        {
            "channel": 1,
            "stream_kind": "sub",
            "rtsp_url": "rtsp://10.0.0.20/cam/1/sub-new-address",
        }
    ]
    repeated = api_context.client.post("/api/video-sources/nvr/channels", json=repeated_payload)

    assert created.status_code == 200
    assert created.json()["count"] == 1
    assert created.json()["created"][0]["source_stream_kind"] == "sub"
    assert repeated.status_code == 200
    assert repeated.json()["count"] == 0
    assert repeated.json()["skipped_count"] == 1
    assert api_context.db.query(Camera).count() == 1


def test_nvr_routes_validate_empty_selection_and_discovery_host(api_context):
    no_profiles = api_context.client.post(
        "/api/video-sources/nvr/channels",
        json={"host": "10.0.0.20", "profiles": []},
    )
    missing_host = api_context.client.post(
        "/api/video-sources/nvr/discover",
        json={"host": "", "probe": False},
    )

    assert no_profiles.status_code == 400
    assert no_profiles.json() == {"detail": "Nenhum canal/profile selecionado"}
    assert missing_host.status_code == 400
    assert "obrigatorio" in missing_host.json()["detail"]


def test_nvr_health_uses_shared_runtime_snapshot(api_context, monkeypatch):
    camera = Camera(
        name="Canal 2",
        ip="10.0.0.20",
        username="nvr",
        password="secret",
        rtsp_url="rtsp://10.0.0.20/cam/2",
        source_type="nvr_channel",
        source_channel=2,
        source_stream_kind="main",
        status="idle",
        is_deleted=False,
    )
    api_context.db.add(camera)
    api_context.db.commit()
    api_context.db.refresh(camera)
    monkeypatch.setattr(
        nvr_health_service,
        "get_runtime_health_snapshot",
        lambda: {
            "cameras": [
                {
                    "camera_id": camera.id,
                    "health_status": "running_motion_test",
                    "is_running": True,
                    "last_frame_at": "2026-07-17T10:00:00Z",
                    "gateway_state": "online",
                }
            ]
        },
    )
    monkeypatch.setattr(nvr_health_service, "remote_runtime_enabled", lambda: True)

    response = api_context.client.get("/api/video-sources/nvr/health")

    assert response.status_code == 200
    assert response.json()["count"] == 1
    channel = response.json()["channels"][0]
    assert channel["health_status"] == "running"
    assert channel["worker_registered"] is True
    assert channel["gateway_state"] == "online"
