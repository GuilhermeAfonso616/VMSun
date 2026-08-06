"""Reconcilia os paths canonicos do MediaMTX a partir das cameras persistidas."""

from __future__ import annotations

import threading

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.db.models import Camera
from app.services.media_backbone_service import ensure_camera_media_path
from app.services.webrtc_gateway_client import (
    invalidate_webrtc_camera_path_cache,
    list_webrtc_camera_paths,
    unregister_webrtc_camera_path,
)

logger = get_logger("app.services.media_backbone_reconciler")


class MediaBackboneReconciler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def reconcile_once(self) -> dict[str, object]:
        if not bool(settings.media_backbone_reconcile_enabled):
            return {"ok": True, "disabled": True, "created_or_updated": 0, "removed": 0, "failures": 0}
        if not self._lock.acquire(blocking=False):
            return {"ok": True, "skipped": "already_running", "created_or_updated": 0, "removed": 0, "failures": 0}
        try:
            db = SessionLocal()
            try:
                query = db.query(Camera)
                if getattr(Camera, "is_deleted", None) is not None:
                    query = query.filter(Camera.is_deleted == False)  # noqa: E712
                cameras = [camera for camera in query.all() if str(camera.rtsp_url or "").strip()]
            finally:
                db.close()
            expected = {f"cam_{int(camera.id)}" for camera in cameras}
            listed = list_webrtc_camera_paths()
            existing = set(listed.get("items", [])) if listed.get("ok") else set()
            changed = failures = removed = 0
            for camera in cameras:
                if listed.get("ok") and f"cam_{int(camera.id)}" not in existing:
                    invalidate_webrtc_camera_path_cache(int(camera.id))
                result = ensure_camera_media_path(camera.id, camera.rtsp_url)
                if result.ok:
                    changed += int(result.created or result.updated)
                else:
                    failures += 1
            if bool(settings.media_backbone_remove_orphan_paths) and listed.get("ok"):
                for path in listed.get("items", []):
                    if path not in expected:
                        try:
                            camera_id = int(str(path).removeprefix("cam_"))
                        except ValueError:
                            continue
                        if unregister_webrtc_camera_path(camera_id).get("ok"):
                            removed += 1
            summary = {"ok": failures == 0, "created_or_updated": changed, "removed": removed, "failures": failures}
            logger.info("Media backbone reconciled", extra={"action": "media_backbone_reconcile", **summary})
            return summary
        finally:
            self._lock.release()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="media-backbone-reconciler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.reconcile_once()
            except Exception:
                logger.exception("Media backbone reconcile failed", extra={"action": "media_backbone_reconcile", "status": "error"})
            self._stop.wait(max(5.0, float(settings.media_backbone_reconcile_interval_seconds)))


media_backbone_reconciler = MediaBackboneReconciler()
