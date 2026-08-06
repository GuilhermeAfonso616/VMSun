"""Rotas web que adaptam o camera-gateway para clientes do monitor."""

from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from app.services.camera_gateway_client import gateway_camera_url, gateway_is_enabled


router = APIRouter(prefix="/monitor/gateway", tags=["monitor-gateway"])


def _gateway_proxy_request(camera_id: int, suffix: str, timeout_seconds: float = 8.0):
    if not gateway_is_enabled():
        raise HTTPException(status_code=503, detail="Gateway de cameras desabilitado")

    request = UrlRequest(
        gateway_camera_url(camera_id, suffix),
        headers={
            "Accept": "image/jpeg, multipart/x-mixed-replace",
            "User-Agent": "ServerAnaliticoVMS-monitor-proxy",
        },
    )
    try:
        return urlopen(request, timeout=max(1.0, float(timeout_seconds)))
    except HTTPError as exc:
        raise HTTPException(status_code=int(exc.code), detail="Gateway nao entregou frame") from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Gateway indisponivel para o monitor") from exc


@router.get("/cameras/{camera_id}/snapshot.jpg")
@router.get("/cameras/{camera_id}/snapshot")
def monitor_gateway_camera_snapshot(camera_id: int):
    with _gateway_proxy_request(camera_id, "/snapshot.jpg", timeout_seconds=8.0) as response:
        data = response.read()
        content_type = response.headers.get("Content-Type") or "image/jpeg"

    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


def _generate_gateway_live_proxy(camera_id: int):
    response = _gateway_proxy_request(camera_id, "/stream/live", timeout_seconds=8.0)
    try:
        while chunk := response.read(64 * 1024):
            yield chunk
    finally:
        response.close()


@router.get("/cameras/{camera_id}/stream/live")
def monitor_gateway_camera_live(camera_id: int):
    return StreamingResponse(
        _generate_gateway_live_proxy(camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
