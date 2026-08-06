from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.db.models import User
from app.web.infrastructure import get_web_user
from app.web.routes import notification_routes


@pytest.fixture
def notification_web(monkeypatch):
    state = {"user": None, "rendered": []}
    application = FastAPI()
    application.include_router(notification_routes.router)
    application.dependency_overrides[get_web_user] = lambda: state["user"]

    def render(*, request, name, context, status_code=200):
        state["rendered"].append((name, context))
        return PlainTextResponse(name, status_code=status_code)

    monkeypatch.setattr(notification_routes.templates, "TemplateResponse", render)
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(client=client, state=state)
    finally:
        application.dependency_overrides.clear()


def _user(role):
    return User(id=80, username=f"notify-{role}", password_hash="hash", role=role, is_active=True)


def test_notification_page_permissions_and_management_context(notification_web):
    denied = notification_web.client.get("/notificacoes", follow_redirects=False)
    assert denied.status_code == 307

    notification_web.state["user"] = _user("supervisor")
    supervisor = notification_web.client.get("/notifications")
    assert supervisor.status_code == 200
    assert notification_web.state["rendered"][-1][1]["can_manage"] is False

    notification_web.state["user"] = _user("admin")
    admin = notification_web.client.get("/notificacoes")
    assert admin.status_code == 200
    assert notification_web.state["rendered"][-1][1]["can_manage"] is True
