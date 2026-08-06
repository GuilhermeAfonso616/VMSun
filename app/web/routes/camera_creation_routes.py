"""Rotas web de cadastro e descoberta individual de cameras."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.analytics.camera_profile_models import CAMERA_FAMILIES, SCENE_CATEGORIES, TARGET_FOCUSES
from app.camera.rtsp_discovery import build_common_rtsp_candidates, probe_rtsp_candidates
from app.core.logging import log_ignored_exception
from app.core.url_safety import mask_url_credentials
from app.db.models import User
from app.services.audit_service import log_audit
from app.services.camera_discovery_cache import get_camera_discovery, store_camera_discovery
from app.services.camera_discovery_service import discover_camera_streams
from app.services.camera_source_service import CameraSourceSpec, create_camera_sources
from app.web.camera_view_models import (
    build_camera_new_context,
    build_discovered_profile_options,
    build_discovery_context,
)
from app.web.infrastructure import get_scoped_db, require_web_auth, templates


router = APIRouter()
_AUTHORIZED_ROLES = ["admin", "supervisor"]
_NUISANCE_KEYS = (
    "vegetation_wind",
    "rain",
    "headlights",
    "insects_ir",
    "strong_shadows",
    "glass_reflection",
    "camera_vibration",
    "low_texture_scene",
    "crowd_occlusion",
    "fog_or_haze",
)


def _as_bool(value: str | None) -> bool:
    return value is not None and str(value).lower() in {"1", "true", "on", "yes", "sim"}


def _normalize_profile(camera_family: str, scene_category: str, target_focus: str) -> tuple[str, str, str]:
    return (
        camera_family if camera_family in CAMERA_FAMILIES else "dome",
        scene_category if scene_category in SCENE_CATEGORIES else "interno",
        target_focus if target_focus in TARGET_FOCUSES else "pessoa",
    )


def _nuisance_profile(**values) -> dict[str, bool]:
    return {key: _as_bool(values.get(key)) for key in _NUISANCE_KEYS}


def _render_new_camera(request: Request, *, status_code: int = 200, **context_values):
    return templates.TemplateResponse(
        request=request,
        name="camera_new.html",
        context=build_camera_new_context(request=request, **context_values),
        status_code=status_code,
    )


def _audit_camera_creation(db, request: Request, current_user: User, *, name: str, ip: str) -> None:
    try:
        log_audit(
            db,
            "camera_create",
            current_user,
            f"Criou câmera: {name} ({ip})",
            ip_address=request.client.host if request.client else None,
        )
    except Exception:
        log_ignored_exception("audit.camera_create", level=logging.WARNING)


@router.get("/cameras/new")
def new_camera_page(
    request: Request,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    return _render_new_camera(
        request,
        form_values={
            "name": str(request.query_params.get("name") or "").strip(),
            "ip": str(request.query_params.get("ip") or "").strip(),
            "onvif_port": str(request.query_params.get("onvif_port") or "").strip(),
            "manufacturer": str(request.query_params.get("manufacturer") or "").strip(),
            "model": str(request.query_params.get("model") or "").strip(),
        },
    )


@router.post("/cameras/new")
def create_camera_web(
    request: Request,
    name: str = Form(...),
    ip: str = Form(...),
    manufacturer: str = Form(...),
    model: str = Form(""),
    onvif_port: str = Form(""),
    username: str = Form(...),
    password: str = Form(...),
    rtsp_url: str = Form(""),
    camera_family: str = Form("dome"),
    scene_category: str = Form("interno"),
    target_focus: str = Form("pessoa"),
    vegetation_wind: str | None = Form(None),
    rain: str | None = Form(None),
    headlights: str | None = Form(None),
    insects_ir: str | None = Form(None),
    strong_shadows: str | None = Form(None),
    glass_reflection: str | None = Form(None),
    camera_vibration: str | None = Form(None),
    low_texture_scene: str | None = Form(None),
    crowd_occlusion: str | None = Form(None),
    fog_or_haze: str | None = Form(None),
    processing_max_width: str = Form(""),
    processing_max_height: str = Form(""),
    processing_upscale_small_frames: str | None = Form(None),
    normal_inference_interval_seconds: str = Form(""),
    capture_drop_frames: str = Form(""),
    visual_raw_publish_interval_seconds: str = Form(""),
    visual_processed_publish_interval_seconds: str = Form(""),
    prefer_motion_test: str | None = Form(None),
    allow_rtsp_fallback: str | None = Form("true"),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    del (
        processing_max_width,
        processing_max_height,
        processing_upscale_small_frames,
        normal_inference_interval_seconds,
        capture_drop_frames,
        visual_raw_publish_interval_seconds,
        visual_processed_publish_interval_seconds,
        prefer_motion_test,
    )
    selected_family, selected_scene, selected_focus = _normalize_profile(
        camera_family,
        scene_category,
        target_focus,
    )
    nuisance_profile = _nuisance_profile(
        vegetation_wind=vegetation_wind,
        rain=rain,
        headlights=headlights,
        insects_ir=insects_ir,
        strong_shadows=strong_shadows,
        glass_reflection=glass_reflection,
        camera_vibration=camera_vibration,
        low_texture_scene=low_texture_scene,
        crowd_occlusion=crowd_occlusion,
        fog_or_haze=fog_or_haze,
    )
    clean_name = name.strip()
    clean_ip = ip.strip()
    clean_manufacturer = manufacturer.strip()
    clean_model = model.strip() or None
    clean_username = username.strip()
    clean_rtsp_url = str(rtsp_url or "").strip()
    safe_form_values = {
        "name": name,
        "ip": ip,
        "manufacturer": manufacturer,
        "model": model,
        "onvif_port": onvif_port,
        "username": username,
        "rtsp_url": rtsp_url,
        "allow_rtsp_fallback": _as_bool(allow_rtsp_fallback),
    }

    db = get_scoped_db()
    try:
        if not clean_manufacturer:
            return _render_new_camera(
                request,
                error="A marca da camera e obrigatoria.",
                form_values=safe_form_values,
                camera_family=selected_family,
                scene_category=selected_scene,
                target_focus=selected_focus,
                nuisance_profile=nuisance_profile,
                status_code=400,
            )
        try:
            normalized_port = int(onvif_port.strip()) if onvif_port.strip() else None
            if clean_rtsp_url:
                cameras = create_camera_sources(
                    db,
                    ip=clean_ip,
                    manufacturer=clean_manufacturer,
                    model=clean_model,
                    onvif_port=normalized_port if normalized_port is not None else 80,
                    username=clean_username,
                    password=password,
                    sources=[CameraSourceSpec(name=clean_name, rtsp_url=clean_rtsp_url)],
                    camera_family=selected_family,
                    scene_category=selected_scene,
                    target_focus=selected_focus,
                    nuisance_profile=nuisance_profile,
                    analytics_profile=None,
                    coordinate_space_override="display",
                )
                _audit_camera_creation(db, request, current_user, name=cameras[0].name, ip=clean_ip)
                return RedirectResponse(url="/cameras", status_code=303)

            discovery = discover_camera_streams(
                ip=clean_ip,
                onvif_port=normalized_port,
                username=clean_username,
                password=password,
                allow_rtsp_fallback=_as_bool(allow_rtsp_fallback),
            )
        except Exception as exc:
            db.rollback()
            return _render_new_camera(
                request,
                error=f"Falha ao descobrir RTSP: {exc}",
                form_values=safe_form_values,
                camera_family=selected_family,
                scene_category=selected_scene,
                target_focus=selected_focus,
                nuisance_profile=nuisance_profile,
                status_code=400,
            )

        if len(discovery.profiles) > 1:
            profiles = build_discovered_profile_options(clean_name, discovery.profiles)
            credential_token = store_camera_discovery(
                {
                    "base_name": clean_name,
                    "ip": clean_ip,
                    "manufacturer": clean_manufacturer,
                    "model": clean_model,
                    "username": clean_username,
                    "password": password,
                    "onvif_port": discovery.onvif_port,
                    "profiles": profiles,
                    "camera_family": selected_family,
                    "scene_category": selected_scene,
                    "target_focus": selected_focus,
                    "nuisance_profile": nuisance_profile,
                    "discovery_method": getattr(discovery, "method", "onvif"),
                }
            )
            return templates.TemplateResponse(
                request=request,
                name="camera_discovery_confirm.html",
                context=build_discovery_context(
                    request=request,
                    base_name=clean_name,
                    ip=clean_ip,
                    manufacturer=clean_manufacturer,
                    model=clean_model,
                    username=clean_username,
                    password="",
                    credential_token=credential_token,
                    onvif_port=discovery.onvif_port,
                    profiles=profiles,
                    mode="new",
                    camera_family=selected_family,
                    scene_category=selected_scene,
                    target_focus=selected_focus,
                    nuisance_profile=nuisance_profile,
                    discovery_method=getattr(discovery, "method", "onvif"),
                ),
            )

        cameras = create_camera_sources(
            db,
            ip=clean_ip,
            manufacturer=clean_manufacturer,
            model=clean_model,
            onvif_port=discovery.onvif_port,
            username=clean_username,
            password=password,
            sources=[CameraSourceSpec(name=clean_name, rtsp_url=discovery.rtsp_url)],
            camera_family=selected_family,
            scene_category=selected_scene,
            target_focus=selected_focus,
            nuisance_profile=nuisance_profile,
            analytics_profile=None,
            coordinate_space_override="display",
        )
        _audit_camera_creation(db, request, current_user, name=cameras[0].name, ip=clean_ip)
        return RedirectResponse(url="/cameras", status_code=303)
    finally:
        db.close()


def _legacy_confirmation_payload(form) -> dict[str, Any]:
    try:
        onvif_port = int(str(form.get("onvif_port") or "").strip())
    except (TypeError, ValueError):
        onvif_port = 80
    try:
        profile_count = max(0, int(str(form.get("profile_count") or "0").strip()))
    except (TypeError, ValueError):
        profile_count = 0
    profiles: list[dict[str, Any]] = []
    for index in range(profile_count):
        rtsp_url = str(form.get(f"profile_rtsp_url_{index}") or "").strip()
        profiles.append(
            {
                "index": index,
                "token": str(form.get(f"profile_token_{index}") or "").strip(),
                "profile_name": str(form.get(f"profile_label_{index}") or "").strip() or f"Canal {index + 1}",
                "rtsp_url": rtsp_url,
                "masked_rtsp_url": str(form.get(f"profile_masked_rtsp_url_{index}") or "").strip()
                or mask_url_credentials(rtsp_url),
                "encoding": str(form.get(f"profile_encoding_{index}") or "").strip(),
                "resolution_label": str(form.get(f"profile_resolution_{index}") or "").strip(),
                "suggested_name": str(form.get(f"profile_name_{index}") or "").strip(),
                "selected": str(form.get(f"profile_enabled_{index}") or "") == "true",
            }
        )
    family, scene, focus = _normalize_profile(
        str(form.get("camera_family") or "dome").strip().lower(),
        str(form.get("scene_category") or "interno").strip().lower(),
        str(form.get("target_focus") or "pessoa").strip().lower(),
    )
    return {
        "base_name": str(form.get("base_name") or "").strip(),
        "ip": str(form.get("ip") or "").strip(),
        "manufacturer": str(form.get("manufacturer") or "").strip(),
        "model": str(form.get("model") or "").strip() or None,
        "username": str(form.get("username") or "").strip(),
        "password": str(form.get("password") or ""),
        "onvif_port": onvif_port,
        "profiles": profiles,
        "camera_family": family,
        "scene_category": scene,
        "target_focus": focus,
        "nuisance_profile": {key: str(form.get(key) or "") == "true" for key in _NUISANCE_KEYS},
        "discovery_method": str(form.get("discovery_method") or "onvif"),
    }


@router.post("/cameras/new/confirm")
async def create_discovered_camera_channels(
    request: Request,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    form = await request.form()
    credential_token = str(form.get("credential_token") or "").strip()
    payload = get_camera_discovery(credential_token) if credential_token else _legacy_confirmation_payload(form)
    if payload is None:
        return _render_new_camera(
            request,
            error="A descoberta expirou. Pesquise a camera novamente.",
            status_code=400,
        )

    profiles = [dict(profile) for profile in payload.get("profiles", [])]
    for index, profile in enumerate(profiles):
        profile["selected"] = str(form.get(f"profile_enabled_{index}") or "") == "true"
        submitted_name = str(form.get(f"profile_name_{index}") or "").strip()
        if submitted_name:
            profile["suggested_name"] = submitted_name
    selected_profiles = [profile for profile in profiles if profile.get("selected") and profile.get("rtsp_url")]
    if not selected_profiles:
        return templates.TemplateResponse(
            request=request,
            name="camera_discovery_confirm.html",
            context=build_discovery_context(
                request=request,
                base_name=str(payload.get("base_name") or ""),
                ip=str(payload.get("ip") or ""),
                manufacturer=str(payload.get("manufacturer") or ""),
                model=str(payload.get("model") or "").strip() or None,
                username=str(payload.get("username") or ""),
                password="" if credential_token else str(payload.get("password") or ""),
                credential_token=credential_token or None,
                onvif_port=int(payload.get("onvif_port") or 80),
                profiles=profiles,
                mode="new",
                error="Selecione pelo menos um canal para adicionar.",
                camera_family=str(payload.get("camera_family") or "dome"),
                scene_category=str(payload.get("scene_category") or "interno"),
                target_focus=str(payload.get("target_focus") or "pessoa"),
                nuisance_profile=dict(payload.get("nuisance_profile") or {}),
                discovery_method=str(payload.get("discovery_method") or "onvif"),
            ),
            status_code=400,
        )

    base_name = str(payload.get("base_name") or "").strip()
    db = get_scoped_db()
    try:
        cameras = create_camera_sources(
            db,
            ip=str(payload.get("ip") or "").strip(),
            manufacturer=str(payload.get("manufacturer") or "").strip(),
            model=str(payload.get("model") or "").strip() or None,
            onvif_port=int(payload.get("onvif_port") or 80),
            username=str(payload.get("username") or "").strip(),
            password=str(payload.get("password") or ""),
            sources=[
                CameraSourceSpec(
                    name=str(profile.get("suggested_name") or "").strip()
                    or f"{base_name or 'Camera'} - {profile.get('profile_name') or 'Canal'}",
                    rtsp_url=str(profile.get("rtsp_url") or ""),
                )
                for profile in selected_profiles
            ],
            camera_family=str(payload.get("camera_family") or "dome"),
            scene_category=str(payload.get("scene_category") or "interno"),
            target_focus=str(payload.get("target_focus") or "pessoa"),
            nuisance_profile=dict(payload.get("nuisance_profile") or {}),
            analytics_profile=None,
            coordinate_space_override="display",
        )
        _audit_camera_creation(
            db,
            request,
            current_user,
            name=f"{len(cameras)} canal(is) de {base_name or 'Camera'}",
            ip=str(payload.get("ip") or ""),
        )
        return RedirectResponse(url="/cameras", status_code=303)
    finally:
        db.close()


@router.post("/cameras/rtsp-test")
def test_rtsp_candidates_new(
    request: Request,
    ip: str = Form(...),
    manufacturer: str = Form(""),
    model: str = Form(""),
    username: str = Form(...),
    password: str = Form(...),
    camera_family: str = Form("dome"),
    scene_category: str = Form("interno"),
    target_focus: str = Form("pessoa"),
    vegetation_wind: str | None = Form(None),
    rain: str | None = Form(None),
    headlights: str | None = Form(None),
    insects_ir: str | None = Form(None),
    strong_shadows: str | None = Form(None),
    glass_reflection: str | None = Form(None),
    camera_vibration: str | None = Form(None),
    low_texture_scene: str | None = Form(None),
    crowd_occlusion: str | None = Form(None),
    fog_or_haze: str | None = Form(None),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    results = probe_rtsp_candidates(
        build_common_rtsp_candidates(ip=ip.strip(), username=username.strip(), password=password)
    )
    safe_results = [
        {
            **item,
            "url": mask_url_credentials(str(item.get("url") or "")),
            "masked_url": str(item.get("masked_url") or "")
            or mask_url_credentials(str(item.get("url") or "")),
        }
        for item in results
    ]
    selected_family, selected_scene, selected_focus = _normalize_profile(
        camera_family,
        scene_category,
        target_focus,
    )
    nuisance_profile = _nuisance_profile(
        vegetation_wind=vegetation_wind,
        rain=rain,
        headlights=headlights,
        insects_ir=insects_ir,
        strong_shadows=strong_shadows,
        glass_reflection=glass_reflection,
        camera_vibration=camera_vibration,
        low_texture_scene=low_texture_scene,
        crowd_occlusion=crowd_occlusion,
        fog_or_haze=fog_or_haze,
    )
    return _render_new_camera(
        request,
        rtsp_test_source={"ip": ip.strip(), "username": username.strip()},
        rtsp_test_results=safe_results,
        camera_family=selected_family,
        scene_category=selected_scene,
        target_focus=selected_focus,
        nuisance_profile=nuisance_profile,
        form_values={
            "ip": ip,
            "manufacturer": manufacturer,
            "model": model,
            "username": username,
            "allow_rtsp_fallback": True,
        },
    )
