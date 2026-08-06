"""Composição do contexto da página detalhada de métricas da câmera."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Camera
from app.services.camera_metrics_service import probe_camera_reachability
from app.services.metrics_store import metrics_store
from app.web.camera_detail_presenter import mask_rtsp
from app.web.camera_overview_presenter import build_light_profile_recommendation
from app.web.operational_metrics_presenter import enrich_camera_metrics_payload


def build_camera_metrics_context(db: Session, camera_id: int) -> dict:
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    metrics = enrich_camera_metrics_payload(metrics_store.get_metrics(camera_id))
    light_profile = build_light_profile_recommendation(camera, metrics)
    rtsp_probe = None
    if camera is not None:
        rtsp_probe = {
            "target": mask_rtsp(camera.rtsp_url) or camera.ip or "-",
            "reachable": probe_camera_reachability(camera),
        }
    return {
        "camera": camera,
        "metrics": metrics,
        "light_profile": light_profile,
        "rtsp_probe": rtsp_probe,
    }
