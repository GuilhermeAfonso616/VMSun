import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import bootstrap
from app.core.credential_crypto import PREFIX
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import Camera, InstallationState, User


def test_legacy_defaults_are_neutralized_and_camera_password_is_migrated(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        db.add_all(
            [
                User(username="admin", password_hash=hash_password("admin"), role="admin", is_active=True),
                User(username="dev", password_hash=hash_password("dev123"), role="dev", is_active=True),
                Camera(
                    name="Camera",
                    ip="10.0.0.2",
                    username="device",
                    password="legacy-secret",
                    rtsp_url="rtsp://device:legacy-secret@10.0.0.2/live",
                ),
            ]
        )
        db.commit()
        db.execute(
            text("UPDATE cameras SET password = 'legacy-secret', "
                 "rtsp_url = 'rtsp://device:legacy-secret@10.0.0.2/live'")
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(bootstrap, "SessionLocal", factory)
    bootstrap._initialize_security_state(logging.getLogger("test.security-migration"))

    db = factory()
    try:
        admin = db.query(User).filter(User.username == "admin").one()
        dev = db.query(User).filter(User.username == "dev").one()
        assert admin.must_change_password is True
        assert dev.is_active is False
        assert db.get(InstallationState, 1).setup_completed is True
        stored_password, stored_url = db.execute(
            text("SELECT password, rtsp_url FROM cameras")
        ).one()
        assert stored_password.startswith(PREFIX)
        assert stored_url.startswith(PREFIX)
        db.expire_all()
        assert db.query(Camera).one().password == "legacy-secret"
    finally:
        db.close()
        engine.dispose()
