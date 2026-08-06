from app.api.routers import notification_routes
from app.db.models import NotificationChannel, NotificationDelivery, User


def test_notification_channel_crud_test_and_delivery_visibility(api_context, monkeypatch):
    admin = User(username="notify-admin", password_hash="hash", role="admin", is_active=True)
    api_context.db.add(admin)
    api_context.db.commit()
    api_context.selected_user["value"] = admin

    created = api_context.client.post(
        "/api/notifications/channels",
        json={
            "name": "SOC",
            "target": "https://hooks.example.test/alarms?token=hidden",
            "signing_secret": "private-signature",
            "min_severity": "high",
            "event_types": ["intrusion"],
        },
    )
    assert created.status_code == 201
    channel_id = created.json()["id"]
    assert created.json()["target_masked"] == "https://hooks.example.test/…"
    assert "hidden" not in created.text
    assert created.json()["signing_secret_configured"] is True

    def fake_delivery(delivery_id, db, *, force=False):
        delivery = db.get(NotificationDelivery, delivery_id)
        delivery.status = "sent"
        delivery.http_status = 204
        db.commit()
        return delivery

    monkeypatch.setattr(notification_routes, "deliver_notification", fake_delivery)
    tested = api_context.client.post(f"/api/notifications/channels/{channel_id}/test")
    assert tested.status_code == 200
    assert tested.json()["status"] == "sent"

    listed = api_context.client.get("/api/notifications/deliveries")
    assert listed.status_code == 200
    assert listed.json()[0]["channel_name"] == "SOC"

    updated = api_context.client.put(
        f"/api/notifications/channels/{channel_id}",
        json={"enabled": False, "min_severity": "critical", "clear_signing_secret": True},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["signing_secret_configured"] is False
    assert api_context.db.get(NotificationChannel, channel_id).target.endswith("token=hidden")


def test_notification_configuration_is_admin_only(api_context):
    operator = User(username="notify-operator", password_hash="hash", role="operator", is_active=True)
    api_context.db.add(operator)
    api_context.db.commit()
    api_context.selected_user["value"] = operator

    assert api_context.client.get("/api/notifications/channels").status_code == 403
    assert api_context.client.post(
        "/api/notifications/channels",
        json={"name": "Denied", "target": "https://example.test/hook"},
    ).status_code == 403


def test_notification_channel_rejects_credentials_embedded_in_url(api_context):
    admin = User(username="url-admin", password_hash="hash", role="admin", is_active=True)
    api_context.db.add(admin)
    api_context.db.commit()
    api_context.selected_user["value"] = admin

    response = api_context.client.post(
        "/api/notifications/channels",
        json={"name": "Unsafe", "target": "https://user:password@example.test/hook"},
    )
    assert response.status_code == 400
