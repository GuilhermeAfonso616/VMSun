from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.credential_crypto import PREFIX
from app.db.base import Base
from app.db.models import Camera


def test_camera_password_is_encrypted_at_rest_and_transparent_to_domain(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'credentials.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        camera = Camera(
            name="Portaria",
            ip="10.0.0.15",
            username="operator",
            password="CredencialPrivada2026!",
            rtsp_url="rtsp://operator:CredencialPrivada2026!@10.0.0.15/live",
        )
        db.add(camera)
        db.commit()
        camera_id = camera.id

        stored_password, stored_url = db.execute(
            text("SELECT password, rtsp_url FROM cameras WHERE id = :id"), {"id": camera_id}
        ).one()
        assert stored_password.startswith(PREFIX)
        assert stored_url.startswith(PREFIX)
        assert "CredencialPrivada2026!" not in stored_password
        assert "CredencialPrivada2026!" not in stored_url

        db.expire_all()
        assert db.get(Camera, camera_id).password == "CredencialPrivada2026!"
        assert db.get(Camera, camera_id).rtsp_url.endswith("@10.0.0.15/live")
    finally:
        db.close()
        engine.dispose()
