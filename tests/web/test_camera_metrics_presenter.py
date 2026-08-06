from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera
from app.web import camera_metrics_presenter


def test_camera_metrics_context_combines_metrics_profile_and_masked_probe(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        camera = Camera(
            name="Portaria",
            ip="10.0.0.7",
            username="operator",
            password="secret",
            rtsp_url="rtsp://operator:secret@10.0.0.7/main",
        )
        db.add(camera)
        db.commit()
        camera_id = camera.id
        monkeypatch.setattr(
            camera_metrics_presenter.metrics_store,
            "get_metrics",
            lambda received_id: {"camera_id": received_id, "fps": 12.5},
        )
        monkeypatch.setattr(
            camera_metrics_presenter,
            "enrich_camera_metrics_payload",
            lambda payload: {**payload, "enriched": True},
        )
        monkeypatch.setattr(
            camera_metrics_presenter,
            "build_light_profile_recommendation",
            lambda received_camera, metrics: {
                "camera_id": received_camera.id,
                "fps": metrics["fps"],
            },
        )
        monkeypatch.setattr(
            camera_metrics_presenter,
            "probe_camera_reachability",
            lambda _camera: True,
        )

        context = camera_metrics_presenter.build_camera_metrics_context(db, camera_id)

    engine.dispose()
    assert context["camera"].name == "Portaria"
    assert context["metrics"] == {
        "camera_id": camera_id,
        "fps": 12.5,
        "enriched": True,
    }
    assert context["light_profile"] == {"camera_id": camera_id, "fps": 12.5}
    assert "secret" not in context["rtsp_probe"]["target"]
    assert context["rtsp_probe"]["reachable"] is True


def test_camera_metrics_context_keeps_probe_absent_for_unknown_camera(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(
        camera_metrics_presenter.metrics_store,
        "get_metrics",
        lambda _camera_id: None,
    )
    monkeypatch.setattr(
        camera_metrics_presenter,
        "enrich_camera_metrics_payload",
        lambda payload: payload or {},
    )
    monkeypatch.setattr(
        camera_metrics_presenter,
        "build_light_profile_recommendation",
        lambda camera, metrics: {"camera": camera, "metrics": metrics},
    )
    with factory() as db:
        context = camera_metrics_presenter.build_camera_metrics_context(db, 999)

    engine.dispose()
    assert context["camera"] is None
    assert context["metrics"] == {}
    assert context["rtsp_probe"] is None
