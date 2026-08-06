"""Endpoints web independentes para atualizar a fonte RTSP de cameras."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.analytics.camera_profile_models import profile_from_camera
from app.core.url_safety import mask_url_credentials
from app.db.models import Camera, User
from app.services.camera_discovery_cache import get_camera_discovery, store_camera_discovery
from app.services.device_auto_detection import detect_common_video_device
from app.services import hik_sdk_lab_service as sdk_lab
from app.services.camera_source_update_service import (
    CameraSourceNotFoundError,
    PendingCameraSourceUpdate,
    confirm_camera_source_update,
    update_camera_source,
    update_camera_rtsp_source,
)
from app.web.camera_detail_presenter import build_camera_detail_context, build_camera_detail_payload
from app.web.camera_view_models import (
    build_discovered_profile_options,
    build_discovery_context,
    build_nuisance_options,
)
from app.web.infrastructure import get_scoped_db, require_web_auth, templates


router = APIRouter()
_AUTHORIZED_ROLES = ["admin", "supervisor"]
logger = logging.getLogger(__name__)

_SUPPORTED_MANUFACTURERS = {
    "hikvision": "Hikvision",
    "dahua": "Dahua",
    "intelbras": "Intelbras",
    "generic": "Generico",
}
_SDK_DEFAULT_PORTS = {
    "hikvision": 8000,
    "dahua": 37777,
    "intelbras": 80,
}


def _not_found_http(exc: CameraSourceNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _detected_manufacturer(result: dict[str, Any]) -> str:
    recommendation = dict(result.get("recommendation") or {})
    brand = str(recommendation.get("brand") or "").strip().lower()
    return _SUPPORTED_MANUFACTURERS.get(brand, _SUPPORTED_MANUFACTURERS["generic"])


def _probe_sdk_manufacturers(
    *,
    owner_id: int,
    host: str,
    username: str,
    password: str,
    open_ports: list[int],
    onvif_port: int | None,
) -> list[dict[str, Any]]:
    open_port_set = {int(port) for port in open_ports}
    attempts: list[dict[str, Any]] = []
    for brand in ("hikvision", "dahua", "intelbras"):
        port = (
            int(onvif_port)
            if brand == "intelbras" and onvif_port and int(onvif_port) in open_port_set
            else _SDK_DEFAULT_PORTS[brand]
        )
        if not sdk_lab.sdk_available(brand):
            attempts.append(
                {
                    "brand": brand,
                    "port": port,
                    "status": "unavailable",
                    "detail": "SDK nao instalado ou desativado.",
                }
            )
            continue
        if port not in open_port_set:
            attempts.append(
                {
                    "brand": brand,
                    "port": port,
                    "status": "port_closed",
                    "detail": "Porta de controle nao respondeu.",
                }
            )
            continue

        token = ""
        try:
            session = sdk_lab.connect_device(
                owner_id=owner_id,
                label=f"Deteccao {brand} {host}",
                device_type="camera",
                host=host,
                port=port,
                username=username,
                password=password,
                channel=1,
                manufacturer=brand,
                connection_mode="sdk",
            )
            token = str(session.get("token") or "")
            device = dict(session.get("device") or {})
            attempts.append(
                {
                    "brand": brand,
                    "port": port,
                    "status": "confirmed",
                    "detail": "Login confirmado sem executar movimento PTZ.",
                    "serial_number": str(device.get("serial_number") or ""),
                }
            )
        except Exception as exc:
            attempts.append(
                {
                    "brand": brand,
                    "port": port,
                    "status": "failed",
                    "detail": str(exc)[:240] or "O login foi recusado.",
                }
            )
        finally:
            if token:
                try:
                    sdk_lab.disconnect(token, owner_id)
                except Exception:
                    pass
    return attempts


def _merge_sdk_detection(
    result: dict[str, Any],
    sdk_attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    merged = dict(result)
    recommendation = dict(merged.get("recommendation") or {})
    base_brand = str(recommendation.get("brand") or "generic").strip().lower()
    confirmed = [
        str(item.get("brand") or "")
        for item in sdk_attempts
        if item.get("status") == "confirmed"
    ]

    if len(confirmed) == 1:
        brand = confirmed[0]
        recommendation.update(
            {
                "brand": brand,
                "confidence": "high",
                "ready": True,
                "reason": (
                    f"Login confirmado pelo backend {_SUPPORTED_MANUFACTURERS[brand]}; "
                    "nenhum comando de movimento PTZ foi enviado."
                ),
            }
        )
    elif len(confirmed) > 1:
        if base_brand in confirmed:
            recommendation.update(
                {
                    "brand": base_brand,
                    "confidence": "medium",
                    "ready": True,
                    "reason": (
                        f"A identificacao de rede aponta para {_SUPPORTED_MANUFACTURERS[base_brand]}, "
                        f"mas mais de um backend autenticou ({', '.join(confirmed)})."
                    ),
                }
            )
        else:
            recommendation.update(
                {
                    "brand": "generic",
                    "confidence": "low",
                    "ready": True,
                    "reason": (
                        "Mais de um backend autenticou e nao foi possivel distinguir com seguranca: "
                        f"{', '.join(confirmed)}."
                    ),
                }
            )

    merged["recommendation"] = recommendation
    merged["sdk_attempts"] = sdk_attempts
    return merged


@router.post("/cameras/manufacturer/detect")
async def detect_camera_manufacturer_web(
    request: Request,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    form = await request.form()
    host = str(form.get("ip") or "").strip()
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    camera_id_text = str(form.get("camera_id") or "").strip()
    onvif_port_text = str(form.get("onvif_port") or "").strip()
    if not host:
        return JSONResponse({"error": "Informe o IP/host da camera."}, status_code=400)

    db = get_scoped_db()
    try:
        if camera_id_text:
            try:
                camera_id = int(camera_id_text)
            except ValueError:
                return JSONResponse({"error": "Camera invalida."}, status_code=400)
            camera = db.query(Camera).filter(Camera.id == camera_id).first()
            if camera is None:
                return JSONResponse({"error": "Camera nao encontrada."}, status_code=404)
            if not password:
                password = str(camera.password or "")
        try:
            onvif_port = int(onvif_port_text) if onvif_port_text else None
            result = await asyncio.to_thread(
                detect_common_video_device,
                host=host,
                username=username,
                password=password,
                onvif_port=onvif_port,
            )
            sdk_attempts = await asyncio.to_thread(
                _probe_sdk_manufacturers,
                owner_id=int(current_user.id),
                host=host,
                username=username,
                password=password,
                open_ports=list(result.get("open_ports") or []),
                onvif_port=onvif_port,
            )
            result = _merge_sdk_detection(result, sdk_attempts)
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            logger.exception(
                "Automatic camera manufacturer detection failed",
                extra={"camera_id": camera_id_text or None, "host": host},
            )
            return JSONResponse(
                {"error": "Falha ao descobrir automaticamente o fabricante."},
                status_code=500,
            )

        recommendation = dict(result.get("recommendation") or {})
        return JSONResponse(
            {
                "manufacturer": _detected_manufacturer(result),
                "brand": str(recommendation.get("brand") or "generic"),
                "confidence": str(recommendation.get("confidence") or "low"),
                "reason": str(recommendation.get("reason") or ""),
                "model": str(recommendation.get("model") or ""),
                "onvif_port": recommendation.get("onvif_port"),
                "open_ports": result.get("open_ports") or [],
                "sdk_attempts": result.get("sdk_attempts") or [],
            }
        )
    finally:
        db.close()


@router.post("/cameras/{camera_id}/edit")
def update_camera_web(
    request: Request,
    camera_id: int,
    name: str = Form(...),
    ip: str = Form(...),
    manufacturer: str = Form(...),
    model: str = Form(""),
    onvif_port: str = Form(""),
    username: str = Form(...),
    password: str = Form(""),
    rtsp_url: str = Form(""),
    rediscover_rtsp: str = Form("false"),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    del current_user
    db = get_scoped_db()
    try:
        try:
            result = update_camera_source(
                db,
                camera_id=camera_id,
                name=name,
                ip=ip,
                manufacturer=manufacturer,
                model=model,
                onvif_port=onvif_port,
                username=username,
                password=password,
                rtsp_url=rtsp_url,
                rediscover_rtsp=rediscover_rtsp == "true",
            )
        except CameraSourceNotFoundError as exc:
            raise _not_found_http(exc) from exc
        except Exception as exc:
            camera, events = build_camera_detail_payload(db, camera_id)
            return templates.TemplateResponse(
                request=request,
                name="camera_detail.html",
                context=build_camera_detail_context(
                    request=request,
                    camera=camera,
                    events=events,
                    camera_error=f"Falha ao atualizar câmera: {exc}",
                ),
                status_code=400,
            )

        if result.pending is not None:
            pending = result.pending
            profile = profile_from_camera(result.camera)
            profiles = build_discovered_profile_options(pending.name, pending.profiles)
            nuisance_profile = {
                item["key"]: item["enabled"]
                for item in build_nuisance_options(getattr(profile, "nuisance_profile", None))
            }
            credential_token = store_camera_discovery(
                {
                    "camera_id": pending.camera_id,
                    "base_name": pending.name,
                    "ip": pending.ip,
                    "manufacturer": pending.manufacturer,
                    "model": pending.model,
                    "username": pending.username,
                    "password": pending.password,
                    "onvif_port": pending.onvif_port,
                    "profiles": profiles,
                    "camera_family": getattr(profile, "camera_family", "dome"),
                    "scene_category": getattr(profile, "scene_category", "interno"),
                    "target_focus": getattr(profile, "target_focus", "pessoa"),
                    "nuisance_profile": nuisance_profile,
                    "discovery_method": "onvif",
                }
            )
            return templates.TemplateResponse(
                request=request,
                name="camera_discovery_confirm.html",
                context=build_discovery_context(
                    request=request,
                    base_name=pending.name,
                    ip=pending.ip,
                    manufacturer=pending.manufacturer,
                    model=pending.model,
                    username=pending.username,
                    password="",
                    credential_token=credential_token,
                    onvif_port=pending.onvif_port,
                    profiles=profiles,
                    mode="edit",
                    camera_id=pending.camera_id,
                    camera_family=getattr(profile, "camera_family", "dome"),
                    scene_category=getattr(profile, "scene_category", "interno"),
                    target_focus=getattr(profile, "target_focus", "pessoa"),
                    nuisance_profile=nuisance_profile,
                ),
            )

        return RedirectResponse(url=f"/cameras/{camera_id}", status_code=303)
    finally:
        db.close()


@router.post("/cameras/{camera_id}/rtsp-url")
def update_camera_rtsp_url(
    request: Request,
    camera_id: int,
    rtsp_url: str = Form(...),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    del request, current_user
    db = get_scoped_db()
    try:
        try:
            update_camera_rtsp_source(db, camera_id=camera_id, rtsp_url=rtsp_url)
        except CameraSourceNotFoundError as exc:
            raise _not_found_http(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=f"/cameras/{camera_id}", status_code=303)
    finally:
        db.close()


def _legacy_confirmation_payload(form, camera: Camera) -> dict[str, Any]:
    try:
        profile_count = max(0, int(str(form.get("profile_count") or "0").strip()))
    except (TypeError, ValueError):
        profile_count = 0
    try:
        selected_index = int(str(form.get("selected_profile") or "").strip())
    except (TypeError, ValueError):
        selected_index = -1
    profiles: list[dict[str, Any]] = []
    for index in range(profile_count):
        rtsp_url = str(form.get(f"profile_rtsp_url_{index}") or "").strip()
        profiles.append(
            {
                "index": index,
                "token": str(form.get(f"profile_token_{index}") or "").strip(),
                "profile_name": str(form.get(f"profile_label_{index}") or "").strip() or f"Canal {index + 1}",
                "rtsp_url": rtsp_url,
                "masked_rtsp_url": mask_url_credentials(rtsp_url),
                "encoding": str(form.get(f"profile_encoding_{index}") or "").strip(),
                "resolution_label": str(form.get(f"profile_resolution_{index}") or "").strip(),
                "suggested_name": str(form.get(f"profile_name_{index}") or "").strip(),
                "selected": index == selected_index,
            }
        )
    try:
        onvif_port = int(str(form.get("onvif_port") or camera.onvif_port or 80).strip())
    except (TypeError, ValueError):
        onvif_port = camera.onvif_port or 80
    return {
        "camera_id": camera.id,
        "base_name": str(form.get("base_name") or camera.name).strip() or camera.name,
        "ip": str(form.get("ip") or camera.ip).strip() or camera.ip,
        "manufacturer": str(form.get("manufacturer") or camera.manufacturer or "").strip(),
        "model": str(form.get("model") or camera.model or "").strip() or None,
        "username": str(form.get("username") or camera.username).strip() or camera.username,
        "password": str(form.get("password") or camera.password),
        "onvif_port": onvif_port,
        "profiles": profiles,
        "camera_family": str(form.get("camera_family") or "dome"),
        "scene_category": str(form.get("scene_category") or "interno"),
        "target_focus": str(form.get("target_focus") or "pessoa"),
        "nuisance_profile": {},
        "discovery_method": str(form.get("discovery_method") or "onvif"),
    }


def _pending_from_payload(payload: dict[str, Any]) -> PendingCameraSourceUpdate:
    return PendingCameraSourceUpdate(
        camera_id=int(payload["camera_id"]),
        name=str(payload.get("base_name") or "Camera"),
        ip=str(payload.get("ip") or ""),
        manufacturer=str(payload.get("manufacturer") or ""),
        model=str(payload.get("model") or "").strip() or None,
        onvif_port=int(payload.get("onvif_port") or 80),
        username=str(payload.get("username") or ""),
        password=str(payload.get("password") or ""),
        profiles=[],
    )


@router.post("/cameras/{camera_id}/edit/confirm")
async def update_discovered_camera_rtsp(
    request: Request,
    camera_id: int,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    del current_user
    form = await request.form()
    credential_token = str(form.get("credential_token") or "").strip()
    db = get_scoped_db()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if camera is None:
            raise HTTPException(status_code=404, detail="Câmera não encontrada")
        payload = get_camera_discovery(credential_token) if credential_token else _legacy_confirmation_payload(form, camera)
        if payload is None or int(payload.get("camera_id") or 0) != camera_id:
            raise HTTPException(status_code=400, detail="A descoberta expirou. Redescubra os profiles da camera.")

        profiles = [dict(profile) for profile in payload.get("profiles", [])]
        try:
            selected_index = int(str(form.get("selected_profile") or "").strip())
        except (TypeError, ValueError):
            selected_index = -1
        for index, profile in enumerate(profiles):
            profile["selected"] = index == selected_index
        selected_profile = next(
            (profile for profile in profiles if profile.get("selected") and profile.get("rtsp_url")),
            None,
        )
        if selected_profile is None:
            return templates.TemplateResponse(
                request=request,
                name="camera_discovery_confirm.html",
                context=build_discovery_context(
                    request=request,
                    base_name=str(payload.get("base_name") or camera.name),
                    ip=str(payload.get("ip") or camera.ip),
                    manufacturer=str(payload.get("manufacturer") or camera.manufacturer or ""),
                    model=str(payload.get("model") or camera.model or "").strip() or None,
                    username=str(payload.get("username") or camera.username),
                    password="" if credential_token else str(payload.get("password") or camera.password),
                    credential_token=credential_token or None,
                    onvif_port=int(payload.get("onvif_port") or camera.onvif_port or 80),
                    profiles=profiles,
                    mode="edit",
                    camera_id=camera.id,
                    error="Selecione um canal para atualizar a camera.",
                    camera_family=str(payload.get("camera_family") or "dome"),
                    scene_category=str(payload.get("scene_category") or "interno"),
                    target_focus=str(payload.get("target_focus") or "pessoa"),
                    nuisance_profile=dict(payload.get("nuisance_profile") or {}),
                    discovery_method=str(payload.get("discovery_method") or "onvif"),
                ),
                status_code=400,
            )

        try:
            confirm_camera_source_update(
                db,
                pending=_pending_from_payload(payload),
                rtsp_url=str(selected_profile["rtsp_url"]),
            )
        except CameraSourceNotFoundError as exc:
            raise _not_found_http(exc) from exc
        return RedirectResponse(url=f"/cameras/{camera_id}", status_code=303)
    finally:
        db.close()
