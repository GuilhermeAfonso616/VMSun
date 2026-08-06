from __future__ import annotations

from urllib.parse import urljoin

from app.core.config import settings


def _normalize_base_url(base_url: str) -> str:
    value = (base_url or "").strip()
    if not value:
        return ""
    return value.rstrip("/") + "/"


def _gateway_public_base_url() -> str:
    public_base_url = getattr(settings, "camera_gateway_public_base_url", "") or ""
    return _normalize_base_url(public_base_url)


def _map_gateway_path(path: str) -> str:
    normalized = path if path.startswith("/") else f"/{path}"

    parts = normalized.strip("/").split("/")
    if len(parts) < 3 or parts[0] != "cameras":
        return normalized

    camera_id = parts[1]

    if normalized.endswith("/snapshot") or normalized.endswith("/snapshot.jpg"):
        return f"/cameras/{camera_id}/snapshot.jpg"

    if (
        normalized.endswith("/stream/raw")
        or normalized.endswith("/stream/processed")
        or normalized.endswith("/stream/live")
    ):
        return f"/cameras/{camera_id}/stream/live"

    return normalized


def resolve_stream_url(path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"

    if settings.camera_gateway_enabled:
        gateway_path = _map_gateway_path(normalized_path)
        public_base_url = _gateway_public_base_url()
        if public_base_url:
            return urljoin(public_base_url, gateway_path.lstrip("/"))
        return f"/monitor/gateway{gateway_path}"

    return normalized_path
