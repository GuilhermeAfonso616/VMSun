from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import settings


class RuntimeClientError(RuntimeError):
    pass


def normalized_app_role() -> str:
    role = str(settings.app_role or "all").strip().lower()
    return role if role in {"all", "web", "runtime"} else "all"


def runtime_role_enabled() -> bool:
    return normalized_app_role() in {"all", "runtime"}


def web_role_enabled() -> bool:
    return normalized_app_role() in {"all", "web"}


def remote_runtime_enabled() -> bool:
    return normalized_app_role() == "web" and bool(str(settings.runtime_api_base_url or "").strip())


def _runtime_url(path: str, params: dict | None = None) -> str:
    base_url = str(settings.runtime_api_base_url or "").strip().rstrip("/")
    if not base_url:
        raise RuntimeClientError("RUNTIME_API_BASE_URL nao configurado")
    normalized_path = "/" + str(path or "").lstrip("/")
    url = f"{base_url}{normalized_path}"
    if params:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        if query:
            url = f"{url}?{query}"
    return url


def runtime_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
    timeout: float | None = None,
) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(_runtime_url(path, params=params), data=data, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout or settings.runtime_api_timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeClientError(detail or str(exc)) from exc
    except URLError as exc:
        raise RuntimeClientError(str(getattr(exc, "reason", exc))) from exc
    except TimeoutError as exc:
        raise RuntimeClientError("timeout ao chamar runtime") from exc

    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeClientError(f"runtime retornou JSON invalido: {body[:200]}") from exc
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def runtime_get(path: str, *, params: dict | None = None, timeout: float | None = None) -> dict:
    return runtime_request("GET", path, params=params, timeout=timeout)


def runtime_post(
    path: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
    timeout: float | None = None,
) -> dict:
    return runtime_request("POST", path, params=params, payload=payload, timeout=timeout)


def runtime_bytes(path: str, *, params: dict | None = None, timeout: float | None = None) -> bytes | None:
    request = Request(_runtime_url(path, params=params), headers={"Accept": "*/*"}, method="GET")
    try:
        with urlopen(request, timeout=timeout or settings.runtime_api_timeout_seconds) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status >= 400:
                return None
            return response.read()
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None


def get_runtime_health_snapshot() -> dict:
    if remote_runtime_enabled():
        try:
            return runtime_get("/internal/health/cameras", timeout=max(1.0, settings.runtime_api_timeout_seconds))
        except RuntimeClientError as exc:
            return {"status": "error", "runtime_error": str(exc), "cameras": []}

    from app.services.camera_health_monitor import camera_health_monitor

    return camera_health_monitor.get_snapshot()


def start_runtime_camera(camera_id: int, *, use_motion_test: bool = True, restart_existing: bool = True) -> dict:
    return runtime_post(
        f"/internal/cameras/{camera_id}/start",
        params={
            "use_motion_test": str(bool(use_motion_test)).lower(),
            "restart_existing": str(bool(restart_existing)).lower(),
        },
        timeout=max(20.0, settings.runtime_api_timeout_seconds),
    )


def stop_runtime_camera(camera_id: int, *, timeout_seconds: float = 5.0) -> dict:
    return runtime_post(
        f"/internal/cameras/{camera_id}/stop",
        params={"timeout_seconds": timeout_seconds},
        timeout=max(timeout_seconds + 1.0, settings.runtime_api_timeout_seconds),
    )


def restart_active_runtime_workers() -> dict:
    return runtime_post("/internal/cameras/restart-active", timeout=max(10.0, settings.runtime_api_timeout_seconds))


def update_runtime_tuning_controls(payload: dict) -> dict:
    return runtime_post(
        "/internal/runtime-tuning",
        payload=payload,
        timeout=max(5.0, settings.runtime_api_timeout_seconds),
    )


def set_runtime_gateway_capture_mode(mode: str) -> dict:
    return runtime_post(
        "/internal/gateway-mode",
        params={"mode": mode},
        timeout=max(10.0, settings.runtime_api_timeout_seconds),
    )


def probe_runtime_camera(camera_id: int) -> dict:
    return runtime_get(f"/internal/cameras/{camera_id}/probe", timeout=max(5.0, settings.runtime_api_timeout_seconds))


def fetch_runtime_camera_frame(camera_id: int, kind: str = "processed") -> bytes | None:
    normalized_kind = "raw" if str(kind or "").strip().lower() == "raw" else "processed"
    return runtime_bytes(
        f"/internal/cameras/{int(camera_id)}/frame/{normalized_kind}",
        timeout=max(2.0, settings.runtime_api_timeout_seconds),
    )
