import pytest

from app.core.security import hash_password
from app.db.models import AuditLog, User, UserSession


@pytest.fixture
def auth_api(api_context):
    db = api_context.db
    admin = User(
        username="admin",
        password_hash=hash_password("admin-secret"),
        name="Administrador",
        role="admin",
        is_active=True,
    )
    operator = User(
        username="operator",
        password_hash=hash_password("operator-secret"),
        role="operator",
        is_active=True,
    )
    viewer = User(
        username="viewer",
        password_hash=hash_password("viewer-secret"),
        role="viewer",
        is_active=True,
    )
    db.add_all([admin, operator, viewer])
    db.commit()
    for user in (admin, operator, viewer):
        db.refresh(user)

    api_context.selected_user["value"] = admin
    api_context.admin = admin
    api_context.operator = operator
    api_context.viewer = viewer
    return api_context


def test_login_records_failures_and_returns_existing_contract(auth_api):
    failed = auth_api.client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "incorreta"},
    )
    succeeded = auth_api.client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-secret"},
    )

    assert failed.status_code == 400
    assert failed.json() == {"detail": "Usuario ou senha incorretos."}
    assert succeeded.status_code == 200
    assert succeeded.json()["token_type"] == "bearer"
    assert succeeded.json()["user"] == {
        "username": "operator",
        "name": None,
        "role": "operator",
    }
    assert succeeded.json()["access_token"]

    auth_api.db.refresh(auth_api.operator)
    assert auth_api.operator.login_attempts == 0
    actions = [
        row.action
        for row in auth_api.db.query(AuditLog)
        .filter(AuditLog.user_id == auth_api.operator.id)
        .order_by(AuditLog.id)
    ]
    assert actions == ["login_failed", "login_success"]


def test_concurrent_session_policy_warns_limits_and_supports_remote_revoke(auth_api):
    auth_api.operator.max_active_sessions = 2
    auth_api.db.commit()

    first = auth_api.client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-secret"},
        headers={"user-agent": "First-PC"},
    )
    auth_api.client.cookies.clear()
    second = auth_api.client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-secret"},
        headers={"user-agent": "Second-PC"},
    )
    auth_api.client.cookies.clear()
    third = auth_api.client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-secret"},
        headers={"user-agent": "Third-PC"},
    )

    assert first.status_code == 200
    assert first.json()["concurrent_session_notice"] is None
    assert second.json()["other_active_sessions"] == 1
    assert "outro(s) dispositivo(s)" in second.json()["concurrent_session_notice"]
    assert third.json()["sessions_revoked"] == 1
    assert "foram encerradas" in third.json()["concurrent_session_notice"]

    active = (
        auth_api.db.query(UserSession)
        .filter(
            UserSession.user_id == auth_api.operator.id,
            UserSession.revoked_at.is_(None),
        )
        .all()
    )
    assert len(active) == 2
    assert {item.user_agent for item in active} == {"Second-PC", "Third-PC"}

    auth_api.selected_user["value"] = auth_api.admin
    listed = auth_api.client.get(f"/api/users/{auth_api.operator.id}/sessions")
    assert listed.status_code == 200
    assert len(listed.json()) == 2
    remote = next(item for item in listed.json() if not item["is_current"])
    closed = auth_api.client.delete(
        f"/api/users/{auth_api.operator.id}/sessions/{remote['id']}"
    )
    assert closed.status_code == 200
    assert closed.json()["current_session_revoked"] is False

    listed_again = auth_api.client.get(f"/api/users/{auth_api.operator.id}/sessions")
    assert len(listed_again.json()) == 1

    auth_api.client.cookies.clear()
    fourth = auth_api.client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-secret"},
        headers={"user-agent": "Fourth-PC"},
    )
    assert fourth.status_code == 200
    auth_api.selected_user["value"] = auth_api.admin
    reduced = auth_api.client.put(
        f"/api/users/{auth_api.operator.id}",
        json={"max_active_sessions": 1},
    )
    assert reduced.status_code == 200
    assert reduced.json()["max_active_sessions"] == 1
    after_reduction = auth_api.client.get(f"/api/users/{auth_api.operator.id}/sessions")
    assert len(after_reduction.json()) == 1


def test_relogin_from_same_client_replaces_its_previous_session(auth_api):
    first = auth_api.client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-secret"},
    )
    second = auth_api.client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-secret"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["other_active_sessions"] == 0
    active_count = (
        auth_api.db.query(UserSession)
        .filter(
            UserSession.user_id == auth_api.operator.id,
            UserSession.revoked_at.is_(None),
        )
        .count()
    )
    assert active_count == 1


def test_user_crud_is_admin_only_and_audited(auth_api):
    auth_api.selected_user["value"] = auth_api.operator
    forbidden = auth_api.client.get("/api/users")
    assert forbidden.status_code == 403
    forbidden_sessions = auth_api.client.get(
        f"/api/users/{auth_api.operator.id}/sessions"
    )
    assert forbidden_sessions.status_code == 403

    auth_api.selected_user["value"] = auth_api.admin
    created = auth_api.client.post(
        "/api/users",
        json={
            "username": "supervisor",
            "password": "Supervisor2026!",
            "name": "Supervisao",
            "role": "supervisor",
            "max_active_sessions": 1,
        },
    )
    assert created.status_code == 200
    user_id = created.json()["id"]
    assert created.json()["max_active_sessions"] == 1

    updated = auth_api.client.put(
        f"/api/users/{user_id}",
        json={
            "name": "Supervisao Noturna",
            "role": "operator",
            "is_active": False,
            "max_active_sessions": None,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Supervisao Noturna"
    assert updated.json()["role"] == "operator"
    assert updated.json()["is_active"] is False
    assert updated.json()["max_active_sessions"] is None

    target = auth_api.db.get(User, user_id)
    target.login_attempts = 5
    auth_api.db.commit()
    unlocked = auth_api.client.post(f"/api/users/{user_id}/unlock")
    assert unlocked.status_code == 200
    auth_api.db.refresh(target)
    assert target.login_attempts == 0

    deleted = auth_api.client.delete(f"/api/users/{user_id}")
    assert deleted.status_code == 200
    assert auth_api.db.get(User, user_id) is None

    self_delete = auth_api.client.delete(f"/api/users/{auth_api.admin.id}")
    assert self_delete.status_code == 400

    actions = {
        row.action
        for row in auth_api.db.query(AuditLog).filter(AuditLog.user_id == auth_api.admin.id)
    }
    assert {"user_create", "user_update", "user_unlock", "user_delete"} <= actions


def test_audit_http_visibility_and_client_registration(auth_api):
    auth_api.db.add_all(
        [
            AuditLog(user_id=auth_api.admin.id, username="admin", action="admin_action"),
            AuditLog(user_id=auth_api.operator.id, username="operator", action="operator_action"),
            AuditLog(user_id=auth_api.viewer.id, username="viewer", action="viewer_action"),
        ]
    )
    auth_api.db.commit()
    auth_api.selected_user["value"] = auth_api.operator

    listed = auth_api.client.get("/api/audit-logs")
    assert listed.status_code == 200
    assert {item["action"] for item in listed.json()} == {"operator_action", "viewer_action"}

    registered = auth_api.client.post(
        "/api/audit-logs",
        json={"action": "monitor_opened", "details": "Monitor principal"},
    )
    assert registered.status_code == 200
    assert registered.json() == {"status": "success"}
    saved = auth_api.db.query(AuditLog).filter(AuditLog.action == "monitor_opened").one()
    assert saved.user_id == auth_api.operator.id
    assert saved.details == "Monitor principal"
