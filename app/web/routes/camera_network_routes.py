"""Rotas web para descoberta e importacao de cameras ONVIF na rede."""

from __future__ import annotations

import ipaddress
import logging

from fastapi import APIRouter, Depends, Form, Request

from app.core.logging import log_ignored_exception
from app.db.models import Camera, User
from app.services.audit_service import log_audit
from app.services.camera_network_service import (
    import_discovered_network_cameras,
    normalize_camera_network_profile,
)
from app.services.onvif_network_discovery import (
    OnvifNetworkDevice,
    discover_onvif_network,
    parse_private_ipv4_network,
)
from app.web.camera_view_models import (
    build_network_device_rows,
    build_network_discovery_page_context,
    suggested_camera_network,
)
from app.web.infrastructure import get_scoped_db, require_web_auth, templates


router = APIRouter()
_AUTHORIZED_ROLES = ["admin", "supervisor"]


def _as_bool(value: str | None) -> bool:
    return value is not None and str(value).lower() in {"1", "true", "on", "yes", "sim"}


def _render_network_page(request: Request, *, status_code: int = 200, **context_values):
    return templates.TemplateResponse(
        request=request,
        name="camera_network_discovery.html",
        context=build_network_discovery_page_context(request=request, **context_values),
        status_code=status_code,
    )


def _parse_devices_from_form(form, network) -> tuple[list[OnvifNetworkDevice], list[OnvifNetworkDevice]]:
    try:
        device_count = min(256, max(0, int(str(form.get("device_count") or "0"))))
    except (TypeError, ValueError):
        device_count = 0
    selected_indexes = {
        int(value)
        for value in form.getlist("selected_device")
        if str(value).isdigit()
    }

    devices: list[OnvifNetworkDevice] = []
    selected_devices: list[OnvifNetworkDevice] = []
    for index in range(device_count):
        ip = str(form.get(f"device_ip_{index}") or "").strip()
        if not ip:
            continue
        try:
            address = ipaddress.ip_address(ip)
            port = int(str(form.get(f"device_port_{index}") or "80"))
        except (TypeError, ValueError):
            continue
        if address not in network or not 1 <= port <= 65535:
            continue
        device = OnvifNetworkDevice(
            ip=str(address),
            port=port,
            name=str(form.get(f"device_name_{index}") or "").strip()[:120],
            manufacturer=str(form.get(f"device_manufacturer_{index}") or "").strip()[:120],
            model=str(form.get(f"device_model_{index}") or "").strip()[:120],
            source=str(form.get(f"device_source_{index}") or "port_scan").strip(),
        )
        devices.append(device)
        if index in selected_indexes:
            selected_devices.append(device)
    return devices, selected_devices


@router.get("/cameras/network-discovery")
def network_discovery_page(
    request: Request,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    db = get_scoped_db()
    try:
        return _render_network_page(request, network=suggested_camera_network(db))
    finally:
        db.close()


@router.post("/cameras/network-discovery")
def scan_camera_network(
    request: Request,
    network: str = Form(...),
    include_port_scan: str | None = Form(None),
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    scan_enabled = _as_bool(include_port_scan)
    try:
        result = discover_onvif_network(network, include_port_scan=scan_enabled)
    except Exception as exc:
        return _render_network_page(
            request,
            network=network,
            error=str(exc) or "Falha ao rastrear a rede.",
            include_port_scan=scan_enabled,
            status_code=400,
        )

    db = get_scoped_db()
    try:
        existing = (
            {
                str(camera.ip): str(camera.name)
                for camera in db.query(Camera)
                .filter(Camera.is_deleted == False, Camera.ip.in_([item.ip for item in result.devices]))
                .all()
            }
            if result.devices
            else {}
        )
        rows = build_network_device_rows(result.devices, existing_by_ip=existing)
        try:
            ip_addr = request.client.host if request.client else None
            log_audit(
                db,
                "camera_network_discover",
                current_user,
                f"Rastreou rede ONVIF {result.network} ({len(rows)} dispositivo(s) encontrado(s))",
                ip_address=ip_addr,
            )
        except Exception:
            log_ignored_exception("audit.camera_network_discover", level=logging.WARNING)
        return _render_network_page(
            request,
            network=result.network,
            devices=rows,
            summary={
                "total": len(rows),
                "ws_discovery_count": result.ws_discovery_count,
                "port_scan_count": result.port_scan_count,
                "elapsed_seconds": result.elapsed_seconds,
            },
            include_port_scan=scan_enabled,
        )
    finally:
        db.close()


@router.post("/cameras/network-discovery/import")
async def import_network_cameras(
    request: Request,
    current_user: User = Depends(require_web_auth(_AUTHORIZED_ROLES)),
):
    form = await request.form()
    network_value = str(form.get("network") or "").strip()
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    raw_family = str(form.get("camera_family") or "dome").strip().lower()
    raw_scene = str(form.get("scene_category") or "interno").strip().lower()
    raw_focus = str(form.get("target_focus") or "pessoa").strip().lower()

    try:
        network = parse_private_ipv4_network(network_value)
    except ValueError as exc:
        return _render_network_page(
            request,
            network=network_value,
            error=str(exc),
            camera_family=raw_family,
            scene_category=raw_scene,
            target_focus=raw_focus,
            status_code=400,
        )

    devices, selected_devices = _parse_devices_from_form(form, network)
    rows = build_network_device_rows(devices)
    if not selected_devices or not username or not password:
        return _render_network_page(
            request,
            network=str(network),
            devices=rows,
            error="Selecione pelo menos uma camera e informe usuario e senha ONVIF.",
            camera_family=raw_family,
            scene_category=raw_scene,
            target_focus=raw_focus,
            status_code=400,
        )

    profile = normalize_camera_network_profile(raw_family, raw_scene, raw_focus)
    db = get_scoped_db()
    try:
        import_results = await import_discovered_network_cameras(
            db,
            devices=selected_devices,
            username=username,
            password=password,
            profile=profile,
        )
        created_count = sum(1 for item in import_results if item["ok"])
        if created_count:
            try:
                ip_addr = request.client.host if request.client else None
                log_audit(
                    db,
                    "camera_network_import",
                    current_user,
                    f"Importou {created_count} camera(s) da rede {network}",
                    ip_address=ip_addr,
                )
            except Exception:
                log_ignored_exception("audit.camera_network_import", level=logging.WARNING)
    finally:
        db.close()

    return _render_network_page(
        request,
        network=str(network),
        devices=rows,
        import_results=import_results,
        camera_family=profile.camera_family,
        scene_category=profile.scene_category,
        target_focus=profile.target_focus,
    )
