from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from app.core.config import settings
from app.core.url_safety import mask_url_credentials


_PATH_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_HIKVISION_STYLE_MAIN_STREAM_RE = re.compile(
    r"(?P<prefix>/Streaming/Channels/)(?P<channel>\d+)01(?P<suffix>(?:[/?#].*)?)$",
    re.IGNORECASE,
)
_registration_cache: dict[int, dict[str, Any]] = {}


def webrtc_gateway_is_enabled() -> bool:
    return bool(settings.webrtc_gateway_enabled and settings.webrtc_gateway_api_base_url.strip())


def webrtc_api_base_url() -> str:
    return str(settings.webrtc_gateway_api_base_url or "").strip().rstrip("/")


def webrtc_public_base_url() -> str:
    return str(settings.webrtc_gateway_public_base_url or "").strip().rstrip("/")


def webrtc_rtsp_public_base_url() -> str:
    configured = str(settings.webrtc_gateway_rtsp_public_base_url or "").strip().rstrip("/")
    if configured:
        return configured

    public_base = webrtc_public_base_url()
    if not public_base:
        return ""

    parsed = urlparse(public_base)
    if not parsed.hostname:
        return ""

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"rtsp://{host}:8554"


def camera_webrtc_path_name(camera_id: int) -> str:
    return f"cam_{int(camera_id)}"


def sanitize_path_name(value: str) -> str:
    cleaned = _PATH_NAME_RE.sub("_", str(value or "").strip())
    cleaned = cleaned.strip("_")
    return cleaned or "camera"


def webrtc_display_source_url(rtsp_url: str | None) -> tuple[str | None, str | None]:
    if not rtsp_url:
        return None, None

    source = str(rtsp_url)
    match = _HIKVISION_STYLE_MAIN_STREAM_RE.search(source)
    if not match:
        return source, None

    # WebRTC in the browser is much more reliable with H.264 substreams.
    # Hikvision/Intelbras-compatible NVR URLs commonly use channel 101 for
    # main stream and 102 for substream, 201/202 for channel 2, and so on.
    adapted = (
        source[: match.start()]
        + match.group("prefix")
        + match.group("channel")
        + "02"
        + match.group("suffix")
    )
    return adapted, "hikvision_style_main_to_substream"


def _api_url(path: str) -> str:
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{webrtc_api_base_url()}{normalized}"


def _mask_diagnostic_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _mask_diagnostic_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_diagnostic_value(item) for item in value]
    if isinstance(value, str) and value.lower().startswith(("rtsp://", "rtsps://")):
        return mask_url_credentials(value)
    return value


def _json_request(method: str, url: str, payload: dict[str, Any] | None = None, timeout_seconds: float | None = None) -> dict[str, Any] | None:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    timeout = max(0.5, float(timeout_seconds or settings.webrtc_gateway_register_timeout_seconds))

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return {"ok": True}
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return {"ok": False, "status": int(exc.code), "error": body or str(exc)}
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc)}

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": True}

    return parsed if isinstance(parsed, dict) else {"ok": True, "data": parsed}


def register_webrtc_path(path_name: str, rtsp_url: str) -> dict[str, Any]:
    """Registra uma origem RTSP arbitrária sem expor credenciais no retorno."""
    safe_path = sanitize_path_name(path_name)
    if not webrtc_gateway_is_enabled():
        return {"ok": False, "path": safe_path, "reason": "disabled"}
    source = str(rtsp_url or "").strip()
    if not source.lower().startswith(("rtsp://", "rtsps://")):
        return {"ok": False, "path": safe_path, "reason": "invalid_rtsp_url"}
    payload = {"source": source, "sourceOnDemand": True, "rtspTransport": "tcp"}
    encoded_name = quote(safe_path, safe="")
    timeout = settings.webrtc_gateway_register_timeout_seconds
    response = _json_request(
        "POST", _api_url(f"/v3/config/paths/add/{encoded_name}"), payload, timeout
    )
    if not response or response.get("ok") is False:
        response = _json_request(
            "PATCH", _api_url(f"/v3/config/paths/patch/{encoded_name}"), payload, timeout
        )
    return {
        "ok": bool(response and response.get("ok") is not False),
        "path": safe_path,
        "masked_source": mask_url_credentials(source),
        "response": _mask_diagnostic_value(response or {}),
    }


def unregister_webrtc_path(path_name: str) -> dict[str, Any]:
    safe_path = sanitize_path_name(path_name)
    if not webrtc_gateway_is_enabled():
        return {"ok": False, "path": safe_path, "reason": "disabled"}
    response = _json_request(
        "DELETE",
        _api_url(f"/v3/config/paths/delete/{quote(safe_path, safe='')}"),
        timeout_seconds=settings.webrtc_gateway_register_timeout_seconds,
    )
    return {
        "ok": bool(response and response.get("ok") is not False),
        "path": safe_path,
        "response": response or {},
    }


def cleanup_webrtc_paths(prefix: str) -> int:
    """Remove paths temporários órfãos; nunca retorna suas URLs de origem."""
    if not webrtc_gateway_is_enabled():
        return 0
    response = _json_request(
        "GET", _api_url("/v3/config/paths/list"),
        timeout_seconds=settings.webrtc_gateway_register_timeout_seconds,
    )
    removed = 0
    for item in (response or {}).get("items", []):
        name = str(item.get("name") or "") if isinstance(item, dict) else ""
        if name.startswith(prefix) and unregister_webrtc_path(name).get("ok"):
            removed += 1
    return removed


