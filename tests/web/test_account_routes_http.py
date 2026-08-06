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
from app.web.routes import account_routes


@pytest.fixture
def account_http(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    state = {"user": None}
    application = FastAPI()
    application.include_router(account_routes.router)
    application.dependency_overrides[get_web_user] = lambda: state["user"]
    monkeypatch.setattr(account_routes, "get_scoped_db", factory)
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(client=client, state=state)
    finally:
        application.dependency_overrides.clear()
        engine.dispose()


def _user(role: str = "operator") -> User:
    return User(
        id=71,
        username="web-user",
        password_hash="hash",
        role=role,
        is_active=True,
    )


def _capture_template(monkeypatch):
    captured = []

    def render(*, request, name, context, status_code=200):
        captured.append({"name": name, "context": context})
        return PlainTextResponse(name, status_code=status_code)

    monkeypatch.setattr(account_routes.templates, "TemplateResponse", render)
    return captured


def test_login_renders_for_anonymous_and_redirects_authenticated_user(
    account_http,
    monkeypatch,
):
    captured = _capture_template(monkeypatch)

    anonymous = account_http.client.get("/login", follow_redirects=False)
    account_http.state["user"] = _user()
    authenticated = account_http.client.get("/login", follow_redirects=False)

    assert anonymous.status_code == 307
    assert anonymous.headers["location"] == "/setup"
    assert authenticated.status_code == 307
    assert authenticated.headers["location"] == "/monitor"


def test_setup_page_is_available_only_before_initial_configuration(account_http, monkeypatch):
    captured = _capture_template(monkeypatch)
    response = account_http.client.get("/setup", follow_redirects=False)

    assert response.status_code == 200
    assert captured[0]["name"] == "setup.html"


def test_logout_records_context_deletes_cookie_and_survives_audit_failure(
    account_http,
    monkeypatch,
):
    account_http.state["user"] = _user()
    observed = []
    monkeypatch.setattr(
        account_routes,
        "record_user_logout",
        lambda _db, **kwargs: observed.append(kwargs),
    )

    response = account_http.client.get("/logout", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/login"
    assert observed[0]["user"].username == "web-user"
    assert observed[0]["ip_address"] == "testclient"
    assert "session_token=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]

    monkeypatch.setattr(
        account_routes,
        "record_user_logout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db")),
    )
    fallback = account_http.client.get("/logout", follow_redirects=False)
    assert fallback.status_code == 307
    assert fallback.headers["location"] == "/login"


def test_profile_preserves_roles_and_requires_session(account_http, monkeypatch):
    captured = _capture_template(monkeypatch)

    denied = account_http.client.get("/perfil", follow_redirects=False)
    account_http.state["user"] = _user("viewer")
    allowed = account_http.client.get("/perfil")

    assert denied.status_code == 307
    assert denied.headers["location"] == "/login"
    assert allowed.status_code == 200
    assert captured[0]["name"] == "perfil.html"


def test_user_page_keeps_aliases_and_admin_only_policy(account_http, monkeypatch):
    captured = _capture_template(monkeypatch)
    account_http.state["user"] = _user("admin")

    users = account_http.client.get("/users")
    usuarios = account_http.client.get("/usuarios")

    assert users.status_code == 200
    assert usuarios.status_code == 200
    assert [item["name"] for item in captured] == [
        "usuarios.html",
        "usuarios.html",
    ]

    account_http.state["user"] = _user("operator")
    denied = account_http.client.get("/users", follow_redirects=False)
    assert denied.status_code == 303
    assert denied.headers["location"] == "/"
