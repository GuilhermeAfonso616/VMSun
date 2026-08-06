from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import AuditLog, User
from app.services.audit_service import log_audit


def test_log_audit_persists_actor_and_request_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        user = User(username="operator", password_hash="hash", role="operator", is_active=True)
        db.add(user)
        db.commit()

        entry = log_audit(
            db,
            "camera_start",
            user,
            "Camera 42",
            "127.0.0.1",
        )

        persisted = db.query(AuditLog).one()
        assert entry is persisted
        assert persisted.user_id == user.id
        assert persisted.username == "operator"
        assert persisted.action == "camera_start"
        assert persisted.details == "Camera 42"
        assert persisted.ip_address == "127.0.0.1"
    finally:
        db.close()
        engine.dispose()
