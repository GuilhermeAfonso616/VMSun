"""Registro canônico dos paths MediaMTX consumidos continuamente."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from app.core.config import settings
from app.services.webrtc_gateway_client import (
    camera_webrtc_path_name,
    register_webrtc_camera_path,
    webrtc_gateway_is_enabled,
)


class MediaBackboneUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class MediaPathResult:
    ok: bool
    camera_id: int
    path_name: str
    internal_rtsp_url: str | None
    source_kind: str = "mediamtx"
    created: bool = False
    updated: bool = False
    ready: bool | None = None
    error_code: str | None = None
    error_message: str | None = None


def camera_gateway_source_mode() -> str:
    explicit = str(settings.camera_gateway_source_mode or "").strip().lower()
    if explicit:
        if explicit not in {"direct", "mediamtx_prefer", "mediamtx_strict"}:
            raise ValueError("CAMERA_GATEWAY_SOURCE_MODE deve ser direct, mediamtx_prefer ou mediamtx_strict")
        return explicit
    return "mediamtx_prefer" if settings.camera_gateway_via_webrtc_rtsp_enabled else "direct"


def media_backbone_selected_for_camera(camera_id: int) -> bool:
    raw = str(settings.camera_gateway_mediamtx_camera_ids or "").strip()
    if not raw:
        return True
    if raw == "*":
        return True
    selected: set[int] = set()
    for item in raw.split(","):
        try:
            value = int(item.strip())
        except ValueError:
            continue
        if value > 0:
            selected.add(value)
    return int(camera_id) in selected


def camera_media_rtsp_url(camera_id: int) -> str:
    base = str(settings.webrtc_gateway_rtsp_base_url or "").strip().rstrip("/")
    return f"{base}/{camera_webrtc_path_name(int(camera_id))}" if base else ""


def ensure_camera_media_path(camera_id: int, source_url: str | None) -> MediaPathResult:
    try:
        normalized_id = int(camera_id)
    except (TypeError, ValueError):
        normalized_id = 0
    path_name = camera_webrtc_path_name(max(0, normalized_id))
    if normalized_id <= 0:
        return MediaPathResult(False, normalized_id, path_name, None, error_code="invalid_camera_id", error_message="camera_id invalido")
    source = str(source_url or "").strip()
    parsed = urlparse(source)
    if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not parsed.hostname:
        return MediaPathResult(False, normalized_id, path_name, None, error_code="invalid_rtsp_url", error_message="URL RTSP invalida")
    internal_url = camera_media_rtsp_url(normalized_id)
    if not webrtc_gateway_is_enabled() or not internal_url:
        return MediaPathResult(False, normalized_id, path_name, None, error_code="media_backbone_unavailable", error_message="MediaMTX nao esta configurado")
    registration = register_webrtc_camera_path(normalized_id, source)
    if not registration.get("ok"):
        return MediaPathResult(False, normalized_id, path_name, None, error_code="media_path_registration_failed", error_message=str(registration.get("reason") or "Falha ao registrar path"))
    return MediaPathResult(
        True,
        normalized_id,
        path_name,
        internal_url,
        created=bool(registration.get("created")),
        updated=bool(registration.get("updated")),
        ready=None,
    )
