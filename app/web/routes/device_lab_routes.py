"""Rotas do laboratorio de dispositivo: prototipo de camera como objeto + PTZ."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Camera, User
from app.services.device_session_service import (
    DeviceCapabilityError,
    DeviceSessionError,
    describe_device,
    invalidate_session,
    ptz_continuous_move,
    ptz_goto_preset,
    ptz_set_preset,
    ptz_stop,
)
from app.services import hik_sdk_lab_service as sdk_lab
from app.web.infrastructure import get_scoped_db, require_web_auth, templates


router = APIRouter()

LAB_ROLES = ["admin", "supervisor", "dev"]
_camera_sdk_tokens: dict[int, str] = {}


class PtzMovePayload(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    tilt: float = Field(default=0.0, ge=-1.0, le=1.0)
    zoom: float = Field(default=0.0, ge=-1.0, le=1.0)


class PresetGotoPayload(BaseModel):
    preset_token: str = Field(min_length=1, max_length=200)


class PresetSavePayload(BaseModel):
    name: str = Field(default="", max_length=120)



def _load_camera(camera_id: int) -> Camera | None:
    db = get_scoped_db()
    try:
        return (
            db.query(Camera)
            .filter(Camera.id == camera_id, Camera.is_deleted == False)  # noqa: E712
            .first()
        )
    finally:
        db.close()


def _device_error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, DeviceCapabilityError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if isinstance(exc, DeviceSessionError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    return JSONResponse({"ok": False, "error": f"Erro inesperado: {exc}"}, status_code=500)


@router.get("/lab/dispositivo")
def device_lab_page(
    request: Request,
    current_user: User = Depends(require_web_auth(LAB_ROLES)),
):
    db = get_scoped_db()
    try:
        cameras = (
            db.query(Camera)
            .filter(Camera.is_deleted == False)  # noqa: E712
            .order_by(Camera.name.asc())
            .all()
        )
        cameras_payload = [
            {
                "id": camera.id,
                "name": camera.name,
                "ip": camera.ip,
                "manufacturer": camera.manufacturer,
                "model": camera.model,
                "onvif_port": camera.onvif_port,
                "status": camera.status,
                "source_type": camera.source_type or "camera",
                "source_brand": camera.source_brand,
            }
            for camera in cameras
        ]
    finally:
        db.close()

    return templates.TemplateResponse(
        request=request,
        name="device_lab.html",
        context={
            "request": request,
            "title": "Laboratório de Dispositivo",
            "cameras_payload": cameras_payload,
        },
    )


@router.get("/lab/dispositivo/{camera_id}/inspecao")
def device_lab_inspect(
    camera_id: int,
    mode: str = "onvif",
    current_user: User = Depends(require_web_auth(LAB_ROLES)),
):
    camera = _load_camera(camera_id)
    if camera is None:
        return JSONResponse({"ok": False, "error": "Camera nao encontrada."}, status_code=404)

    if mode == "sdk":
        brand = (camera.source_brand or camera.manufacturer or "hikvision").lower()
        if "hik" in brand:
            manufacturer = "hikvision"
            port = 8000
        elif "dahua" in brand:
            manufacturer = "dahua"
            port = 37777
        elif "intelbras" in brand:
            manufacturer = "intelbras"
            port = int(camera.onvif_port or 80)
        else:
            manufacturer = "hikvision"
            port = 8000

        if not sdk_lab.sdk_available(manufacturer):
            label = "Dahua NetSDK" if manufacturer == "dahua" else "HCNetSDK" if manufacturer == "hikvision" else "API HTTP Intelbras"
            return JSONResponse({
                "ok": False,
                "error": f"O SDK proprietário para {label} não está habilitado/instalado neste servidor."
            }, status_code=400)

        rtsp_port = 554
        if camera.rtsp_url:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(camera.rtsp_url)
                if parsed.port:
                    rtsp_port = parsed.port
            except Exception:
                pass

        channel = camera.source_channel or 1
        stream_kind = camera.source_stream_kind or "sub"
        if stream_kind not in {"main", "sub"}:
            stream_kind = "sub"

        try:
            session_payload = sdk_lab.connect_device(
                owner_id=int(current_user.id),
                label=camera.name,
                device_type=camera.source_type or "camera",
                host=camera.ip,
                port=port,
                username=camera.username,
                password=camera.password,
                channel=channel,
                manufacturer=manufacturer,
                rtsp_port=rtsp_port,
                stream_kind=stream_kind,
            )
            _camera_sdk_tokens[camera_id] = session_payload["token"]
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"Erro de conexão SDK: {exc}"}, status_code=502)

        presets = []
        try:
            presets = sdk_lab.list_presets(session_payload["token"], int(current_user.id))
        except Exception:
            presets = []

        mapped_payload = {
            "device": {
                "manufacturer": manufacturer.capitalize(),
                "model": session_payload["device"].get("model") or camera.model or "—",
                "firmware_version": session_payload["device"].get("firmware_version") or "—",
                "serial_number": session_payload["device"].get("serial_number") or "—",
            },
            "capabilities": {
                "ptz": True,
                "events": False,
                "analytics": False,
                "imaging": False,
                "media": True,
            },
            "profiles": [
                {
                    "name": f"SDK Stream (Canal {channel})",
                    "token": "sdk_stream",
                    "ptz": True,
                }
            ],
            "profile_token": "sdk_stream",
            "presets": presets,
            "endpoint": f"{session_payload['host']}:{session_payload['port']} (SDK {manufacturer.upper()})",
            "session_age_seconds": session_payload.get("expires_in_seconds", 0),
        }
        return JSONResponse({"ok": True, **mapped_payload})

    try:
        payload = describe_device(camera)
    except Exception as exc:
        return _device_error_response(exc)
    return JSONResponse({"ok": True, **payload})


@router.post("/lab/dispositivo/{camera_id}/ptz/mover")
def device_lab_ptz_move(
    camera_id: int,
    payload: PtzMovePayload,
    mode: str = "onvif",
    current_user: User = Depends(require_web_auth(LAB_ROLES)),
):
    if mode == "sdk":
        token = _camera_sdk_tokens.get(camera_id)
        if not token:
            return JSONResponse({"ok": False, "error": "Sessão SDK não iniciada ou expirada."}, status_code=400)
        try:
            pan = 1 if payload.pan > 0.05 else -1 if payload.pan < -0.05 else 0
            tilt = 1 if payload.tilt > 0.05 else -1 if payload.tilt < -0.05 else 0
            zoom = 1 if payload.zoom > 0.05 else -1 if payload.zoom < -0.05 else 0

            speed_val = max(abs(payload.pan), abs(payload.tilt), abs(payload.zoom))
            sdk_speed = max(1, min(7, int(speed_val * 7))) if speed_val > 0 else 4

            continuous = sdk_lab.ptz_hold_start(
                token, int(current_user.id), pan=pan, tilt=tilt, zoom=zoom, speed=sdk_speed,
            )
            if not continuous:
                sdk_lab.move_ptz(
                    token,
                    int(current_user.id),
                    pan=pan,
                    tilt=tilt,
                    zoom=zoom,
                    speed=sdk_speed,
                    duration_ms=400,
                )
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
        return JSONResponse({"ok": True, "continuous": continuous})

    camera = _load_camera(camera_id)
    if camera is None:
        return JSONResponse({"ok": False, "error": "Camera nao encontrada."}, status_code=404)
    try:
        ptz_continuous_move(camera, pan=payload.pan, tilt=payload.tilt, zoom=payload.zoom)
    except Exception as exc:
        return _device_error_response(exc)
    return JSONResponse({"ok": True})


@router.post("/lab/dispositivo/{camera_id}/ptz/parar")
def device_lab_ptz_stop(
    camera_id: int,
    mode: str = "onvif",
    current_user: User = Depends(require_web_auth(LAB_ROLES)),
):
    if mode == "sdk":
        token = _camera_sdk_tokens.get(camera_id)
        if token:
            try:
                sdk_lab.ptz_hold_stop(token, int(current_user.id))
            except Exception:
                pass
        return JSONResponse({"ok": True})

    camera = _load_camera(camera_id)
    if camera is None:
        return JSONResponse({"ok": False, "error": "Camera nao encontrada."}, status_code=404)
    try:
        ptz_stop(camera)
    except Exception as exc:
        return _device_error_response(exc)
    return JSONResponse({"ok": True})


@router.post("/lab/dispositivo/{camera_id}/ptz/preset/ir")
def device_lab_ptz_goto_preset(
    camera_id: int,
    payload: PresetGotoPayload,
    mode: str = "onvif",
    current_user: User = Depends(require_web_auth(LAB_ROLES)),
):
    if mode == "sdk":
        token = _camera_sdk_tokens.get(camera_id)
        if not token:
            return JSONResponse({"ok": False, "error": "Sessão SDK não iniciada ou expirada."}, status_code=400)
        try:
            sdk_lab.goto_preset(token, int(current_user.id), payload.preset_token)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
        return JSONResponse({"ok": True})

    camera = _load_camera(camera_id)
    if camera is None:
        return JSONResponse({"ok": False, "error": "Camera nao encontrada."}, status_code=404)
    try:
        ptz_goto_preset(camera, payload.preset_token)
    except Exception as exc:
        return _device_error_response(exc)
    return JSONResponse({"ok": True})


@router.post("/lab/dispositivo/{camera_id}/ptz/preset/salvar")
def device_lab_ptz_save_preset(
    camera_id: int,
    payload: PresetSavePayload,
    mode: str = "onvif",
    current_user: User = Depends(require_web_auth(LAB_ROLES)),
):
    if mode == "sdk":
        token = _camera_sdk_tokens.get(camera_id)
        if not token:
            return JSONResponse({"ok": False, "error": "Sessão SDK não iniciada ou expirada."}, status_code=400)
        try:
            session = sdk_lab.get_session(token, int(current_user.id))
            ptz, profile_token = sdk_lab._onvif_ptz_for_session(session)
            res_token = ptz.SetPreset({
                "ProfileToken": profile_token,
                "PresetName": str(payload.name).strip() or None,
            })
            return JSONResponse({"ok": True, "token": str(res_token)})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"Erro ao salvar preset via ONVIF (sessão SDK): {exc}"}, status_code=502)

    camera = _load_camera(camera_id)
    if camera is None:
        return JSONResponse({"ok": False, "error": "Camera nao encontrada."}, status_code=404)
    try:
        result = ptz_set_preset(camera, payload.name)
    except Exception as exc:
        return _device_error_response(exc)
    return JSONResponse({"ok": True, **result})


@router.post("/lab/dispositivo/{camera_id}/sessao/reset")
def device_lab_reset_session(
    camera_id: int,
    mode: str = "onvif",
    current_user: User = Depends(require_web_auth(LAB_ROLES)),
):
    if mode == "sdk":
        token = _camera_sdk_tokens.pop(camera_id, None)
        if token:
            try:
                sdk_lab.disconnect(token, int(current_user.id))
            except Exception:
                pass
    else:
        invalidate_session(camera_id)
    return JSONResponse({"ok": True})


@router.get("/lab/dispositivo/{camera_id}/snapshot")
def device_lab_snapshot(
    camera_id: int,
    mode: str = "onvif",
    current_user: User = Depends(require_web_auth(LAB_ROLES)),
):
    if mode == "sdk":
        token = _camera_sdk_tokens.get(camera_id)
        if not token:
            return JSONResponse({"ok": False, "error": "Sessão SDK não iniciada."}, status_code=400)
        try:
            from fastapi.responses import Response
            frame = sdk_lab.capture_snapshot(token, int(current_user.id))
            return Response(content=frame, media_type="image/jpeg")
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"Erro de snapshot SDK: {exc}"}, status_code=502)

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/cameras/{camera_id}/snapshot")
