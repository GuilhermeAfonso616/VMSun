from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db
from app.application import create_app
from app.db.base import Base
from app.db.models import InstallationState, UserSession


def test_initial_setup_cookie_and_revocable_session(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    application = create_app(enable_lifecycle=False)

    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = override_db
    try:
        with TestClient(application, base_url="https://testserver") as client:
            assert client.get("/api/auth/setup/status").json() == {"setup_required": True}

            weak = client.post(
                "/api/auth/setup",
                json={"username": "owner", "password": "senha-fraca"},
            )
            assert weak.status_code == 400

            created = client.post(
                "/api/auth/setup",
                json={"username": "owner", "name": "Proprietario", "password": "ProdutoFinal2026!"},
            )
            assert created.status_code == 201
            cookie = created.headers["set-cookie"]
            assert "HttpOnly" in cookie
            assert "Secure" in cookie
            assert "SameSite=lax" in cookie
            assert client.get("/api/auth/me").status_code == 200

            duplicate = client.post(
                "/api/auth/setup",
                json={"username": "other", "password": "OutraSenha2026!"},
            )
            assert duplicate.status_code == 409

            logged_out = client.post("/api/auth/logout")
            assert logged_out.status_code == 200
            assert client.get("/api/auth/me").status_code == 401

            desktop_login = client.post(
                "/api/auth/login",
                json={"username": "owner", "password": "ProdutoFinal2026!"},
            )
            bearer = desktop_login.json()["access_token"]
            client.cookies.clear()
            headers = {"Authorization": f"Bearer {bearer}"}
            assert client.get("/api/auth/me", headers=headers).status_code == 200
            assert client.post("/api/auth/logout", headers=headers).status_code == 200
            assert client.get("/api/auth/me", headers=headers).status_code == 401

        db = factory()
        try:
            assert db.get(InstallationState, 1).setup_completed is True
            assert db.query(UserSession).filter(UserSession.revoked_at.is_not(None)).count() == 2
        finally:
            db.close()
    finally:
        application.dependency_overrides.clear()
        engine.dispose()
