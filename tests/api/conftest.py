from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import bootstrap
from app.api.dependencies import get_current_user, get_db
from app.application import create_app
from app.db.base import Base
from app.db.models import User


@pytest.fixture
def api_context(monkeypatch):
    """Aplicacao real com banco isolado e identidade substituivel por teste."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    selected_user = {
        "value": User(id=0, username="anonymous", role="admin", is_active=True),
    }

    monkeypatch.setattr(bootstrap.settings, "app_role", "web")
    application = create_app(enable_lifecycle=False)

    def override_db():
        yield db

    application.dependency_overrides[get_db] = override_db
    application.dependency_overrides[get_current_user] = lambda: selected_user["value"]

    try:
        with TestClient(application) as client:
            yield SimpleNamespace(
                application=application,
                client=client,
                db=db,
                selected_user=selected_user,
            )
    finally:
        application.dependency_overrides.clear()
        db.close()
        engine.dispose()
