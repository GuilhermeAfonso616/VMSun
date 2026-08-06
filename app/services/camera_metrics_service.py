"""Coleta isolada do estado de conectividade exibido nas métricas da câmera."""

from __future__ import annotations

from app.db.models import Camera
from app.services.camera_health_monitor import camera_health_monitor
from app.services.runtime_client import probe_runtime_camera, remote_runtime_enabled


def probe_camera_reachability(camera: Camera) -> bool | None:
    """Retorna reachability local/remoto ou ``None`` quando a sonda falha."""

    try:
        if remote_runtime_enabled():
            return probe_runtime_camera(camera.id).get("reachable")
        return bool(camera_health_monitor._probe_camera_reachable(camera))
    except Exception:
        return None