def register_webrtc_camera_path(camera_id: int, rtsp_url: str | None) -> dict[str, Any]:
    path_name = camera_webrtc_path_name(camera_id)
    if not webrtc_gateway_is_enabled():
        return {
            "ok": False,
            "path": path_name,
            "reason": "disabled",
        }

    # ``cam_{id}`` e a origem canônica do backbone. Nunca adapte o RTSP
    # configurado (por exemplo main -> substream) durante este registro.
    display_source = str(rtsp_url or "").strip()
    if not display_source:
        return {
            "ok": False,
            "path": path_name,
            "reason": "missing_rtsp_url",
        }

    cache_key = int(camera_id)
    now = time.monotonic()
    encoded_name = quote(sanitize_path_name(path_name), safe="")
    timeout = settings.webrtc_gateway_register_timeout_seconds
    cached = _registration_cache.get(cache_key)
    if (
        cached
        and cached.get("source") == str(display_source)
        and now - float(cached.get("registered_at", 0.0) or 0.0) <= max(1.0, float(settings.webrtc_gateway_registration_cache_ttl_seconds or 300.0))
    ):
        # MediaMTX keeps dynamically configured paths in memory. A successful
        # registration cached by this process can therefore become stale after
        # MediaMTX restarts even though the source itself did not change.
        cached_path = _json_request(
            "GET",
            _api_url(f"/v3/config/paths/get/{encoded_name}"),
            timeout_seconds=timeout,
        )
        if cached_path and cached_path.get("ok") is not False:
            result = dict(cached.get("result") or {})
            result["cached"] = True
            return result
        _registration_cache.pop(cache_key, None)

    path_payload: dict[str, Any] = {
        "source": str(display_source),
        "sourceOnDemand": True,
        "rtspTransport": "tcp",
    }

    response = _json_request("POST", _api_url(f"/v3/config/paths/add/{encoded_name}"), path_payload, timeout)
    created = bool(response and response.get("ok") is not False)
    updated = False
    if not created:
        response = _json_request("PATCH", _api_url(f"/v3/config/paths/patch/{encoded_name}"), path_payload, timeout)
        updated = bool(response and response.get("ok") is not False)

    ok = bool(response and response.get("ok") is not False)
    result = {
        "ok": ok,
        "path": path_name,
        "masked_source": mask_url_credentials(str(display_source)),
        "response": response or {},
        "created": created,
        "updated": updated,
    }
    if ok:
        _registration_cache[cache_key] = {
            "source": str(display_source),
            "registered_at": now,
            "result": dict(result),
        }
    else:
        _registration_cache.pop(cache_key, None)
    return result


def unregister_webrtc_camera_path(camera_id: int) -> dict[str, Any]:
    path_name = camera_webrtc_path_name(camera_id)
    _registration_cache.pop(int(camera_id), None)
    if not webrtc_gateway_is_enabled():
        return {
            "ok": False,
            "path": path_name,
            "reason": "disabled",
        }

    encoded_name = quote(sanitize_path_name(path_name), safe="")
    response = _json_request(
        "DELETE",
        _api_url(f"/v3/config/paths/delete/{encoded_name}"),
        timeout_seconds=settings.webrtc_gateway_register_timeout_seconds,
    )
    return {
        "ok": bool(response and response.get("ok") is not False),
        "path": path_name,
        "response": response or {},
    }


def list_webrtc_camera_paths() -> dict[str, Any]:
    """Lista somente paths canônicos, sem revelar suas origens."""
    if not webrtc_gateway_is_enabled():
        return {"ok": False, "reason": "disabled", "items": []}
    response = _json_request(
        "GET",
        _api_url("/v3/config/paths/list"),
        timeout_seconds=settings.webrtc_gateway_register_timeout_seconds,
    ) or {}
    items = response.get("items") if isinstance(response, dict) else []
    paths: list[str] = []
    for item in items if isinstance(items, list) else []:
        name = str(item.get("name") or "") if isinstance(item, dict) else ""
        if re.fullmatch(r"cam_\d+", name):
            paths.append(name)
    return {"ok": bool(response.get("ok") is not False), "items": sorted(paths)}


def invalidate_webrtc_camera_path_cache(camera_id: int) -> None:
    _registration_cache.pop(int(camera_id), None)


def get_webrtc_path_diagnostics(path_name: str) -> dict[str, Any]:
    safe_path = sanitize_path_name(path_name)
    if not webrtc_gateway_is_enabled():
        return {
            "ok": False,
            "path": safe_path,
            "reason": "disabled",
        }

    encoded_name = quote(safe_path, safe="")
    timeout = settings.webrtc_gateway_register_timeout_seconds
    runtime = _json_request("GET", _api_url(f"/v3/paths/get/{encoded_name}"), timeout_seconds=timeout)
    config = _json_request("GET", _api_url(f"/v3/config/paths/get/{encoded_name}"), timeout_seconds=timeout)
    return {
        "ok": bool(runtime and runtime.get("ok") is not False),
        "path": safe_path,
        "runtime": _mask_diagnostic_value(runtime or {}),
        "config": _mask_diagnostic_value(config or {}),
    }


def build_webrtc_player_url(path_name: str) -> str:
    public_base = webrtc_public_base_url()
    if public_base:
        return f"{public_base}/{quote(sanitize_path_name(path_name), safe='')}"

    return f"/__webrtc_public__/{quote(sanitize_path_name(path_name), safe='')}"


def build_webrtc_rtsp_url(path_name: str) -> str:
    public_base = webrtc_rtsp_public_base_url()
    if public_base:
        return f"{public_base}/{quote(sanitize_path_name(path_name), safe='')}"
    return ""
