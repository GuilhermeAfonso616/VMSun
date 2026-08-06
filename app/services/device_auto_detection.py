from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import socket
import time
from typing import Any

from app.camera.rtsp_discovery import build_rtsp_url, probe_rtsp_url_details_bounded
from app.core.config import settings
from app.services.onvif_network_discovery import OnvifNetworkDevice, probe_onvif_endpoint
from app.video_sources.providers.generic_nvr import NVR_RTSP_TEMPLATES


DEFAULT_QUICK_PORTS = (37777, 8000, 554, 80, 443, 8080, 8899)
NATIVE_SDK_PORTS = {37777, 8000}
ONVIF_CANDIDATE_PORTS = (80, 8080, 8899, 443)

_MANUFACTURER_BRAND_HINTS: tuple[tuple[str, str], ...] = (
    ("hikvision", "hikvision"),
    ("hangzhou", "hikvision"),
    ("dahua", "dahua"),
    ("intelbras", "intelbras"),
    ("uniview", "uniview"),
    ("vivotek", "vivotek"),
    ("axis", "axis"),
    ("tp-link", "tplink"),
    ("tplink", "tplink"),
    ("reolink", "reolink"),
    ("zkteco", "zkteco"),
    ("giga", "giga"),
    ("jfl", "jfl"),
)


def _brand_from_manufacturer(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return ""
    for hint, brand in _MANUFACTURER_BRAND_HINTS:
        if hint in lowered:
            return brand
    return ""


@dataclass(frozen=True, slots=True)
class DriverCandidate:
    driver_id: str
    label: str
    brand: str
    provider_type: str
    port: int
    protocol: str
    detected: bool
    ready: bool
    detail: str


def _native_gateway_ready(provider_type: str) -> bool:
    setting_name = {
        "dahua_sdk": "dahua_sdk_gateway_base_url",
        "intelbras_sdk": "intelbras_sdk_gateway_base_url",
        "hikvision_sdk": "hikvision_sdk_gateway_base_url",
    }.get(provider_type, "")
    return bool(str(getattr(settings, setting_name, "") or "").strip()) if setting_name else False


def _tcp_port_open(host: str, port: int, timeout_seconds: float) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_seconds):
            return True
    except (OSError, ValueError):
        return False


def probe_common_ports(
    host: str,
    *,
    ports: list[int] | tuple[int, ...] = DEFAULT_QUICK_PORTS,
    timeout_seconds: float = 1.5,
) -> dict[int, bool]:
    normalized_ports = list(dict.fromkeys(int(port) for port in ports if 0 < int(port) <= 65535))
    timeout = max(0.2, min(3.0, float(timeout_seconds or 1.5)))
    if not normalized_ports:
        return {}
    workers = min(8, len(normalized_ports))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="device-port-probe") as executor:
        states = list(executor.map(lambda port: _tcp_port_open(host, port, timeout), normalized_ports))
    return dict(zip(normalized_ports, states))


def _rtsp_probe_order(open_ports: set[int], onvif_brand: str = "") -> list[tuple[str, str, str]]:
    order: list[str] = []
    if onvif_brand and onvif_brand in NVR_RTSP_TEMPLATES and onvif_brand not in order:
        order.append(onvif_brand)
    if 37777 in open_ports and "dahua" not in order:
        order.append("dahua")
    if 8000 in open_ports and "hikvision" not in order:
        order.append("hikvision")
    for brand in ("dahua", "hikvision", "intelbras"):
        if brand not in order:
            order.append(brand)

    candidates: list[tuple[str, str, str]] = []
    seen_paths: set[str] = set()
    for brand in order:
        for stream_kind, template in NVR_RTSP_TEMPLATES.get(brand, ()):
            if stream_kind != "main":
                continue
            path = template.format(channel=1)
            if path in seen_paths:
                continue
            seen_paths.add(path)
            candidates.append((brand, brand, path))
    # Templates genericos cobrem equipamentos fora da familia Dahua/Hikvision
    # que nao respondem ONVIF GetDeviceInformation.
    for stream_kind, template in NVR_RTSP_TEMPLATES.get("generic", ()):
        if stream_kind != "main":
            continue
        path = template.format(channel=1)
        if path in seen_paths:
            continue
        seen_paths.add(path)
        candidates.append(("generic", "generic", path))
    return candidates


