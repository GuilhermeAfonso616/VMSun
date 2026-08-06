import pytest
from fastapi import HTTPException

from app.api.dependencies import require_role
from app.db.models import User


def test_require_role_accepts_allowed_role_and_dev_override():
    dependency = require_role(["admin"])
    admin = User(id=1, username="admin", role="admin", is_active=True)
    developer = User(id=2, username="dev", role="dev", is_active=True)

    assert dependency(current_user=admin) is admin
    assert dependency(current_user=developer) is developer


def test_require_role_rejects_disallowed_role():
    dependency = require_role(["admin"])
    operator = User(id=3, username="operator", role="operator", is_active=True)

    with pytest.raises(HTTPException) as error:
        dependency(current_user=operator)

    assert error.value.status_code == 403
