"""Endpoints HTTP para snapshots e streams MJPEG de cameras."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from app.db.models import Camera
from app.services.camera_media_service import (
    MJPEG_MEDIA_TYPE,
    build_stream_status_frame,
    frame_to_jpeg_bytes,
    generate_camera_raw_mjpeg,
    generate_mjpeg_bytes,
    generate_status_mjpeg,
    get_boxed_stream_bytes,
    get_camera_snapshot_bytes,
    get_processed_stream_bytes,
)
from app.services.media_backbone_service import MediaBackboneUnavailable
from app.web.camera_detail_presenter import camera_ia1_visual_threshold
from app.web.infrastructure import get_scoped_db


router = APIRouter()


@router.get("/cameras/{camera_id}/stream/offline")
def camera_stream_offline(camera_id: int, state: str | None = None):
    state_label = (state or "offline").replace("_", " ").upper()
    return StreamingResponse(
        generate_status_mjpeg(f"CAMERA {state_label}", f"camera_id={camera_id}"),
        media_type=MJPEG_MEDIA_TYPE,
    )


@router.get("/cameras/{camera_id}/stream/raw")
def camera_stream_raw(camera_id: int):
    db = get_scoped_db()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if camera is None or not camera.rtsp_url:
            raise HTTPException(status_code=404, detail="Stream RTSP não encontrado")
        return StreamingResponse(
            generate_camera_raw_mjpeg(camera_id, camera.rtsp_url),
            media_type=MJPEG_MEDIA_TYPE,
        )
    finally:
        db.close()


@router.get("/cameras/{camera_id}/snapshot")
def camera_snapshot(camera_id: int):
    db = get_scoped_db()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if camera is None:
            raise HTTPException(status_code=404, detail="Camera nao encontrada")
        try:
            jpg_bytes = get_camera_snapshot_bytes(camera_id, camera.rtsp_url)
        except MediaBackboneUnavailable as exc:
            raise HTTPException(status_code=503, detail=exc.code) from exc
        if not jpg_bytes:
            jpg_bytes = frame_to_jpeg_bytes(
                build_stream_status_frame(
                    "SNAPSHOT INDISPONIVEL",
                    "Nao foi possivel gerar a imagem.",
                )
            )
        return Response(content=jpg_bytes or b"", media_type="image/jpeg")
    finally:
        db.close()


@router.get("/cameras/{camera_id}/stream/processed")
def camera_stream_processed(camera_id: int):
    return StreamingResponse(
        generate_mjpeg_bytes(get_processed_stream_bytes, camera_id),
        media_type=MJPEG_MEDIA_TYPE,
    )


@router.get("/cameras/{camera_id}/stream/boxed")
def camera_stream_boxed(camera_id: int):
    db = get_scoped_db()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if camera is None:
            raise HTTPException(status_code=404, detail="Camera nao encontrada")
        minimum_confidence = camera_ia1_visual_threshold(camera)
    finally:
        db.close()
    return StreamingResponse(
        generate_mjpeg_bytes(
            lambda current_camera_id: get_boxed_stream_bytes(
                current_camera_id,
                minimum_confidence,
            ),
            camera_id,
        ),
        media_type=MJPEG_MEDIA_TYPE,
    )


@router.get("/cameras/{camera_id}/stream")
def camera_stream(camera_id: int):
    return StreamingResponse(
        generate_mjpeg_bytes(get_processed_stream_bytes, camera_id),
        media_type=MJPEG_MEDIA_TYPE,
    )
