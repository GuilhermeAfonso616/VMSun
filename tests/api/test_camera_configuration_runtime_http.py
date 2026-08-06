from app.db.models import AuditLog, Camera, User
from app.services import camera_runtime_service as runtime_service
from app.services.runtime_client import RuntimeClientError


def _seed_admin_and_camera(api_context):
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    camera = Camera(
        name="Entrada",
        ip="10.0.0.10",
        username="camera",
        password="secret",
        rtsp_url="rtsp://10.0.0.10/main",
        status="idle",
        is_deleted=False,
    )
    api_context.db.add_all([admin, camera])
    api_context.db.commit()
    api_context.db.refresh(admin)
    api_context.db.refresh(camera)
    api_context.selected_user["value"] = admin
    return admin, camera


def test_configuration_routes_persist_profile_ops_and_audit(api_context):
    admin, camera = _seed_admin_and_camera(api_context)

    profile_response = api_context.client.put(
        f"/api/cameras/{camera.id}/analytics-profile",
        json={
            "camera_family": "bullet",
            "scene_category": "perimetral",
            "analytic_goal": "intrusion",
        },
    )
    ops_response = api_context.client.put(
        f"/api/cameras/{camera.id}/ops-config",
        json={
            "site_name": "Matriz",
            "group_name": "Perimetro",
            "camera_priority": "high",
            "auto_start_enabled": True,
            "alarm_sound_enabled": False,
            "alarm_popup_enabled": True,
        },
    )
    fetched = api_context.client.get(f"/api/cameras/{camera.id}/analytics-profile")

    assert profile_response.status_code == 200
    assert profile_response.json()["message"] == "Perfil analítico atualizado"
    assert ops_response.status_code == 200
    assert fetched.status_code == 200
    assert fetched.json()["camera_family"] == "bullet"
    api_context.db.refresh(camera)
    assert camera.site_name == "Matriz"
    assert camera.camera_priority == "high"
    actions = {
        row.action
        for row in api_context.db.query(AuditLog).filter(AuditLog.user_id == admin.id)
    }
    assert {"camera_analytics_profile_update", "camera_ops_config_update"} <= actions


def test_configuration_routes_preserve_validation_and_authorization(api_context):
    _admin, camera = _seed_admin_and_camera(api_context)
    invalid = api_context.client.put(
        f"/api/cameras/{camera.id}/ops-config",
        json={"camera_priority": "urgent"},
    )
    operator = User(username="operator", password_hash="hash", role="operator", is_active=True)
    api_context.db.add(operator)
    api_context.db.commit()
    api_context.selected_user["value"] = operator
    forbidden = api_context.client.put(
        f"/api/cameras/{camera.id}/analytics-config",
        json={"roi_name": "Portao"},
    )

    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "Prioridade inválida"}
    assert forbidden.status_code == 403


def test_runtime_routes_delegate_remote_calls_and_record_audit(api_context, monkeypatch):
    admin, camera = _seed_admin_and_camera(api_context)
    calls = []
    monkeypatch.setattr(runtime_service, "remote_runtime_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_service,
        "start_runtime_camera",
        lambda camera_id, **kwargs: calls.append(("start", camera_id, kwargs)) or {"status": "started"},
    )
    monkeypatch.setattr(
        runtime_service,
        "stop_runtime_camera",
        lambda camera_id: calls.append(("stop", camera_id)) or {"status": "stopped"},
    )

    started = api_context.client.post(f"/api/cameras/{camera.id}/start?use_motion_test=false")
    stopped = api_context.client.post(f"/api/cameras/{camera.id}/stop")

    assert started.status_code == 200
    assert started.json() == {"status": "started"}
    assert stopped.status_code == 200
    assert stopped.json() == {"status": "stopped"}
    assert calls == [
        ("start", camera.id, {"use_motion_test": False, "restart_existing": False}),
        ("stop", camera.id),
    ]
    actions = {
        row.action
        for row in api_context.db.query(AuditLog).filter(AuditLog.user_id == admin.id)
    }
    assert {"camera_start", "camera_stop"} <= actions


def test_runtime_route_translates_remote_failure(api_context, monkeypatch):
    _admin, camera = _seed_admin_and_camera(api_context)
    monkeypatch.setattr(runtime_service, "remote_runtime_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_service,
        "start_runtime_camera",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeClientError("offline")),
    )

    response = api_context.client.post(f"/api/cameras/{camera.id}/start")

    assert response.status_code == 502
    assert response.json() == {"detail": "Runtime indisponivel: offline"}
