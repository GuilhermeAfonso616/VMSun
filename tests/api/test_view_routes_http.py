from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import bootstrap
from app.api.dependencies import get_current_user, get_db
from app.application import create_app
from app.db.base import Base
from app.db.models import User


def test_view_presets_round_trip_through_http_dependencies(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    user = User(username="operator", password_hash="hash", role="operator", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    monkeypatch.setattr(bootstrap.settings, "app_role", "web")
    application = create_app(enable_lifecycle=False)

    def override_db():
        yield db

    application.dependency_overrides[get_db] = override_db
    application.dependency_overrides[get_current_user] = lambda: user

    try:
        with TestClient(application) as client:
            created = client.post(
                "/api/view-presets",
                json={
                    "id": "view_http",
                    "name": "Guarita",
                    "grid_size": 4,
                    "camera_ids": [1, 2, None, 4],
                    "hide_offline": True,
                    "boxes_enabled": False,
                    "view_config": {"fit": "contain"},
                },
            )
            listed = client.get("/api/view-presets")

        assert created.status_code == 200
        assert created.json() == {"status": "success"}
        assert listed.status_code == 200
        assert listed.json() == [
            {
                "id": "view_http",
                "name": "Guarita",
                "grid_size": 4,
                "camera_ids": [1, 2, None, 4],
                "hide_offline": True,
                "boxes_enabled": False,
                "view_config": {"fit": "contain"},
                "is_shared": False,
                "owner_username": "operator",
                "can_manage": True,
            }
        ]
    finally:
        application.dependency_overrides.clear()
        db.close()
        engine.dispose()
