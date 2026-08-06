import json
from pathlib import Path
from fastapi.testclient import TestClient
from app.services import role_permissions_service as perm_service
from app.web.infrastructure import templates, require_web_auth
from app.db.models import User
from app.web.routes.dev_permissions_routes import dev_permissions_page
from fastapi import FastAPI, Request


def test_default_permissions_matrix():
    matrix = perm_service.load_role_permissions_matrix()
    assert "nav" in matrix
    assert "actions" in matrix
    assert "dashboard" in matrix["nav"]
    assert "control_ptz" in matrix["actions"]


def test_dev_role_always_has_all_permissions():
    assert perm_service.is_nav_item_visible("dev", "dashboard") is True
    assert perm_service.is_nav_item_visible("dev", "diagnostics") is True
    assert perm_service.is_action_allowed("dev", "control_ptz") is True
    assert perm_service.is_action_allowed("dev", "manage_users") is True


def test_save_and_load_custom_matrix(tmp_path, monkeypatch):
    test_file = tmp_path / "role_permissions.json"
    monkeypatch.setattr(perm_service, "PERMISSIONS_FILE_PATH", test_file)

    custom_matrix = {
        "nav": {
            "dashboard": ["admin", "supervisor", "dev"],
            "diagnostics": ["dev"],
        },
        "actions": {
            "control_ptz": ["operator", "dev"],
        }
    }

    perm_service.save_role_permissions_matrix(custom_matrix)
    assert test_file.exists()

    loaded = perm_service.load_role_permissions_matrix()
    assert "dev" in loaded["nav"]["diagnostics"]
    assert perm_service.is_nav_item_visible("admin", "diagnostics") is False
    assert perm_service.is_nav_item_visible("dev", "diagnostics") is True


def test_dev_permissions_template_render():
    app = FastAPI()

    @app.get("/dev/permissions")
    def mock_dev_permissions(request: Request):
        dummy_user = User(id=1, username="devuser", name="Dev User", role="dev")
        request.state.user = dummy_user
        request.state.effective_role = "dev"
        request.state.dev_preview_mode = False
        return dev_permissions_page(request, current_user=dummy_user)

    client = TestClient(app)
    response = client.get("/dev/permissions")
    assert response.status_code == 200
    assert "Matriz de Permissões" in response.text
    assert "Simulador de Visão" in response.text
