"""Paginas e acoes web para descoberta e cadastro de canais NVR."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.logging import get_logger, log_ignored_exception
from app.db.models import User
from app.services.audit_service import log_audit
from app.services.device_auto_detection import detect_common_video_device
from app.services.nvr_channel_service import NvrChannelSpec, create_nvr_channel_sources, normalize_source_stream_kind
from app.services.nvr_discovery_cache import get_nvr_discovery_cache, store_nvr_discovery_cache
from app.services.nvr_discovery_jobs import nvr_discovery_job_manager
from app.services.nvr_discovery_service import discover_nvr_channels
from app.web.infrastructure import get_scoped_db, require_web_auth, templates
from app.web.nvr_view_models import build_nvr_brand_options, build_nvr_channel_health, build_nvr_profile_rows


router = APIRouter()
logger = get_logger("app.web.nvr")
_AUTHORIZED_ROLES = ["admin", "supervisor"]


def _as_bool(value: str | None) -> bool:
    return value is not None and str(value).lower() in {"1", "true", "on", "yes", "sim"}


def _parse_nvr_discovery_form(form) -> dict[str, Any]:
    stream_kinds = [normalize_source_stream_kind(value) for value in form.getlist("stream_kinds")]
    if not stream_kinds:
        stream_kinds = ["main", "sub"]
    try:
        rtsp_port = int(str(form.get("rtsp_port") or "554").strip())
    except (TypeError, ValueError):
        rtsp_port = 554
    try:
        onvif_port = int(str(form.get("onvif_port") or "80").strip())
    except (TypeError, ValueError):
        onvif_port = 80
    try:
        channel_count = int(str(form.get("channel_count") or "16").strip())
    except (TypeError, ValueError):
        channel_count = 16
    return {
        "base_name": str(form.get("base_name") or "NVR").strip() or "NVR",
        "host": str(form.get("host") or "").strip(),
        "username": str(form.get("username") or "").strip(),
        "password": str(form.get("password") or ""),
        "credential_token": str(form.get("credential_token") or "").strip(),
        "brand": str(form.get("brand") or "generic").strip().lower(),
        "provider_type": str(form.get("provider_type") or "generic_nvr").strip().lower() or "generic_nvr",
        "stream_kinds": stream_kinds,
        "rtsp_port": max(1, min(65535, rtsp_port)),
        "onvif_port": max(1, min(65535, onvif_port)),
        "channel_count": max(1, min(128, channel_count)),
        "probe": _as_bool(str(form.get("probe") or "")),
    }


def _restore_nvr_discovery_password(values: dict[str, Any]) -> None:
    """Reutiliza a senha temporaria sem devolve-la ao navegador."""
    if values.get("password"):
        return
    if values.get("credential_token"):
        cached = get_nvr_discovery_cache(str(values["credential_token"]))
        if not cached:
            raise ValueError("A senha temporaria expirou. Digite a senha do NVR novamente.")
        if (
            str(cached.get("host") or "").strip() != values.get("host")
            or str(cached.get("username") or "").strip() != values.get("username")
        ):
            raise ValueError("O host ou usuario mudou. Digite a senha do NVR novamente.")
        values["password"] = str(cached.get("password") or "")
    if values.get("username") and not values.get("password"):
        raise ValueError("Informe a senha do NVR para testar os canais RTSP.")


def _public_form_values(values: dict[str, Any], *, credential_token: str | None = None) -> dict[str, Any]:
    public_values = dict(values)
    public_values["password"] = ""
    if credential_token is not None:
        public_values["credential_token"] = credential_token
    return public_values


def _render_nvr_page(
    request: Request,
    db,
    *,
    form_values: dict[str, Any],
    error: str | None = None,
    message: str | None = None,
    created: int | None = None,
    skipped: int | None = None,
    credential_token: str | None = None,
    profiles=None,
    status_code: int = 200,
    **extra_context,
):
    context = {
        "request": request,
        "error": error,
        "message": message,
        "created": created,
        "skipped": skipped,
        "credential_token": credential_token,
        "form_values": form_values,
        "brand_options": build_nvr_brand_options(form_values.get("brand")),
        "profiles": profiles,
        "channel_health": build_nvr_channel_health(db),
        **extra_context,
    }
    return templates.TemplateResponse(
        request=request,
        name="nvr_sources.html",
        context=context,
        status_code=status_code,
    )


@router.get("/video-sources/nvr")
def nvr_sources_page(
    request: Request,
    created: int | None = None,
    skipped: int | None = None,
    job: str | None = Query(None),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    active_job = nvr_discovery_job_manager.get(str(job or ""), owner_user_id=current_user.id) if job else None
    if active_job and active_job.status == "completed":
        return RedirectResponse(url=f"/video-sources/nvr/discover/{active_job.token}/result", status_code=303)

    form_values = {
        "brand": "generic",
        "rtsp_port": 554,
        "onvif_port": 80,
        "channel_count": 16,
        "probe": False,
        "stream_kinds": ["main", "sub"],
    }
    if active_job:
        form_values.update(active_job.form_values)
        form_values["password"] = ""
    db = get_scoped_db()
    try:
        return _render_nvr_page(
            request,
            db,
            form_values=form_values,
            created=created,
            skipped=skipped,
            active_job_token=active_job.token if active_job else None,
            discovery_job_counts=active_job.counts() if active_job else None,
        )
    finally:
        db.close()


@router.post("/video-sources/nvr/detect")
async def detect_nvr_device_web(
    request: Request,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    values = _parse_nvr_discovery_form(await request.form())
    if not values["host"]:
        return JSONResponse({"error": "Informe o IP/host do dispositivo."}, status_code=400)
    try:
        _restore_nvr_discovery_password(values)
        result = await asyncio.to_thread(
            detect_common_video_device,
            host=values["host"],
            username=values["username"],
            password=values["password"],
            rtsp_port=values["rtsp_port"],
            onvif_port=values["onvif_port"],
        )
        return JSONResponse(result)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:
        logger.exception("Automatic video device detection failed", extra={"host": values["host"]})
        return JSONResponse({"error": "Falha ao detectar automaticamente o dispositivo."}, status_code=500)


@router.post("/video-sources/nvr/discover/start")
async def start_nvr_discovery_job_web(
    request: Request,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    values = _parse_nvr_discovery_form(await request.form())
    if not values["host"]:
        return JSONResponse({"error": "Informe o IP/host do NVR."}, status_code=400)
    try:
        _restore_nvr_discovery_password(values)
        result = discover_nvr_channels(
            host=values["host"],
            username=values["username"],
            password=values["password"],
            rtsp_port=values["rtsp_port"],
            brand=values["brand"],
            channel_count=values["channel_count"],
            provider_type=values["provider_type"],
            stream_kinds=tuple(values["stream_kinds"]),
            probe=False,
        )
        if len(result.profiles) > 256:
            return JSONResponse(
                {"error": f"A busca gerou {len(result.profiles)} URLs. Reduza os canais/streams ou selecione a marca correta do NVR."},
                status_code=400,
            )
        public_values = _public_form_values(values)
        job = nvr_discovery_job_manager.create(
            owner_user_id=current_user.id,
            host=values["host"],
            username=values["username"],
            password=values["password"],
            form_values=public_values,
            profiles=result.profiles,
            probe_enabled=values["probe"],
        )
        return JSONResponse(job.public_snapshot(), status_code=202 if values["probe"] else 200)
    except Exception as exc:
        return JSONResponse({"error": str(exc) or "Falha ao iniciar descoberta do NVR."}, status_code=400)


@router.get("/video-sources/nvr/discover/{job_token}/status")
def nvr_discovery_job_status_web(
    job_token: str,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    try:
        job = nvr_discovery_job_manager.get(job_token, owner_user_id=current_user.id)
        if not job:
            return JSONResponse({"error": "Descoberta expirada ou não encontrada. Inicie uma nova busca."}, status_code=404)
        return JSONResponse(job.public_snapshot())
    except Exception as exc:
        logger.exception("Falha ao consultar status da descoberta NVR")
        return JSONResponse({"error": "Falha interna ao consultar status do NVR."}, status_code=500)


@router.post("/video-sources/nvr/discover/{job_token}/pause")
def pause_nvr_discovery_job_web(
    job_token: str,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    try:
        job = nvr_discovery_job_manager.pause(job_token, owner_user_id=current_user.id)
        if not job:
            return JSONResponse({"error": "Descoberta expirada ou não encontrada."}, status_code=404)
        return JSONResponse(job.public_snapshot())
    except Exception as exc:
        logger.exception("Falha ao pausar descoberta NVR")
        return JSONResponse({"error": "Falha interna ao pausar busca do NVR."}, status_code=500)


@router.post("/video-sources/nvr/discover/{job_token}/resume")
def resume_nvr_discovery_job_web(
    job_token: str,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    try:
        job = nvr_discovery_job_manager.resume(job_token, owner_user_id=current_user.id)
        if not job:
            return JSONResponse({"error": "Descoberta expirada ou não encontrada."}, status_code=404)
        return JSONResponse(job.public_snapshot(), status_code=202)
    except Exception as exc:
        logger.exception("Falha ao retomar descoberta NVR")
        return JSONResponse({"error": "Falha interna ao retomar busca do NVR."}, status_code=500)


@router.post("/video-sources/nvr/discover/{job_token}/integrate")
def integrate_nvr_discovery_job_web(
    job_token: str,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    try:
        job = nvr_discovery_job_manager.get(job_token, owner_user_id=current_user.id)
        if not job:
            return JSONResponse({"error": "Descoberta expirada ou não encontrada."}, status_code=404)
        with job.lock:
            if job.status in {"queued", "running"}:
                job.run_generation += 1
                job.status = "completed"
                job.touch()
        return JSONResponse(job.public_snapshot())
    except Exception as exc:
        logger.exception("Falha ao integrar descoberta NVR")
        return JSONResponse({"error": "Falha interna ao integrar resultados do NVR."}, status_code=500)


@router.post("/video-sources/nvr/discover/{job_token}/cancel")
def cancel_nvr_discovery_job_web(
    job_token: str,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    try:
        job = nvr_discovery_job_manager.get(job_token, owner_user_id=current_user.id)
        if not job:
            return JSONResponse({"error": "Descoberta expirada ou não encontrada."}, status_code=404)
        with job.lock:
            job.run_generation += 1
            job.status = "canceled"
            job.touch()
        return JSONResponse({"ok": True})
    except Exception as exc:
        logger.exception("Falha ao cancelar descoberta NVR")
        return JSONResponse({"error": "Falha interna ao cancelar busca do NVR."}, status_code=500)


@router.get("/video-sources/nvr/discover/{job_token}/result")
def nvr_discovery_job_result_web(
    request: Request,
    job_token: str,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    job = nvr_discovery_job_manager.get(job_token, owner_user_id=current_user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Descoberta expirada ou nao encontrada.")
    if job.status != "completed":
        return RedirectResponse(url=f"/video-sources/nvr?job={job.token}", status_code=303)

    db = get_scoped_db()
    try:
        profiles = build_nvr_profile_rows(db, job.profile_snapshot(), host=job.host)
        credential_token = store_nvr_discovery_cache(
            host=job.host,
            username=job.username,
            password=job.password,
            profiles=profiles,
            token=job.token,
        )
        counts = job.counts()
        message = (
            f"{counts['total']} perfil(is): {counts['ok']} OK, {counts['failed']} falharam e {counts['timeout']} atingiram o tempo limite."
            if job.probe_enabled
            else f"{counts['total']} URL(s) gerada(s) sem probe."
        )
        safe_values = _public_form_values(job.form_values, credential_token=credential_token)
        return _render_nvr_page(
            request,
            db,
            form_values=safe_values,
            message=message,
            credential_token=credential_token,
            profiles=profiles,
            active_job_token=None,
            completed_job_token=job.token,
            discovery_job_counts=counts,
        )
    finally:
        db.close()


@router.post("/video-sources/nvr/discover")
async def discover_nvr_sources_web(
    request: Request,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    values = _parse_nvr_discovery_form(await request.form())
    db = get_scoped_db()
    try:
        if not values["host"]:
            raise ValueError("Informe o IP/host do NVR.")
        _restore_nvr_discovery_password(values)
        result = discover_nvr_channels(
            host=values["host"],
            username=values["username"],
            password=values["password"],
            rtsp_port=values["rtsp_port"],
            brand=values["brand"],
            channel_count=values["channel_count"],
            provider_type=values["provider_type"],
            stream_kinds=tuple(values["stream_kinds"]),
            probe=values["probe"],
        )
        profiles = build_nvr_profile_rows(db, result.profiles, host=values["host"])
        try:
            ip_addr = request.client.host if request.client else None
            log_audit(
                db,
                "nvr_discover",
                current_user,
                f"Buscou canais de NVR no host: {values['host']} (Canais encontrados: {len(profiles)})",
                ip_address=ip_addr,
            )
        except Exception:
            log_ignored_exception("audit.nvr_discover", level=logging.WARNING)
        credential_token = store_nvr_discovery_cache(
            host=values["host"],
            username=values["username"],
            password=values["password"],
            profiles=profiles,
        )
        safe_values = _public_form_values(values, credential_token=credential_token)
        ok_count = sum(1 for item in profiles if item["ok"])
        message = (
            f"{len(profiles)} perfil(is) gerado(s); {ok_count} abriram frame."
            if values["probe"]
            else f"{len(profiles)} URL(s) gerada(s) sem probe."
        )
        return _render_nvr_page(
            request,
            db,
            form_values=safe_values,
            message=message,
            credential_token=credential_token,
            profiles=profiles,
        )
    except Exception as exc:
        return _render_nvr_page(
            request,
            db,
            form_values=_public_form_values(values),
            error=str(exc) or "Falha ao descobrir canais do NVR.",
            status_code=400,
        )
    finally:
        db.close()


@router.post("/video-sources/nvr/create")
async def create_nvr_sources_web(
    request: Request,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    form = await request.form()
    values = _parse_nvr_discovery_form(form)
    selected_indexes = {str(value) for value in form.getlist("selected_profile")}
    try:
        profile_count = max(0, int(str(form.get("profile_count") or "0").strip()))
    except (TypeError, ValueError):
        profile_count = 0

    db = get_scoped_db()
    try:
        if not selected_indexes:
            raise ValueError("Selecione pelo menos um canal para criar.")
        cached = get_nvr_discovery_cache(values["credential_token"])
        if cached:
            values["host"] = str(cached.get("host") or values["host"])
            values["username"] = str(cached.get("username") or values["username"])
            values["password"] = str(cached.get("password") or values["password"])
        cached_profiles = cached.get("profiles", {}) if cached else {}

        profiles: list[NvrChannelSpec] = []
        missing_url_count = 0
        for index in range(profile_count):
            if str(index) not in selected_indexes:
                continue
            cached_profile = cached_profiles.get(index, {}) if isinstance(cached_profiles, dict) else {}
            rtsp_url = str(cached_profile.get("rtsp_url") or form.get(f"profile_rtsp_url_{index}") or "").strip()
            if not rtsp_url:
                missing_url_count += 1
                continue
            channel = int(str(form.get(f"profile_channel_{index}") or "0").strip() or 0)
            stream_kind = normalize_source_stream_kind(str(form.get(f"profile_stream_kind_{index}") or "main"))
            profiles.append(
                NvrChannelSpec(
                    channel=channel,
                    stream_kind=stream_kind,
                    rtsp_url=rtsp_url,
                    name=str(form.get(f"profile_camera_name_{index}") or "").strip() or None,
                )
            )

        created_count = 0
        skipped_count = missing_url_count
        if profiles:
            result = create_nvr_channel_sources(
                db,
                host=values["host"],
                username=values["username"],
                password=values["password"],
                onvif_port=values["onvif_port"],
                base_name=values["base_name"],
                brand=values["brand"],
                provider_type=values["provider_type"],
                profiles=profiles,
                camera_family="dome",
                scene_category="interno",
                target_focus="pessoa",
                nuisance_profile={},
                analytics_profile=None,
                coordinate_space_override="display",
            )
            created_count = len(result.created)
            skipped_count += len(result.skipped)
        try:
            ip_addr = request.client.host if request.client else None
            log_audit(
                db,
                "nvr_channels_create",
                current_user,
                f"Adicionou {created_count} canais de NVR (IP/Host: {values['host']}) como cameras",
                ip_address=ip_addr,
            )
        except Exception:
            log_ignored_exception("audit.nvr_channels_create", level=logging.WARNING)
        return RedirectResponse(
            url=f"/video-sources/nvr?created={created_count}&skipped={skipped_count}",
            status_code=303,
        )
    except Exception as exc:
        db.rollback()
        return _render_nvr_page(
            request,
            db,
            form_values=_public_form_values(values),
            error=str(exc) or "Falha ao criar canais do NVR.",
            status_code=400,
        )
    finally:
        db.close()
