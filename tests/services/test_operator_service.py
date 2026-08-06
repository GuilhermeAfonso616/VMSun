import json
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera
from app.services import operator_service
from app.services.operator_service import (
    build_operator_bootstrap,
    operator_performance_filename,
    store_operator_performance,
)


def _camera_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    camera = Camera(
        name="Entrada",
        ip="10.0.0.10",
        username="camera-user",
        password="camera-secret",
        rtsp_url="rtsp://camera-user:camera-secret@10.0.0.10/main",
        status="running_motion_test",
        camera_priority="high",
        source_type="camera",
        is_deleted=False,
    )
    deleted = Camera(
        name="Removida",
        ip="10.0.0.11",
        username="user",
        password="secret",
        rtsp_url="rtsp://10.0.0.11/main",
        is_deleted=True,
    )
    db.add_all([camera, deleted])
    db.commit()
    db.refresh(camera)
    return engine, db, camera


def test_operator_bootstrap_uses_health_and_never_exposes_credentials(monkeypatch):
    engine, db, camera = _camera_db()
    try:
        monkeypatch.setattr(
            operator_service,
            "get_runtime_health_snapshot",
            lambda: {
                "cameras": [
                    {
                        "camera_id": camera.id,
                        "health_status": "running",
                        "is_running": True,
                        "gateway_state": "online",
                    }
                ]
            },
        )
        monkeypatch.setattr(operator_service, "webrtc_gateway_is_enabled", lambda: True)
        monkeypatch.setattr(operator_service, "webrtc_rtsp_public_base_url", lambda: "")
        monkeypatch.setattr(operator_service, "webrtc_public_base_url", lambda: "https://media.test")
        monkeypatch.setattr(operator_service, "build_webrtc_rtsp_url", lambda _path: "")
        monkeypatch.setattr(operator_service, "build_webrtc_player_url", lambda path: f"https://media.test/{path}")

        payload = build_operator_bootstrap(
            db,
            request_hostname="vms.local",
            register_paths=False,
            now=datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc),
        )

        assert payload["camera_count"] == 1
        row = payload["cameras"][0]
        assert row["status"] == "running"
        assert row["health_status"] == "running"
        assert row["media_rtsp_url"].startswith("rtsp://vms.local:8554/")
        assert row["registration_reason"] == "not_registered_by_request"
        serialized = json.dumps(payload)
        assert "camera-user" not in serialized
        assert "camera-secret" not in serialized
    finally:
        db.close()
        engine.dispose()


def test_performance_filename_sanitizes_machine_and_uses_capture_time():
    filename = operator_performance_filename(
        {
            "machine_name": "PC Operador / 01",
            "captured_at_utc": "2026-07-17T15:30:00Z",
        }
    )

    assert filename == "operator_perf_PC_Operador_01_20260717T153000Z.json"


def test_store_operator_performance_writes_json_and_reports_remote_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(operator_service.settings, "runtime_state_dir", str(tmp_path))
    monkeypatch.setattr(operator_service.onedrive_client, "enabled", lambda: True)
    monkeypatch.setattr(
        operator_service.onedrive_client,
        "upload_operator_performance_log",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("drive offline")),
    )
    received_at = datetime(2026, 7, 17, 16, 0, tzinfo=timezone.utc)

    result = store_operator_performance(
        {"machine_name": "OPS-01", "fps": 24.5},
        client_host="10.0.0.50",
        received_at=received_at,
    )

    stored = json.loads((tmp_path / "operator_performance" / result["filename"]).read_text(encoding="utf-8"))
    assert stored["client_host"] == "10.0.0.50"
    assert stored["received_at_utc"] == received_at.isoformat()
    assert result["onedrive_enabled"] is True
    assert result["onedrive"] is None
    assert result["onedrive_error"] == "drive offline"
