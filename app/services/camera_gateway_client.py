from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import settings
from app.core.url_safety import mask_url_credentials
from app.services.media_backbone_service import (
    MediaBackboneUnavailable,
    camera_gateway_source_mode,
    camera_media_rtsp_url,
    ensure_camera_media_path,
    media_backbone_selected_for_camera,
)


logger = logging.getLogger("app.camera_gateway_client")
_direct_fallback_total = 0


def gateway_is_enabled() -> bool:
    return bool(settings.camera_gateway_enabled and settings.camera_gateway_base_url.strip())


def _gateway_base_url() -> str:
    return str(settings.camera_gateway_base_url or "").strip().rstrip("/")


def gateway_service_url(path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{_gateway_base_url()}{normalized_path}"


def gateway_camera_url(camera_id: int, suffix: str) -> str:
    normalized_suffix = suffix if suffix.startswith("/") else f"/{suffix}"
    return f"{_gateway_base_url()}/cameras/{int(camera_id)}{normalized_suffix}"


def camera_gateway_via_webrtc_rtsp_is_enabled() -> bool:
    return camera_gateway_source_mode() != "direct"


def webrtc_rtsp_source_url(camera_id: int) -> str:
    return camera_media_rtsp_url(camera_id)


def resolve_camera_gateway_source_url(
    camera_id: int,
    source_url: str | None,
    timeout_seconds: float | None = None,
) -> str | None:
    global logger, _direct_fallback_total
    if not source_url:
        return source_url

    # Se for uma URL do HikCentral, resolve dinamicamente via OpenAPI
    if source_url.startswith("hc://"):
        camera_index_code = source_url[5:]
        from app.db.base import SessionLocal
        from app.db.models import Camera
        from app.services.hikcentral_client import get_hikcentral_preview_url
        import logging
        logger = logging.getLogger("app.camera_gateway_client")
        db = SessionLocal()
        try:
            camera = db.query(Camera).filter(Camera.id == int(camera_id)).first()
            if camera:
                try:
                    resolved_url = get_hikcentral_preview_url(
                        host=camera.ip,
                        app_key=camera.username,
                        app_secret=camera.password,
                        camera_index_code=camera_index_code
                    )
                    source_url = resolved_url
                    logger.info("URL do HikCentral resolvida com sucesso para camera_id=%s: %s", camera_id, mask_url_credentials(resolved_url))
                except Exception as exc:
                    logger.warning("Falha ao obter URL dinâmica do HikCentral para camera_id=%s: %s", camera_id, exc)
        finally:
            db.close()

    # Se for uma URL do Hik-Connect P2P, resolve dinamicamente
    if source_url.startswith("hcp2p://"):
        import urllib.parse
        parsed = urllib.parse.urlparse(source_url)
        serial_number = parsed.netloc
        params = urllib.parse.parse_qs(parsed.query)
        verification_code = params.get("verify", [""])[0]
        channel_no_str = params.get("channel", ["1"])[0]
        try:
            channel_no = int(channel_no_str)
        except Exception:
            channel_no = 1
            
        from app.services.hikcentral_client import get_hikconnect_preview_url
        import logging
        logger = logging.getLogger("app.camera_gateway_client")
        
        simulate = serial_number.startswith("hc-")
        try:
            resolved_url = get_hikconnect_preview_url(
                serial_number=serial_number,
                verification_code=verification_code,
                channel_no=channel_no,
                simulate=simulate
            )
            source_url = resolved_url
            logger.info("URL do Hik-Connect P2P resolvida com sucesso para camera_id=%s: %s", camera_id, mask_url_credentials(resolved_url))
        except Exception as exc:
            logger.warning("Falha ao obter URL P2P do Hik-Connect para camera_id=%s: %s", camera_id, exc)

    mode = camera_gateway_source_mode()
    if mode == "direct" or not media_backbone_selected_for_camera(int(camera_id)):
        return source_url
    media_path = ensure_camera_media_path(int(camera_id), source_url)
    if media_path.ok:
        return media_path.internal_rtsp_url
    if mode == "mediamtx_prefer":
        _direct_fallback_total += 1
        logger.warning(
            "Camera gateway using explicit direct RTSP fallback",
            extra={"action": "camera_gateway_direct_fallback", "camera_id": int(camera_id), "error_code": media_path.error_code},
        )
        return source_url
    raise MediaBackboneUnavailable(
        media_path.error_code or "media_backbone_unavailable",
        media_path.error_message or "MediaMTX indisponivel para esta camera.",
    )


def _fetch_json(url: str, timeout_seconds: float) -> dict[str, Any] | None:
    request = Request(url, headers={"Accept": "application/json"})

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            if int(status) >= 400:
                return None
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None

    if not raw:
        return None

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None

    return payload if isinstance(payload, dict) else None


def _post_json(url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any] | None:
    raw_payload = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=raw_payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            if int(status) >= 400:
                return None
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None

    if not raw:
        return None

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        return None

    return parsed if isinstance(parsed, dict) else None


def register_camera_source(
    camera_id: int,
    source_url: str | None,
    timeout_seconds: float | None = None,
) -> dict[str, Any] | None:
    if not gateway_is_enabled() or not source_url:
        return None

    timeout = max(0.5, float(timeout_seconds or settings.camera_health_probe_timeout_seconds))
    resolved_source_url = resolve_camera_gateway_source_url(camera_id, source_url, timeout_seconds=timeout)
    response = _post_json(
        gateway_camera_url(camera_id, "/source"),
        {"source_url": str(resolved_source_url or source_url)},
        timeout,
    )

    if not response or not bool(response.get("ok")):
        return None

    return response


def stop_camera_source(
    camera_id: int,
    timeout_seconds: float | None = None,
) -> dict[str, Any] | None:
    if not gateway_is_enabled():
        return None

    timeout = max(0.5, float(timeout_seconds or settings.camera_health_probe_timeout_seconds))
    request = Request(
        gateway_camera_url(camera_id, "/source"),
        headers={"Accept": "application/json"},
        method="DELETE",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None

    if not raw:
        return {"ok": True}

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": True}

    return parsed if isinstance(parsed, dict) else {"ok": True}


def remove_camera_frame_buffer(
    camera_id: int,
    timeout_seconds: float | None = None,
) -> dict[str, Any] | None:
    """Para a origem e remove somente o recurso IPC da câmera excluída."""
    if not gateway_is_enabled():
        return None
    timeout = max(
        0.5, float(timeout_seconds or settings.camera_health_probe_timeout_seconds)
    )
    request = Request(
        gateway_camera_url(camera_id, "/source") + "?remove_buffer=true",
        headers={"Accept": "application/json"},
        method="DELETE",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None
    if not raw:
        return {"ok": True}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": True}
    return parsed if isinstance(parsed, dict) else {"ok": True}


def fetch_camera_status(
    camera_id: int,
    source_url: str | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any] | None:
    if not gateway_is_enabled():
        return None

    timeout = max(0.5, float(timeout_seconds or settings.camera_health_probe_timeout_seconds))

    if source_url:
        register_camera_source(camera_id, source_url, timeout_seconds=timeout)

    return _fetch_json(gateway_camera_url(camera_id, "/status"), timeout)


def fetch_gateway_health(timeout_seconds: float | None = None) -> dict[str, Any] | None:
    if not gateway_is_enabled():
        return None

    timeout = max(0.5, float(timeout_seconds or settings.camera_health_probe_timeout_seconds))
    return _fetch_json(gateway_service_url("/healthz"), timeout)


def fetch_gateway_cameras(timeout_seconds: float | None = None) -> list[dict[str, Any]]:
    if not gateway_is_enabled():
        return []

    timeout = max(0.5, float(timeout_seconds or settings.camera_health_probe_timeout_seconds))
    payload = _fetch_json(gateway_service_url("/cameras"), timeout)
    cameras = (payload or {}).get("cameras")
    if not isinstance(cameras, list):
        return []
    return [item for item in cameras if isinstance(item, dict)]
