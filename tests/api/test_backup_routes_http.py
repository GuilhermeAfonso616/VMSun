import sqlite3

from fastapi.testclient import TestClient

from app import bootstrap
from app.api.dependencies import get_current_user
from app.application import create_app
from app.core.config import settings
from app.db.models import User


def test_export_backup_through_http_contract(tmp_path, monkeypatch):
    database_path = tmp_path / "analytics.db"
    environment_path = tmp_path / ".env"

    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE healthcheck (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO healthcheck (id) VALUES (1)")
    environment_path.write_text("APP_PORT=8000\n", encoding="utf-8")

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{database_path}")
    monkeypatch.setattr(settings, "app_base_dir", str(tmp_path))
    monkeypatch.setattr(bootstrap.settings, "app_role", "web")

    application = create_app(enable_lifecycle=False)
    application.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        username="admin",
        role="admin",
        is_active=True,
    )

    try:
        with TestClient(application) as client:
            response = client.post("/api/backup/export", json={"password": "segredo-forte"})

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"] == "attachment; filename=vms_backup.enc"
        assert response.content.startswith(b"VMSB")
    finally:
        application.dependency_overrides.clear()
