"""Laboratório administrativo para conexão direta ao HCNetSDK."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db.models import User
from app.services import hik_sdk_lab_service as sdk_lab
from app.services import sdk_package_service as sdk_packages
from app.services.webrtc_gateway_client import webrtc_public_base_url
from app.web.infrastructure import require_web_auth, templates

router = APIRouter(prefix="/admin/teste-sdk")
_ROLES = ["admin"]


class ConnectPayload(BaseModel):
    label: str = Field(default="", max_length=80)
    device_type: str
    manufacturer: str = "hikvision"
    host: str = Field(max_length=64)
    port: int = 8000
    username: str = Field(max_length=64)
    password: str = Field(min_length=1, max_length=128)
    channel: int = 1
    rtsp_port: int = 554
    stream_kind: str = "sub"
    connection_mode: str = "sdk"


class PtzPayload(BaseModel):
    pan: int = 0
    tilt: int = 0
    zoom: int = 0
    speed: int = 4
    duration_ms: int = 300


class Ptz3dPayload(BaseModel):
    x_start: int = Field(ge=0, le=255)
    y_start: int = Field(ge=0, le=255)
    x_end: int = Field(ge=0, le=255)
    y_end: int = Field(ge=0, le=255)


class PresetGotoPayload(BaseModel):
    preset_token: str = Field(min_length=1, max_length=200)


def _error(exc: Exception, status_code: int = 400) -> JSONResponse:
    if isinstance(exc, sdk_lab.HikSdkLabError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)
    return JSONResponse({"ok": False, "error": "Falha inesperada no laboratório SDK."}, status_code=500)


@router.get("")
def sdk_test_page(request: Request, current_user: User = Depends(require_web_auth(_ROLES))):
    sdk_lab.cleanup_orphaned_streams()
    return templates.TemplateResponse(
        request=request, name="sdk_test.html",
        context={"request": request, "title": "Teste SDK", "sdk_available": sdk_lab.sdk_available(),
                 "sdk_availability": sdk_lab.sdk_availability(),
                 "sdk_packages": sdk_packages.package_status(),
                 "public_ips_allowed": sdk_lab.public_ips_allowed(),
                 "webrtc_public_base_url": webrtc_public_base_url(),
                 "sessions": sdk_lab.list_sessions(int(current_user.id))},
    )


@router.post("/instalar")
def sdk_test_install(
    manufacturer: str = Form(...),
    archive: UploadFile = File(...),
    _current_user: User = Depends(require_web_auth(_ROLES)),
):
    try:
        installed = sdk_packages.install_archive(
            manufacturer=manufacturer,
            filename=archive.filename or "sdk-package",
            stream=archive.file,
        )
    except sdk_packages.SdkPackageError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "Falha inesperada ao instalar o pacote SDK."},
            status_code=500,
        )
    finally:
        archive.file.close()
    return {"ok": True, "package": installed}


@router.get("/sessoes")
def sdk_test_sessions(current_user: User = Depends(require_web_auth(_ROLES))):
    return {"ok": True, "sessions": sdk_lab.list_sessions(int(current_user.id))}


@router.post("/sessoes")
def sdk_test_connect(payload: ConnectPayload, current_user: User = Depends(require_web_auth(_ROLES))):
    try:
        session = sdk_lab.connect_device(owner_id=int(current_user.id), **payload.model_dump())
    except Exception as exc:
        return _error(exc)
    return {"ok": True, "session": session}


@router.delete("/sessoes/{token}")
def sdk_test_disconnect(token: str, current_user: User = Depends(require_web_auth(_ROLES))):
    try:
        sdk_lab.disconnect(token, int(current_user.id))
    except Exception as exc:
        return _error(exc, 404)
    return {"ok": True}


@router.post("/sessoes/{token}/ptz")
def sdk_test_ptz(token: str, payload: PtzPayload,
                 current_user: User = Depends(require_web_auth(_ROLES))):
    try:
        continuous = sdk_lab.ptz_hold_start(
            token, int(current_user.id),
            pan=payload.pan, tilt=payload.tilt, zoom=payload.zoom, speed=payload.speed,
        )
        if not continuous:
            sdk_lab.move_ptz(token, int(current_user.id), **payload.model_dump())
    except Exception as exc:
        return _error(exc, 502)
    return {"ok": True, "continuous": continuous}


@router.post("/sessoes/{token}/ptz/parar")
def sdk_test_ptz_stop(token: str, current_user: User = Depends(require_web_auth(_ROLES))):
    try:
        sdk_lab.ptz_hold_stop(token, int(current_user.id))
    except Exception as exc:
        return _error(exc, 502)
    return {"ok": True}


@router.post("/sessoes/{token}/ptz/3d")
def sdk_test_ptz_3d(
    token: str,
    payload: Ptz3dPayload,
    current_user: User = Depends(require_web_auth(_ROLES)),
):
    try:
        sdk_lab.position_ptz_3d(
            token,
            int(current_user.id),
            **payload.model_dump(),
        )
    except Exception as exc:
        return _error(exc, 502)
    return {"ok": True}


@router.get("/sessoes/{token}/presets")
def sdk_test_presets(token: str, current_user: User = Depends(require_web_auth(_ROLES))):
    try:
        presets = sdk_lab.list_presets(token, int(current_user.id))
    except Exception as exc:
        return _error(exc, 502)
    return {"ok": True, "presets": presets}


@router.post("/sessoes/{token}/presets/goto")
def sdk_test_goto_preset(token: str, payload: PresetGotoPayload,
                         current_user: User = Depends(require_web_auth(_ROLES))):
    try:
        sdk_lab.goto_preset(token, int(current_user.id), payload.preset_token)
    except Exception as exc:
        return _error(exc, 502)
    return {"ok": True}


@router.post("/sessoes/{token}/stream")
def sdk_test_stream(token: str, current_user: User = Depends(require_web_auth(_ROLES))):
    try:
        stream = sdk_lab.start_stream(token, int(current_user.id))
    except Exception as exc:
        return _error(exc, 502)
    return {"ok": True, "stream": stream}


@router.get("/sessoes/{token}/dimensoes")
def sdk_test_dimensions(token: str, current_user: User = Depends(require_web_auth(_ROLES))):
    """Largura/altura reais do stream para o overlay 3D casar a area de desenho
    com a imagem (letterbox). Nao expoe o frame: so as dimensoes saem daqui."""
    try:
        width, height = sdk_lab.stream_dimensions(token, int(current_user.id))
    except Exception as exc:
        return _error(exc, 502)
    return {"ok": True, "width": width, "height": height}


@router.post("/sessoes/{token}/audio/analise")
def sdk_test_audio_probe(token: str, current_user: User = Depends(require_web_auth(_ROLES))):
    try:
        audio = sdk_lab.probe_audio(token, int(current_user.id))
    except Exception as exc:
        return _error(exc, 502)
    return {"ok": True, "audio": audio}
