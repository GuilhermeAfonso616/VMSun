import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.core.security import verify_password
from app.db.base import Base
from app.db.models import AuditLog, User
from app.services.user_service import (
    UserServiceError,
    authenticate_user,
    record_user_logout,
    update_user_account,
)


@pytest.fixture
def user_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def test_authenticate_user_locks_account_after_five_failures(user_db):
    user = User(
        username="operator",
        password_hash=hash_password("correta"),
        role="operator",
        is_active=True,
    )
    user_db.add(user)
    user_db.commit()

    for _ in range(5):
        with pytest.raises(UserServiceError) as error:
            authenticate_user(user_db, username="operator", password="incorreta")
        assert error.value.status_code == 400

    user_db.refresh(user)
    assert user.login_attempts == 5
    assert user.lockout_until is not None
    assert user_db.query(AuditLog).filter(AuditLog.action == "account_locked").count() == 1

    with pytest.raises(UserServiceError) as blocked:
        authenticate_user(user_db, username="operator", password="correta")
    assert blocked.value.status_code == 403
    assert "Conta bloqueada" in blocked.value.detail


def test_update_user_rejects_invalid_role_without_committing_it(user_db):
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    target = User(username="target", password_hash="hash", role="viewer", is_active=True)
    user_db.add_all([admin, target])
    user_db.commit()

    with pytest.raises(UserServiceError) as error:
        update_user_account(user_db, user_id=target.id, actor=admin, role="root")

    assert error.value.status_code == 400
    user_db.refresh(target)
    assert target.role == "viewer"


def test_dev_can_set_weak_password_but_admin_still_uses_policy(user_db):
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    dev = User(username="dev", password_hash="hash", role="dev", is_active=True)
    target = User(username="target", password_hash=hash_password("Strong2026"), role="viewer", is_active=True)
    user_db.add_all([admin, dev, target])
    user_db.commit()

    with pytest.raises(UserServiceError) as error:
        update_user_account(user_db, user_id=target.id, actor=admin, password="1")

    assert error.value.status_code == 400
    user_db.refresh(target)
    assert not verify_password("1", target.password_hash)

    updated = update_user_account(user_db, user_id=target.id, actor=dev, password="1")

    assert updated.id == target.id
    assert verify_password("1", updated.password_hash)


def test_record_user_logout_persists_audit_context(user_db):
    user = User(
        username="operator",
        password_hash="hash",
        role="operator",
        is_active=True,
    )
    user_db.add(user)
    user_db.commit()

    record_user_logout(user_db, user=user, ip_address="127.0.0.9")

    audit = user_db.query(AuditLog).filter(AuditLog.action == "logout").one()
    assert audit.user_id == user.id
    assert audit.username == "operator"
    assert audit.details == "Logout realizado com sucesso"
    assert audit.ip_address == "127.0.0.9"
