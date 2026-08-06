"""View models compartilhados pelas paginas web de cameras."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.analytics.camera_profile_models import CAMERA_FAMILIES, SCENE_CATEGORIES, TARGET_FOCUSES
from app.core.url_safety import mask_url_credentials
from app.db.models import Camera
from app.services.onvif_network_discovery import OnvifNetworkDevice, network_for_camera_ip


MANUFACTURER_OPTIONS: tuple[str, ...] = (
    "Hikvision",
    "Dahua",
    "Intelbras",
    "Uniview",
    "VIVOTEK",
    "Axis Communications",
    "TP-Link",
    "Reolink",
    "ZKTeco",
    "Giga Security",
    "JFL Alarmes",
)


def build_manufacturer_options() -> list[str]:
    return list(MANUFACTURER_OPTIONS)


def _select_options(values: list[str], labels: dict[str, str], selected: str) -> list[dict[str, Any]]:
    return [
        {
            "value": value,
            "label": labels.get(value, value.replace("_", " ").title()),
            "selected": value == selected,
        }
        for value in values
    ]


def build_camera_family_options(selected_family: str | None = None) -> list[dict[str, Any]]:
    labels = {
        "dome": "Dome",
        "bullet": "Bullet",
        "turret": "Turret",
        "ptz": "PTZ",
        "speed_dome": "Speed dome",
        "fisheye": "Fisheye",
        "panoramic": "Panorâmica",
        "multisensor": "Multissensor",
        "box": "Box",
        "lpr": "LPR",
        "thermal": "Térmica",
        "starlight": "Starlight / baixa luz",
    }
    selected = str(selected_family or "dome").strip().lower()
    return _select_options(CAMERA_FAMILIES, labels, selected)


def build_scene_category_options(selected_category: str | None = None) -> list[dict[str, Any]]:
    labels = {
        "interno": "Interno",
        "perimetral": "Perimetral",
        "misto": "Misto",
        "externo_geral": "Externo geral",
        "interno_restrito": "Interno restrito",
    }
    selected = str(selected_category or "interno").strip().lower()
    return _select_options(SCENE_CATEGORIES, labels, selected)


def build_target_focus_options(selected_focus: str | None = None) -> list[dict[str, Any]]:
    labels = {
        "pessoa": "Pessoa",
        "objeto": "Objeto",
        "veiculo": "Veículo",
        "placa": "Placa",
        "zona": "Zona",
        "linha": "Linha",
    }
    selected = str(selected_focus or "pessoa").strip().lower()
    return _select_options(TARGET_FOCUSES, labels, selected)


def build_nuisance_options(profile: Any | None = None) -> list[dict[str, Any]]:
    labels = {
        "vegetation_wind": "Vegetação / vento",
        "rain": "Chuva",
        "headlights": "Faróis",
        "insects_ir": "Insetos no IR",
        "strong_shadows": "Sombras fortes",
        "glass_reflection": "Reflexo em vidro",
        "camera_vibration": "Vibração da câmera",
        "low_texture_scene": "Cena com pouca textura",
        "crowd_occlusion": "Oclusão / multidão",
        "fog_or_haze": "Neblina / haze",
    }
    if profile is None:
        values: dict[str, Any] = {}
    elif isinstance(profile, dict):
        values = profile
    elif is_dataclass(profile):
        values = asdict(profile)
    else:
        values = {key: bool(getattr(profile, key, False)) for key in labels}
    return [
        {"key": key, "label": label, "enabled": bool(values.get(key, False))}
        for key, label in labels.items()
    ]


def build_discovered_profile_options(base_name: str, profiles) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for index, profile in enumerate(profiles):
        suggested_name = base_name.strip() or "Camera"
        clean_profile_name = str(getattr(profile, "name", "") or "").strip()
        if len(profiles) > 1:
            suffix = clean_profile_name or f"Canal {index + 1}"
            suggested_name = f"{suggested_name} - {suffix}"
        width = getattr(profile, "width", None)
        height = getattr(profile, "height", None)
        rtsp_url = str(getattr(profile, "rtsp_url", "") or "")
        options.append(
            {
                "index": index,
                "token": str(getattr(profile, "token", "") or ""),
                "profile_name": clean_profile_name or f"Canal {index + 1}",
                "rtsp_url": rtsp_url,
                "masked_rtsp_url": mask_url_credentials(rtsp_url),
                "encoding": str(getattr(profile, "encoding", "") or "").strip(),
                "resolution_label": f"{width}x{height}" if width and height else "",
                "suggested_name": suggested_name,
                "selected": index == 0,
            }
        )
    return options


def build_discovery_context(
    *,
    request,
    base_name: str,
    ip: str,
    username: str,
    manufacturer: str = "",
    model: str | None = None,
    onvif_port: int,
    profiles: list[dict[str, Any]],
    password: str = "",
    credential_token: str | None = None,
    mode: str = "new",
    camera_id: int | None = None,
    error: str | None = None,
    camera_family: str = "dome",
    scene_category: str = "interno",
    target_focus: str = "pessoa",
    nuisance_profile: dict[str, Any] | None = None,
    discovery_method: str = "onvif",
) -> dict[str, Any]:
    normalized_family = camera_family if camera_family in CAMERA_FAMILIES else "dome"
    normalized_scene = scene_category if scene_category in SCENE_CATEGORIES else "interno"
    normalized_focus = target_focus if target_focus in TARGET_FOCUSES else "pessoa"
    return {
        "request": request,
        "error": error,
        "base_name": base_name,
        "ip": ip,
        "username": username,
        "manufacturer": manufacturer,
        "model": model,
        "password": password,
        "credential_token": credential_token,
        "onvif_port": onvif_port,
        "profiles": profiles,
        "mode": mode,
        "camera_id": camera_id,
        "camera_family": normalized_family,
        "camera_family_options": build_camera_family_options(normalized_family),
        "scene_category": normalized_scene,
        "scene_category_options": build_scene_category_options(normalized_scene),
        "target_focus": normalized_focus,
        "target_focus_options": build_target_focus_options(normalized_focus),
        "nuisance_options": build_nuisance_options(nuisance_profile),
        "discovery_method": discovery_method,
    }


def build_camera_new_context(
    *,
    request,
    form_values: dict[str, Any] | None = None,
    error: str | None = None,
    rtsp_test_results=None,
    rtsp_test_source=None,
    camera_family: str = "dome",
    scene_category: str = "interno",
    target_focus: str = "pessoa",
    nuisance_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "request": request,
        "error": error,
        "rtsp_test_results": rtsp_test_results,
        "rtsp_test_source": rtsp_test_source,
        "camera_family_options": build_camera_family_options(camera_family),
        "scene_category_options": build_scene_category_options(scene_category),
        "target_focus_options": build_target_focus_options(target_focus),
        "nuisance_options": build_nuisance_options(nuisance_profile),
        "selected_camera_family": camera_family,
        "selected_scene_category": scene_category,
        "selected_target_focus": target_focus,
        "form_values": form_values or {},
        "manufacturer_options": build_manufacturer_options(),
    }


def build_network_device_rows(
    devices: list[OnvifNetworkDevice],
    *,
    existing_by_ip: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    existing_by_ip = existing_by_ip or {}
    rows: list[dict[str, Any]] = []
    for index, device in enumerate(devices):
        rows.append(
            {
                "index": index,
                "ip": device.ip,
                "port": device.port,
                "name": device.name,
                "manufacturer": device.manufacturer,
                "model": device.model,
                "xaddr": device.xaddr,
                "source": device.source,
                "suggested_name": device.suggested_name,
                "existing_name": existing_by_ip.get(device.ip, ""),
                "add_url": "/cameras/new?"
                + urlencode(
                    {
                        "ip": device.ip,
                        "onvif_port": device.port,
                        "name": device.suggested_name,
                        "manufacturer": device.manufacturer,
                        "model": device.model,
                    }
                ),
            }
        )
    return rows


def build_network_discovery_page_context(
    *,
    request,
    network: str,
    devices: list[dict[str, Any]] | None = None,
    error: str | None = None,
    summary: dict[str, Any] | None = None,
    import_results: list[dict[str, Any]] | None = None,
    include_port_scan: bool = True,
    camera_family: str = "dome",
    scene_category: str = "interno",
    target_focus: str = "pessoa",
) -> dict[str, Any]:
    return {
        "request": request,
        "network": network,
        "devices": devices,
        "error": error,
        "summary": summary,
        "import_results": import_results,
        "include_port_scan": include_port_scan,
        "camera_family_options": build_camera_family_options(camera_family),
        "scene_category_options": build_scene_category_options(scene_category),
        "target_focus_options": build_target_focus_options(target_focus),
    }


def suggested_camera_network(db: Session) -> str:
    camera_ips = (
        db.query(Camera.ip)
        .filter(Camera.is_deleted == False)
        .order_by(Camera.id.desc())
        .limit(50)
        .all()
    )
    for row in camera_ips:
        network = network_for_camera_ip(row[0])
        if network:
            return network
    return "192.168.1.0/24"
