"""Pagina de detalhes, eventos recentes e diagnostico RTSP de cameras."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.camera.rtsp_discovery import build_common_rtsp_candidates, probe_rtsp_candidates
from app.core.url_safety import mask_url_credentials
from app.db.models import Camera, Event, User
from app.web.camera_detail_presenter import (
    build_camera_detail_context,
    build_camera_detail_payload,
    enrich_event,
    get_camera_map,
    serialize_camera_detail_event,
)
from app.web.infrastructure import get_scoped_db, require_web_auth, templates


router = APIRouter()
_AUTHORIZED_ROLES = ["admin", "supervisor"]


@router.get("/cameras/{camera_id}")
def camera_detail(
    request: Request,
    camera_id: int,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    del current_user
    db = get_scoped_db()
    try:
        camera, events = build_camera_detail_payload(db, camera_id)
        return templates.TemplateResponse(
            request=request,
            name="camera_detail.html",
            context=build_camera_detail_context(
                request=request,
                camera=camera,
                events=events,
            ),
        )
    finally:
        db.close()


@router.get("/cameras/{camera_id}/events-data")
def camera_detail_events_data(camera_id: int):
    db = get_scoped_db()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if camera is None:
            raise HTTPException(status_code=404, detail="Câmera não encontrada")

        camera_map = get_camera_map(db)
        events = (
            db.query(Event)
            .filter(Event.camera_id == camera_id)
            .order_by(Event.id.desc())
            .limit(50)
            .all()
        )
        for event in events:
            enrich_event(event, camera_map)

        response = JSONResponse(
            {
                "camera_id": camera_id,
                "events": [serialize_camera_detail_event(event) for event in events],
            }
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    finally:
        db.close()


@router.post("/cameras/{camera_id}/rtsp-test")
def test_rtsp_candidates_existing(
    request: Request,
    camera_id: int,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    del current_user
    db = get_scoped_db()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if camera is None:
            raise HTTPException(status_code=404, detail="Câmera não encontrada")

        candidates = build_common_rtsp_candidates(
            ip=camera.ip,
            username=camera.username,
            password=camera.password,
        )
        results = [
            {
                **item,
                "url": mask_url_credentials(str(item.get("url") or "")),
                "masked_url": str(item.get("masked_url") or "")
                or mask_url_credentials(str(item.get("url") or "")),
            }
            for item in probe_rtsp_candidates(candidates)
        ]
        camera, events = build_camera_detail_payload(db, camera_id)
        return templates.TemplateResponse(
            request=request,
            name="camera_detail.html",
            context=build_camera_detail_context(
                request=request,
                camera=camera,
                events=events,
                rtsp_test_results=results,
                rtsp_test_source={"ip": camera.ip, "username": camera.username},
            ),
        )
    finally:
        db.close()
