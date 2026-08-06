"""Paginas de descoberta e importacao HikCentral/Hik-Connect."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.core.logging import log_ignored_exception
from app.db.models import User
from app.services.audit_service import log_audit
from app.services.hik_source_service import (
    build_hik_discovery_profiles,
    create_hik_sources,
)
from app.services.hikcentral_client import (
    discover_hikcentral_cameras,
    discover_hikconnect_cameras,
)
from app.services.nvr_discovery_cache import (
    get_nvr_discovery_cache,
    store_nvr_discovery_cache,
)
from app.web.hik_source_presenter import (
    build_hikcentral_channel_health,
    build_hikconnect_channel_health,
    hikcentral_form_defaults,
    hikconnect_form_defaults,
    without_password,
)
from app.web.infrastructure import get_scoped_db, require_web_auth, templates


router = APIRouter()
_ALLOWED_ROLES = ["admin", "supervisor"]


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
        "sim",
    }


def _channel_number(value: Any) -> int:
    try:
        return int(str(value or "1").strip())
    except (TypeError, ValueError):
        return 1


def _audit_discovery(
    db,
    request: Request,
    current_user: User,
    action: str,
    details: str,
) -> None:
    try:
        ip_address = request.client.host if request.client else None
        log_audit(
            db,
            action,
            current_user,
            details,
            ip_address=ip_address,
        )
    except Exception:
        log_ignored_exception(f"audit.{action}", level=logging.WARNING)


def _render_hik_page(
    *,
    provider: str,
    request: Request,
    db,
    error: str | None = None,
    message: str | None = None,
    created: int | None = None,
    skipped: int | None = None,
    credential_token: str | None = None,
    form_values: dict[str, Any] | None = None,
    profiles: list[dict[str, Any]] | None = None,
    status_code: int = 200,
):
    if provider == "hikcentral":
        template_name = "hikcentral_sources.html"
        defaults = hikcentral_form_defaults()
        channel_health = build_hikcentral_channel_health(db)
    else:
        template_name = "hikconnect_sources.html"
        defaults = hikconnect_form_defaults()
        channel_health = build_hikconnect_channel_health(db)

    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "request": request,
            "error": error,
            "message": message,
            "created": created,
            "skipped": skipped,
            "credential_token": credential_token,
            "form_values": form_values if form_values is not None else defaults,
            "profiles": profiles,
            "channel_health": channel_health,
        },
        status_code=status_code,
    )


@router.get("/video-sources/hikcentral")
def hikcentral_sources_page(
    request: Request,
    created: int | None = None,
    skipped: int | None = None,
    current_user: User = Depends(require_web_auth(_ALLOWED_ROLES)),
):
    db = get_scoped_db()
    try:
        return _render_hik_page(
            provider="hikcentral",
            request=request,
            db=db,
            created=created,
            skipped=skipped,
        )
    finally:
        db.close()


@router.post("/video-sources/hikcentral/discover")
async def discover_hikcentral_sources_web(
    request: Request,
    current_user: User = Depends(require_web_auth(_ALLOWED_ROLES)),
):
    form = await request.form()
    host = str(form.get("host") or "").strip()
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    base_name = str(form.get("base_name") or "HikCentral").strip()
    simulate = _as_bool(form.get("simulate"))
    form_values = {
        "host": host,
        "username": username,
        "password": password,
        "base_name": base_name,
        "simulate": simulate,
    }

    db = get_scoped_db()
    try:
        if not host:
            raise ValueError("Informe o IP/host do servidor HikCentral.")
        try:
            discovered = discover_hikcentral_cameras(
                host=host,
                app_key=username,
                app_secret=password,
                simulate=simulate,
            )
        except Exception as exc:
            if not simulate:
                raise ValueError(
                    "Não foi possível conectar ao HikCentral OpenAPI: "
                    f"{exc}. Ative a opção 'Simular ambiente de teste' "
                    "para testar offline."
                ) from exc
            raise

        _audit_discovery(
            db,
            request,
            current_user,
            "hikcentral_discover",
            (
                f"Buscou canais do HikCentral no host: {host} "
                f"(Canais encontrados: {len(discovered)})"
            ),
        )
        profiles = build_hik_discovery_profiles(
            db,
            provider="hikcentral",
            discovered_cameras=discovered,
        )
        credential_token = store_nvr_discovery_cache(
            host=host,
            username=username,
            password=password,
            profiles=profiles,
        )
        safe_values = without_password(form_values)
        safe_values["credential_token"] = credential_token
        return _render_hik_page(
            provider="hikcentral",
            request=request,
            db=db,
            message=(
                f"Conectado com sucesso! Encontrada(s) {len(profiles)} "
                "câmera(s) no HikCentral."
            ),
            credential_token=credential_token,
            form_values=safe_values,
            profiles=profiles,
        )
    except Exception as exc:
        return _render_hik_page(
            provider="hikcentral",
            request=request,
            db=db,
            error=str(exc),
            form_values=without_password(form_values),
            status_code=400,
        )
    finally:
        db.close()


@router.post("/video-sources/hikcentral/create")
async def create_hikcentral_sources_web(
    request: Request,
    current_user: User = Depends(require_web_auth(_ALLOWED_ROLES)),
):
    form = await request.form()
    host = str(form.get("host") or "").strip()
    username = str(form.get("username") or "").strip()
    credential_token = str(form.get("credential_token") or "").strip()
    base_name = str(form.get("base_name") or "HikCentral").strip()
    selected_indexes = {str(value) for value in form.getlist("selected_profile")}
    db = get_scoped_db()
    try:
        camera_names = {
            int(index): str(form.get(f"profile_camera_name_{index}") or "").strip()
            for index in selected_indexes
        }
        cache = get_nvr_discovery_cache(credential_token)
        if not cache:
            raise HTTPException(
                status_code=400,
                detail="Sessão de descoberta expirou. Refaça a busca das câmeras.",
            )
        result = create_hik_sources(
            db,
            provider="hikcentral",
            host=host,
            username=username,
            password=str(cache.get("password") or ""),
            verification_code="",
            selected_indexes=selected_indexes,
            cached_profiles=cache.get("profiles") or {},
            camera_names=camera_names,
        )
        _audit_discovery(
            db,
            request,
            current_user,
            "hikcentral_channels_create",
            (
                f"Adicionou {result.created_count} canais do HikCentral "
                f"(Host: {host}) como câmeras"
            ),
        )
        return RedirectResponse(
            url=(
                "/video-sources/hikcentral"
                f"?created={result.created_count}&skipped={result.skipped_count}"
            ),
            status_code=303,
        )
    except Exception as exc:
        db.rollback()
        return _render_hik_page(
            provider="hikcentral",
            request=request,
            db=db,
            error=str(exc) or "Falha ao criar canais HikCentral.",
            form_values={
                "host": host,
                "username": username,
                "base_name": base_name,
                "simulate": True,
            },
            status_code=400,
        )
    finally:
        db.close()


@router.get("/video-sources/hikconnect")
def hikconnect_sources_page(
    request: Request,
    created: int | None = None,
    skipped: int | None = None,
    current_user: User = Depends(require_web_auth(_ALLOWED_ROLES)),
):
    db = get_scoped_db()
    try:
        return _render_hik_page(
            provider="hikconnect",
            request=request,
            db=db,
            created=created,
            skipped=skipped,
        )
    finally:
        db.close()


@router.post("/video-sources/hikconnect/discover")
async def discover_hikconnect_sources_web(
    request: Request,
    current_user: User = Depends(require_web_auth(_ALLOWED_ROLES)),
):
    form = await request.form()
    serial_number = str(form.get("serial_number") or "").strip()
    verification_code = str(form.get("verification_code") or "").strip()
    channel_no = _channel_number(form.get("channel_no"))
    username = str(form.get("username") or "admin").strip()
    password = str(form.get("password") or "")
    base_name = str(form.get("base_name") or "Hik-Connect").strip()
    simulate = _as_bool(form.get("simulate"))
    form_values = {
        "serial_number": serial_number,
        "verification_code": verification_code,
        "channel_no": channel_no,
        "username": username,
        "password": password,
        "base_name": base_name,
        "simulate": simulate,
    }

    db = get_scoped_db()
    try:
        if not serial_number:
            raise ValueError("Informe o Número de Série (S/N) do dispositivo.")
        if not verification_code:
            raise ValueError("Informe o Código de Verificação do dispositivo.")
        discovered = discover_hikconnect_cameras(
            serial_number=serial_number,
            verification_code=verification_code,
            channel_no=channel_no,
            simulate=simulate,
        )
        _audit_discovery(
            db,
            request,
            current_user,
            "hikconnect_discover",
            (
                f"Buscou canais do Hik-Connect com Serial: {serial_number} "
                f"(Canais encontrados: {len(discovered)})"
            ),
        )
        profiles = build_hik_discovery_profiles(
            db,
            provider="hikconnect",
            discovered_cameras=discovered,
            serial_number=serial_number,
            verification_code=verification_code,
            channel_no=channel_no,
        )
        credential_token = store_nvr_discovery_cache(
            host=serial_number,
            username=username,
            password=password,
            profiles=profiles,
        )
        safe_values = without_password(form_values)
        safe_values["credential_token"] = credential_token
        return _render_hik_page(
            provider="hikconnect",
            request=request,
            db=db,
            message=(
                "Conexão com a nuvem Hik-Connect testada com sucesso! "
                f"Canal {channel_no} pronto para importação."
            ),
            credential_token=credential_token,
            form_values=safe_values,
            profiles=profiles,
        )
    except Exception as exc:
        return _render_hik_page(
            provider="hikconnect",
            request=request,
            db=db,
            error=str(exc),
            form_values=without_password(form_values),
            status_code=400,
        )
    finally:
        db.close()


@router.post("/video-sources/hikconnect/create")
async def create_hikconnect_sources_web(
    request: Request,
    current_user: User = Depends(require_web_auth(_ALLOWED_ROLES)),
):
    form = await request.form()
    serial_number = str(form.get("serial_number") or "").strip()
    verification_code = str(form.get("verification_code") or "").strip()
    channel_no = _channel_number(form.get("channel_no"))
    username = str(form.get("username") or "admin").strip()
    credential_token = str(form.get("credential_token") or "").strip()
    base_name = str(form.get("base_name") or "Hik-Connect").strip()
    selected_indexes = {str(value) for value in form.getlist("selected_profile")}
    db = get_scoped_db()
    try:
        camera_names = {
            int(index): str(form.get(f"profile_camera_name_{index}") or "").strip()
            for index in selected_indexes
        }
        cache = get_nvr_discovery_cache(credential_token)
        if not cache:
            raise HTTPException(
                status_code=400,
                detail="Sessão de descoberta expirou. Refaça a busca do canal.",
            )
        result = create_hik_sources(
            db,
            provider="hikconnect",
            host=serial_number,
            username=username,
            password=str(cache.get("password") or ""),
            verification_code=verification_code,
            selected_indexes=selected_indexes,
            cached_profiles=cache.get("profiles") or {},
            camera_names=camera_names,
        )
        _audit_discovery(
            db,
            request,
            current_user,
            "hikconnect_channels_create",
            (
                f"Adicionou {result.created_count} canais do Hik-Connect "
                f"(Serial: {serial_number}) como câmeras"
            ),
        )
        return RedirectResponse(
            url=(
                "/video-sources/hikconnect"
                f"?created={result.created_count}&skipped={result.skipped_count}"
            ),
            status_code=303,
        )
    except Exception as exc:
        db.rollback()
        return _render_hik_page(
            provider="hikconnect",
            request=request,
            db=db,
            error=str(exc) or "Falha ao criar canal Hik-Connect.",
            form_values={
                "serial_number": serial_number,
                "verification_code": verification_code,
                "channel_no": channel_no,
                "username": username,
                "base_name": base_name,
                "simulate": True,
            },
            status_code=400,
        )
    finally:
        db.close()