def _quick_rtsp_test(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    open_ports: set[int],
    timeout_seconds: float,
    onvif_brand: str = "",
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for brand, label, path in _rtsp_probe_order(open_ports, onvif_brand)[:4]:
        url = build_rtsp_url(host, port, path, username, password)
        details = probe_rtsp_url_details_bounded(
            url,
            timeout_seconds=timeout_seconds,
            transport="tcp",
        )
        error = str(details.get("error") or "")
        attempt = {
            "brand": brand,
            "label": label,
            "ok": bool(details.get("ok")),
            "timed_out": bool(details.get("timed_out")),
            "error": error,
        }
        attempts.append(attempt)
        if attempt["ok"]:
            return {"tested": True, "ok": True, "brand": brand, "error": "", "attempts": attempts}
        # Uma autenticacao rejeitada vale para o dispositivo inteiro. Parar
        # imediatamente evita repetir senha e acionar bloqueio do NVR.
        if "401" in error or "senha" in error.lower() or "authentication" in error.lower():
            return {
                "tested": True,
                "ok": False,
                "brand": brand,
                "authentication_failed": True,
                "error": error,
                "attempts": attempts,
            }
    return {
        "tested": True,
        "ok": False,
        "brand": None,
        "authentication_failed": False,
        "error": attempts[-1]["error"] if attempts else "",
        "attempts": attempts,
    }


def _driver_candidates(
    open_ports: set[int],
    rtsp_ports: set[int],
    onvif_device: OnvifNetworkDevice | None = None,
) -> list[DriverCandidate]:
    native_missing = "Porta detectada; instale/configure o gateway SDK para login e video nativos."
    return [
        DriverCandidate(
            driver_id="dahua_sdk",
            label="Dahua NetSDK",
            brand="dahua",
            provider_type="dahua_sdk",
            port=37777,
            protocol="Dahua TCP/NetSDK",
            detected=37777 in open_ports,
            ready=_native_gateway_ready("dahua_sdk"),
            detail="Gateway NetSDK configurado." if _native_gateway_ready("dahua_sdk") else native_missing,
        ),
        DriverCandidate(
            driver_id="intelbras_sdk",
            label="Intelbras compativel Dahua",
            brand="intelbras",
            provider_type="intelbras_sdk",
            port=37777,
            protocol="Intelbras/Dahua TCP",
            detected=37777 in open_ports,
            ready=_native_gateway_ready("intelbras_sdk"),
            detail="Gateway Intelbras configurado." if _native_gateway_ready("intelbras_sdk") else native_missing,
        ),
        DriverCandidate(
            driver_id="hikvision_sdk",
            label="Hikvision HCNetSDK",
            brand="hikvision",
            provider_type="hikvision_sdk",
            port=8000,
            protocol="Hikvision SDK",
            detected=8000 in open_ports,
            ready=_native_gateway_ready("hikvision_sdk"),
            detail="Gateway HCNetSDK configurado." if _native_gateway_ready("hikvision_sdk") else native_missing,
        ),
        DriverCandidate(
            driver_id="rtsp",
            label="RTSP generico",
            brand="generic",
            provider_type="generic_nvr",
            port=min(rtsp_ports) if rtsp_ports else 554,
            protocol="RTSP",
            detected=bool(rtsp_ports),
            ready=bool(rtsp_ports),
            detail="Servico RTSP acessivel." if rtsp_ports else "Servico RTSP nao detectado.",
        ),
        DriverCandidate(
            driver_id="onvif",
            label="ONVIF/HTTP",
            brand=(_brand_from_manufacturer(onvif_device.manufacturer) if onvif_device else "") or "generic",
            provider_type="generic_nvr",
            port=onvif_device.port if onvif_device else 80,
            protocol="ONVIF/HTTP",
            detected=onvif_device is not None,
            ready=onvif_device is not None,
            detail=(
                f"{onvif_device.manufacturer} {onvif_device.model}".strip()
                if onvif_device and (onvif_device.manufacturer or onvif_device.model)
                else (
                    "ONVIF respondeu, mas nao informou fabricante/modelo."
                    if onvif_device
                    else (
                        "Porta HTTP/ONVIF acessivel, mas GetDeviceInformation nao respondeu."
                        if open_ports.intersection({80, 443, 8080, 8899})
                        else "ONVIF nao detectado."
                    )
                )
            ),
        ),
    ]


def _probe_onvif_device(
    host: str,
    candidate_ports: list[int],
    open_ports: set[int],
    *,
    timeout_seconds: float,
) -> OnvifNetworkDevice | None:
    fallback: OnvifNetworkDevice | None = None
    for port in candidate_ports:
        if port not in open_ports:
            continue
        try:
            candidate = probe_onvif_endpoint(host, port, timeout_seconds=timeout_seconds)
        except Exception:
            candidate = None
        if candidate is None:
            continue
        if candidate.manufacturer or candidate.model:
            return candidate
        if fallback is None:
            fallback = candidate
    return fallback


def detect_common_video_device(
    *,
    host: str,
    username: str = "",
    password: str = "",
    rtsp_port: int = 554,
    onvif_port: int | None = None,
    port_timeout_seconds: float = 1.5,
    stream_timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    started_at = time.monotonic()
    normalized_host = str(host or "").strip()
    if not normalized_host:
        raise ValueError("Informe o IP/host do dispositivo.")

    configured_rtsp_port = max(1, min(65535, int(rtsp_port or 554)))
    onvif_candidate_ports: list[int] = []
    if onvif_port:
        normalized_onvif_port = max(1, min(65535, int(onvif_port)))
        onvif_candidate_ports.append(normalized_onvif_port)
    for port in ONVIF_CANDIDATE_PORTS:
        if port not in onvif_candidate_ports:
            onvif_candidate_ports.append(port)

    ports = list(DEFAULT_QUICK_PORTS)
    for port in [configured_rtsp_port, *onvif_candidate_ports]:
        if port not in ports:
            ports.append(port)
    port_states = probe_common_ports(
        normalized_host,
        ports=ports,
        timeout_seconds=port_timeout_seconds,
    )
    open_ports = {port for port, is_open in port_states.items() if is_open}
    rtsp_ports = {
        port
        for port in {configured_rtsp_port, 554}
        if port in open_ports and port not in NATIVE_SDK_PORTS
    }

    onvif_device = _probe_onvif_device(
        normalized_host,
        onvif_candidate_ports,
        open_ports,
        timeout_seconds=min(2.0, port_timeout_seconds),
    )
    onvif_brand = _brand_from_manufacturer(onvif_device.manufacturer) if onvif_device else ""

    rtsp_result: dict[str, Any] = {"tested": False, "ok": False, "attempts": []}
    if rtsp_ports and (username or password):
        selected_rtsp_port = configured_rtsp_port if configured_rtsp_port in rtsp_ports else min(rtsp_ports)
        rtsp_result = _quick_rtsp_test(
            host=normalized_host,
            port=selected_rtsp_port,
            username=username,
            password=password,
            open_ports=open_ports,
            timeout_seconds=max(2.0, min(8.0, float(stream_timeout_seconds or 5.0))),
            onvif_brand=onvif_brand,
        )
    else:
        selected_rtsp_port = configured_rtsp_port if configured_rtsp_port in rtsp_ports else (min(rtsp_ports) if rtsp_ports else 554)

    drivers = _driver_candidates(open_ports, rtsp_ports, onvif_device)
    recommendation: dict[str, Any]
    if onvif_device is not None and (onvif_device.manufacturer or onvif_device.model):
        brand = onvif_brand or "generic"
        identity = " ".join(part for part in (onvif_device.manufacturer, onvif_device.model) if part).strip()
        recommendation = {
            "brand": brand,
            "provider_type": "generic_nvr",
            "rtsp_port": selected_rtsp_port,
            "onvif_port": onvif_device.port,
            "confidence": "high",
            "ready": True,
            "manufacturer_raw": onvif_device.manufacturer,
            "model": onvif_device.model,
            "reason": f"Identificado via ONVIF GetDeviceInformation: {identity or 'fabricante nao informado pelo dispositivo'}.",
        }
    elif rtsp_result.get("ok"):
        detected_brand = str(rtsp_result.get("brand") or "generic")
        recommendation = {
            "brand": detected_brand,
            "provider_type": "generic_nvr",
            "rtsp_port": selected_rtsp_port,
            "onvif_port": onvif_device.port if onvif_device else None,
            "confidence": "high",
            "ready": True,
            "reason": f"Frame RTSP aberto com o template {detected_brand}.",
        }
    elif rtsp_result.get("authentication_failed"):
        recommendation = {
            "brand": str(rtsp_result.get("brand") or "generic"),
            "provider_type": "generic_nvr",
            "rtsp_port": selected_rtsp_port,
            "onvif_port": onvif_device.port if onvif_device else None,
            "confidence": "high",
            "ready": False,
            "reason": "O servico RTSP respondeu, mas recusou o usuario ou a senha; nenhuma nova tentativa foi feita.",
        }
    elif 37777 in open_ports:
        ready = _native_gateway_ready("dahua_sdk") or _native_gateway_ready("intelbras_sdk")
        recommendation = {
            "brand": "dahua",
            "provider_type": "dahua_sdk",
            "rtsp_port": selected_rtsp_port,
            "onvif_port": onvif_device.port if onvif_device else None,
            "sdk_port": 37777,
            "confidence": "medium",
            "ready": ready,
            "reason": (
                "Porta Dahua/Intelbras 37777 detectada."
                if ready
                else "Porta 37777 detectada, mas o gateway NetSDK ainda nao esta instalado."
            ),
        }
    elif 8000 in open_ports:
        ready = _native_gateway_ready("hikvision_sdk")
        recommendation = {
            "brand": "hikvision",
            "provider_type": "hikvision_sdk",
            "rtsp_port": selected_rtsp_port,
            "onvif_port": onvif_device.port if onvif_device else None,
            "sdk_port": 8000,
            "confidence": "medium",
            "ready": ready,
            "reason": (
                "Porta Hikvision 8000 detectada."
                if ready
                else "Porta 8000 detectada, mas o gateway HCNetSDK ainda nao esta instalado."
            ),
        }
    elif onvif_device is not None:
        recommendation = {
            "brand": "generic",
            "provider_type": "generic_nvr",
            "rtsp_port": selected_rtsp_port,
            "onvif_port": onvif_device.port,
            "confidence": "medium",
            "ready": True,
            "reason": (
                f"ONVIF respondeu na porta {onvif_device.port}, mas nao foi possivel ler "
                "fabricante/modelo (autenticacao ou API restrita)."
            ),
        }
    elif rtsp_ports:
        recommendation = {
            "brand": "generic",
            "provider_type": "generic_nvr",
            "rtsp_port": selected_rtsp_port,
            "onvif_port": None,
            "confidence": "low",
            "ready": True,
            "reason": "RTSP acessivel, mas o template nao foi confirmado no teste rapido.",
        }
    else:
        recommendation = {
            "brand": "generic",
            "provider_type": "generic_nvr",
            "rtsp_port": configured_rtsp_port,
            "onvif_port": None,
            "confidence": "low",
            "ready": False,
            "reason": "Nenhum servico de video conhecido foi confirmado.",
        }

    return {
        "host": normalized_host,
        "elapsed_ms": round((time.monotonic() - started_at) * 1000, 1),
        "open_ports": sorted(open_ports),
        "ports": [{"port": port, "open": bool(port_states[port])} for port in sorted(port_states)],
        "drivers": [asdict(driver) for driver in drivers],
        "rtsp": rtsp_result,
        "recommendation": recommendation,
    }
