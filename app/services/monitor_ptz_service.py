"""Despacho de PTZ para o monitor operacional.

Prioridade:
1. ONVIF (ContinuousMove real, sessao reaproveitada, baixa latencia).
2. SDK nativo por fabricante, somente quando a camera realmente nao expoe
   PTZ via ONVIF (sem servico PTZ, ou a conexao ONVIF falhou). O SDK nativo
   sobe um processo por comando (ver hik_sdk_lab_service._run_worker), entao
   evitamos usa-lo como caminho padrao.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.analytics.camera_profile_models import profile_from_camera
from app.db.base import SessionLocal
from app.db.models import Camera, CameraPtzProfile
from app.services import hik_sdk_lab_service as sdk_lab
from app.services.device_session_service import (
    DeviceCapabilityError,
    DeviceSessionError,
    describe_device as describe_onvif_device,
    ptz_continuous_move as onvif_ptz_move,
    ptz_goto_preset as onvif_goto_preset,
    ptz_stop as onvif_ptz_stop,
)


SDK_BRAND_PORTS = {
    "hikvision": 8000,
    "dahua": 37777,
    # Intelbras usa CGI HTTP neste projeto. Quando nao ha porta customizada,
    # o destino correto e a porta HTTP, e nao a porta NetSDK 37777.
    "intelbras": 80,
}
SDK_PROVIDER_HINTS = {
    "hikvision_sdk": "hikvision",
    "hikvision_nvr": "hikvision",
    "hikvision_isapi": "hikvision",
    "hikvision": "hikvision",
    "dahua_sdk": "dahua",
    "dahua_nvr": "dahua",
    "dahua_http": "dahua",
    "dahua_cgi": "dahua",
    "dahua_tcp": "dahua",
    "dahua": "dahua",
    "intelbras_sdk": "intelbras",
    "intelbras_nvr": "intelbras",
    "intelbras_http": "intelbras",
    "intelbras": "intelbras",
}


@dataclass(slots=True)
class _SdkSessionEntry:
    token: str
    owner_id: int
    key: tuple[int, int, str, str, int, int]
    created_at: float
    last_used_at: float


_sdk_sessions: dict[tuple[int, int, str, str, int, int], _SdkSessionEntry] = {}
_sdk_sessions_lock = threading.Lock()

# Evita retestar uma conexao ONVIF quebrada a cada comando durante um
# arraste de joystick ou repeticao do teclado (a cada ~200ms): depois de uma
# falha de conexao, comandos seguintes pulam direto para o SDK nativo por
# um curto periodo.
_ONVIF_FAILURE_TTL_SECONDS = 30.0
_onvif_connect_failures: dict[int, float] = {}
_onvif_failures_lock = threading.Lock()
_probe_camera_ids: set[int] = set()
_probe_lock = threading.Lock()
_active_movements: dict[tuple[int, int], dict[str, Any]] = {}
_active_movements_lock = threading.Lock()
_preferred_backend_cache: dict[int, tuple[str, str]] = {}
_preferred_backend_cache_lock = threading.Lock()

_PTZ_PROFILE_MAX_AGE = timedelta(days=7)
_PTZ_RETRY_DELAY = timedelta(minutes=5)
_PTZ_ERROR_LIMIT = 500

PTZ_STATUS_LABELS = {
    "unknown": "PTZ nao avaliado",
    "probing": "Verificando PTZ...",
    "controllable": "PTZ disponivel",
    "not_controllable": "Sem capacidade PTZ",
    "unavailable": "PTZ temporariamente indisponivel",
    "stale": "Diagnostico PTZ desatualizado",
}


def _onvif_recently_failed(camera_id: int) -> bool:
    with _onvif_failures_lock:
        failed_at = _onvif_connect_failures.get(int(camera_id))
    return failed_at is not None and (time.time() - failed_at) < _ONVIF_FAILURE_TTL_SECONDS


def _remember_onvif_failure(camera_id: int) -> None:
    with _onvif_failures_lock:
        _onvif_connect_failures[int(camera_id)] = time.time()


def _clear_onvif_failure(camera_id: int) -> None:
    with _onvif_failures_lock:
        _onvif_connect_failures.pop(int(camera_id), None)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _camera_brand(camera: Camera) -> str:
    provider = _normalize_text(getattr(camera, "source_provider", None))
    if provider in SDK_PROVIDER_HINTS:
        return SDK_PROVIDER_HINTS[provider]

    source_brand = _normalize_text(getattr(camera, "source_brand", None))
    manufacturer = _normalize_text(getattr(camera, "manufacturer", None))
    name = _normalize_text(getattr(camera, "name", None))
    rtsp_url = _normalize_text(getattr(camera, "rtsp_url", None))

    for candidate in (provider, source_brand, manufacturer, name, rtsp_url):
        if not candidate:
            continue
        if "hikvision" in candidate or candidate == "hik" or "hik_" in candidate or "hik/" in candidate:
            return "hikvision"
        if "intelbras" in candidate:
            return "intelbras"
        if "dahua" in candidate or "dh-" in candidate or "dh_" in candidate or "dh/" in candidate:
            return "dahua"
    return ""


def _camera_channel(camera: Camera) -> int:
    try:
        channel = int(getattr(camera, "source_channel", None) or 1)
    except (TypeError, ValueError):
        channel = 1
    return max(1, min(4096, channel))


def _camera_declared_ptz(camera: Camera) -> bool:
    """Retorna somente uma declaracao explicita, nunca uma heuristica de marca."""

    try:
        profile = profile_from_camera(camera)
        if str(getattr(profile, "camera_family", "") or "").strip().lower() in {"ptz", "speed_dome"}:
            return True
        overrides = getattr(profile, "manual_overrides", None) or {}
        return overrides.get("ptz_enabled") is True
    except Exception:
        return False


def _camera_sdk_port(brand: str) -> int:
    return SDK_BRAND_PORTS.get(brand, 0)


def _sdk_available(brand: str) -> bool:
    try:
        return sdk_lab.sdk_available(brand)
    except Exception:
        return False


def _to_dir(val: float) -> int:
    if val > 0.05:
        return 1
    if val < -0.05:
        return -1
    return 0


# O frontend ja multiplica pan/tilt/zoom pelo slider de velocidade (0.1-1.0)
# antes de mandar pro backend -- isso alimenta corretamente o Velocity do
# ONVIF, mas o SDK nativo e o Dahua HTTP usam um campo "speed" (1-7) separado
# da direcao. Sem isso, esse campo ficava sempre no default e a regulagem de
# velocidade nao tinha efeito nenhum fora do ONVIF.
_NATIVE_PTZ_SPEED_MIN = 1
_NATIVE_PTZ_SPEED_MAX = 7


def _speed_from_magnitude(pan: float, tilt: float, zoom: float) -> int:
    magnitude = min(1.0, max(abs(pan), abs(tilt), abs(zoom), 0.0))
    return max(_NATIVE_PTZ_SPEED_MIN, round(magnitude * _NATIVE_PTZ_SPEED_MAX))


def _native_sdk_candidate(camera: Camera) -> dict[str, Any] | None:
    brand = _camera_brand(camera)
    if not brand:
        return None

    # Usa porta customizada somente se definida e se nao for porta ONVIF/HTTP/RTSP generica ou de outra marca
    onvif_port = int(getattr(camera, "onvif_port", None) or 0)
    ignored_ports = {80, 8080, 88, 554}
    if brand == "dahua":
        ignored_ports.add(8000)
    elif brand == "hikvision":
        ignored_ports.add(37777)

    if onvif_port and onvif_port not in ignored_ports:
        port = onvif_port
    else:
        port = _camera_sdk_port(brand)

    if not port or not _sdk_available(brand):
        return None

    return {
        "backend": "native_sdk",
        "brand": brand,
        "port": port,
        "channel": _camera_channel(camera),
    }


def _onvif_candidate(camera: Camera) -> dict[str, Any]:
    return {
        "backend": "onvif",
        "brand": _normalize_text(getattr(camera, "manufacturer", None)) or "generic",
        "port": int(getattr(camera, "onvif_port", None) or 80),
        "channel": 1,
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _configuration_fingerprint(camera: Camera) -> str:
    """Identifica alteracoes que tornam uma descoberta PTZ anterior invalida."""

    parts = (
        getattr(camera, "ip", None),
        getattr(camera, "onvif_port", None),
        getattr(camera, "username", None),
        getattr(camera, "manufacturer", None),
        getattr(camera, "source_brand", None),
        getattr(camera, "source_provider", None),
        getattr(camera, "source_channel", None),
        _camera_declared_ptz(camera),
    )
    raw = "\x1f".join(str(value or "").strip().lower() for value in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stored_backend_name(payload: dict[str, Any]) -> str:
    if str(payload.get("backend") or "") == "onvif":
        return "onvif"
    brand = _normalize_text(payload.get("sdk_brand") or (payload.get("device") or {}).get("manufacturer"))
    if brand == "intelbras":
        return "intelbras_http"
    if brand == "dahua":
        return "dahua_sdk"
    if brand == "hikvision":
        return "hikvision_sdk"
    return "native_sdk"


def _backend_label(backend: str | None) -> str:
    return {
        "onvif": "ONVIF",
        "intelbras_http": "Intelbras HTTP",
        "dahua_sdk": "Dahua NetSDK",
        "hikvision_sdk": "Hikvision HCNetSDK",
        "native_sdk": "SDK nativo",
    }.get(str(backend or ""), "")


def ptz_profile_payload(profile: CameraPtzProfile | None) -> dict[str, Any]:
    if profile is None:
        return {
            "status": "unknown",
            "status_label": PTZ_STATUS_LABELS["unknown"],
            "ptz_capable": False,
            "capabilities": {"ptz": False},
        }
    backend = str(profile.selected_backend or "")
    status = str(profile.status or "unknown")
    label = PTZ_STATUS_LABELS.get(status, PTZ_STATUS_LABELS["unknown"])
    backend_label = _backend_label(backend)
    if status == "controllable" and backend_label:
        label = f"PTZ disponivel · {backend_label}"
    diagnostics: dict[str, Any] = {}
    if profile.diagnostics_json:
        try:
            parsed_diagnostics = json.loads(profile.diagnostics_json)
            if isinstance(parsed_diagnostics, dict):
                diagnostics = parsed_diagnostics
        except (TypeError, ValueError, json.JSONDecodeError):
            diagnostics = {"parse_error": True}
    return {
        "status": status,
        "status_label": label,
        "ptz_capable": status == "controllable",
        "capabilities": {
            "ptz": status == "controllable",
            "pan_tilt": bool(profile.supports_pan_tilt),
            "zoom": bool(profile.supports_zoom),
            "presets": bool(profile.supports_presets),
        },
        "backend": backend,
        "backend_label": backend_label,
        "detected_brand": profile.detected_brand or "",
        "control_port": profile.control_port,
        "channel": profile.channel,
        "continuous": bool(profile.continuous_move),
        "failure_count": int(profile.failure_count or 0),
        "last_error_category": profile.last_error_category or "",
        "last_error": profile.last_error or "",
        "reason": profile.last_error or label,
        "diagnostics": diagnostics,
        "last_verified_at": profile.last_verified_at.isoformat() if profile.last_verified_at else None,
        "last_success_at": profile.last_success_at.isoformat() if profile.last_success_at else None,
        "next_retry_at": profile.next_retry_at.isoformat() if profile.next_retry_at else None,
    }


def load_ptz_profile_map(cameras_or_ids) -> dict[int, dict[str, Any]]:
    camera_by_id: dict[int, Camera] = {}
    ids: list[int] = []
    for item in cameras_or_ids:
        if isinstance(item, Camera):
            camera_id = int(item.id)
            camera_by_id[camera_id] = item
        else:
            camera_id = int(item)
        ids.append(camera_id)
    ids = sorted(set(ids))
    if not ids:
        return {}
    db = SessionLocal()
    try:
        profiles = db.query(CameraPtzProfile).filter(CameraPtzProfile.camera_id.in_(ids)).all()
        now = _utcnow()
        changed = False
        result: dict[int, dict[str, Any]] = {}
        for profile in profiles:
            camera_id = int(profile.camera_id)
            camera = camera_by_id.get(camera_id)
            verified_at = profile.last_verified_at
            if verified_at is not None and verified_at.tzinfo is None:
                verified_at = verified_at.replace(tzinfo=timezone.utc)
            expired = verified_at is not None and now - verified_at > _PTZ_PROFILE_MAX_AGE
            changed_configuration = (
                camera is not None
                and profile.configuration_fingerprint != _configuration_fingerprint(camera)
            )
            if profile.status in {"controllable", "not_controllable"} and (expired or changed_configuration):
                profile.status = "stale"
                changed = True
            result[camera_id] = ptz_profile_payload(profile)
        if changed:
            db.commit()
        return result
    except SQLAlchemyError:
        return {}
    finally:
        db.close()


def _load_valid_profile(camera: Camera) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        profile = db.query(CameraPtzProfile).filter(CameraPtzProfile.camera_id == int(camera.id)).first()
        if profile is None or profile.status != "controllable":
            return None
        if profile.configuration_fingerprint != _configuration_fingerprint(camera):
            profile.status = "stale"
            db.commit()
            return None
        verified_at = profile.last_verified_at
        if verified_at is not None:
            if verified_at.tzinfo is None:
                verified_at = verified_at.replace(tzinfo=timezone.utc)
            if _utcnow() - verified_at > _PTZ_PROFILE_MAX_AGE:
                profile.status = "stale"
                db.commit()
                return None
        return ptz_profile_payload(profile)
    except SQLAlchemyError:
        return None
    finally:
        db.close()


def invalidate_ptz_profile(camera_id: int) -> None:
    with _preferred_backend_cache_lock:
        _preferred_backend_cache.pop(int(camera_id), None)
    db = SessionLocal()
    try:
        profile = db.query(CameraPtzProfile).filter(CameraPtzProfile.camera_id == int(camera_id)).first()
        if profile is not None:
            profile.status = "stale"
            profile.next_retry_at = None
            db.commit()
    except SQLAlchemyError:
        db.rollback()
    finally:
        db.close()


def _save_profile(
    camera: Camera,
    *,
    status: str,
    payload: dict[str, Any] | None = None,
    error: Exception | None = None,
    error_category: str | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        profile = db.query(CameraPtzProfile).filter(CameraPtzProfile.camera_id == int(camera.id)).first()
        if profile is None:
            profile = CameraPtzProfile(camera_id=int(camera.id))
            db.add(profile)
        now = _utcnow()
        previous_status = str(profile.status or "")
        profile.status = status
        profile.detected_brand = _camera_brand(camera) or _normalize_text(getattr(camera, "manufacturer", None)) or None
        profile.configuration_fingerprint = _configuration_fingerprint(camera)
        profile.last_verified_at = now
        if payload is not None:
            capabilities = dict(payload.get("capabilities") or {})
            backend = _stored_backend_name(payload)
            previous_backend = str(profile.selected_backend or "")
            profile.fallback_backend = previous_backend if previous_backend and previous_backend != backend else profile.fallback_backend
            profile.selected_backend = backend
            profile.control_port = int(payload.get("sdk_port") or payload.get("port") or getattr(camera, "onvif_port", 80) or 80)
            profile.channel = 1 if backend == "onvif" else int(payload.get("sdk_channel") or _camera_channel(camera))
            profile.profile_token = str(payload.get("profile_token") or "") or None
            profile.supports_pan_tilt = bool(capabilities.get("ptz") or payload.get("ptz_capable"))
            profile.supports_zoom = bool(capabilities.get("zoom", profile.supports_pan_tilt))
            profile.supports_presets = bool(payload.get("presets") or capabilities.get("presets"))
            profile.continuous_move = bool(payload.get("continuous", True))
            diagnostics = payload.get("diagnostics")
            if isinstance(diagnostics, dict):
                encoded_diagnostics = json.dumps(
                    diagnostics,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                if len(encoded_diagnostics) > 16000:
                    encoded_diagnostics = json.dumps(
                        {
                            "truncated": True,
                            "raw_prefix": encoded_diagnostics[:15500],
                        },
                        ensure_ascii=False,
                    )
                profile.diagnostics_json = encoded_diagnostics
            profile.failure_count = 0 if status == "controllable" else 1
            profile.last_error = None if status == "controllable" else (payload.get("reason") or "Sem suporte a PTZ confirmado")
            profile.last_error_category = None if status == "controllable" else "capability"
            profile.next_retry_at = None
            if status == "controllable":
                profile.last_success_at = now
        elif error is not None:
            profile.failure_count = int(profile.failure_count or 0) + 1
            profile.last_error_category = error_category or type(error).__name__
            profile.last_error = str(error)[:_PTZ_ERROR_LIMIT]
            profile.next_retry_at = now + _PTZ_RETRY_DELAY
            # Uma falha pontual de comando não apaga uma descoberta válida.
            # Manter o backend selecionado evita que o próximo clique tente
            # ONVIF em uma porta de SDK (37777/8000) e acumule timeouts.
            if (
                error_category == "control"
                and previous_status == "controllable"
                and profile.selected_backend
            ):
                profile.status = "controllable"
        db.commit()
        db.refresh(profile)
        if profile.status == "controllable" and profile.selected_backend:
            with _preferred_backend_cache_lock:
                _preferred_backend_cache[int(camera.id)] = (
                    str(profile.configuration_fingerprint or ""),
                    str(profile.selected_backend),
                )
        return ptz_profile_payload(profile)
    except SQLAlchemyError:
        db.rollback()
        return {
            "status": status,
            "status_label": PTZ_STATUS_LABELS.get(status, status),
            "ptz_capable": status == "controllable",
            "capabilities": {"ptz": status == "controllable"},
        }
    finally:
        db.close()


def _mark_probing(camera_id: int, *, force: bool = False) -> tuple[dict[str, Any], bool]:
    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.id == int(camera_id), Camera.is_deleted == False).first()  # noqa: E712
        if camera is None:
            raise DeviceSessionError("Camera nao encontrada.")
        profile = db.query(CameraPtzProfile).filter(CameraPtzProfile.camera_id == int(camera_id)).first()
        fingerprint = _configuration_fingerprint(camera)
        if profile is not None and not force:
            valid_fingerprint = profile.configuration_fingerprint == fingerprint
            if profile.status in {"controllable", "not_controllable"} and valid_fingerprint:
                verified_at = profile.last_verified_at
                if verified_at is None or (
                    _utcnow() - (verified_at if verified_at.tzinfo else verified_at.replace(tzinfo=timezone.utc))
                    <= _PTZ_PROFILE_MAX_AGE
                ):
                    return ptz_profile_payload(profile), False
            if profile.status == "probing":
                return ptz_profile_payload(profile), False
            if profile.next_retry_at is not None:
                retry_at = profile.next_retry_at
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                if retry_at > _utcnow():
                    return ptz_profile_payload(profile), False
        if profile is None:
            profile = CameraPtzProfile(camera_id=int(camera_id))
            db.add(profile)
        profile.status = "probing"
        profile.detected_brand = _camera_brand(camera) or None
        profile.configuration_fingerprint = fingerprint
        profile.last_error = None
        profile.last_error_category = None
        db.commit()
        db.refresh(profile)
        with _probe_lock:
            if int(camera_id) in _probe_camera_ids:
                return ptz_profile_payload(profile), False
            _probe_camera_ids.add(int(camera_id))
        return ptz_profile_payload(profile), True
    finally:
        db.close()


def prepare_ptz_inspection(camera_id: int, *, force: bool = False) -> tuple[dict[str, Any], bool]:
    """Retorna o estado imediatamente e informa se uma descoberta deve ser agendada."""

    try:
        return _mark_probing(camera_id, force=force)
    except SQLAlchemyError:
        # Compatibilidade durante atualizacao: sem tabela, o chamador ainda
        # pode usar a descoberta sincrona legada.
        return ptz_profile_payload(None), True


def discover_and_persist_ptz(camera_id: int, owner_id: int) -> dict[str, Any]:
    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.id == int(camera_id), Camera.is_deleted == False).first()  # noqa: E712
        if camera is None:
            raise DeviceSessionError("Camera nao encontrada.")
        try:
            payload = _describe_ptz_uncached(camera, owner_id=owner_id)
            capable = bool(payload.get("ptz_capable") or (payload.get("capabilities") or {}).get("ptz"))
            return _save_profile(camera, status="controllable" if capable else "not_controllable", payload=payload)
        except DeviceCapabilityError as exc:
            return _save_profile(camera, status="not_controllable", error=exc, error_category="capability")
        except Exception as exc:
            return _save_profile(camera, status="unavailable", error=exc, error_category="connection")
    finally:
        db.close()
        with _probe_lock:
            _probe_camera_ids.discard(int(camera_id))


def resolve_ptz_backend(camera: Camera) -> dict[str, Any]:
    native = _native_sdk_candidate(camera)
    if native is not None:
        return native
    return _onvif_candidate(camera)


def _native_describe(camera: Camera, owner_id: int, native: dict[str, Any], *, reason: str) -> dict[str, Any]:
    device_info = {
        "manufacturer": native["brand"],
        "host": getattr(camera, "ip", None),
        "port": native["port"],
        "channel": native["channel"],
    }
    session = _get_or_create_sdk_session(camera, owner_id, native["brand"], native["port"], native["channel"])
    if getattr(session, "device", None):
        device_info.update(session.device)
    capability_verified = bool(device_info.get("ptz_capability_verified"))
    # Alguns NVRs Dahua reportam ``ptz_capable=false`` no bloco geral do
    # canal, mesmo expondo corretamente os recursos pan/zoom (e aceitando
    # CLIENT_DHPTZControlEx2). Nesse caso, os recursos individuais são a
    # evidência mais confiável para a avaliação do PTZ.
    individual_ptz = bool(device_info.get("ptz_pan") or device_info.get("ptz_zoom"))
    declared_protocol_ptz = bool(
        native["brand"] == "dahua"
        and _camera_declared_ptz(camera)
        and device_info.get("ptz_protocol_motion")
    )
    ptz_capable = capability_verified and bool(
        device_info.get("ptz_capable") or individual_ptz or declared_protocol_ptz
    )
    if declared_protocol_ptz:
        device_info["ptz_declared_by_camera_profile"] = True
    diagnostics = {
        "brand": native["brand"],
        "channel": native["channel"],
        "port": native["port"],
        "ptz_capability_verified": capability_verified,
        "ptz_capable": bool(device_info.get("ptz_capable")),
        "ptz_protocol_motion": bool(device_info.get("ptz_protocol_motion")),
        "physical_ptz": bool(device_info.get("physical_ptz")),
        "motorized_input": bool(device_info.get("motorized_input")),
        "ptz_declared_by_camera_profile": declared_protocol_ptz,
        "ptz_protocol_caps": device_info.get("ptz_protocol_caps") or {},
        "video_input_caps": device_info.get("video_input_caps") or {},
    }
    pan_tilt = ptz_capable and bool(device_info.get("ptz_pan", True))
    zoom = ptz_capable and bool(device_info.get("ptz_zoom", True))

    return {
        "backend": "native_sdk",
        "ptz_capable": ptz_capable,
        "capabilities": {
            "ptz": ptz_capable,
            "pan_tilt": pan_tilt,
            "zoom": zoom,
        },
        "device": device_info,
        "diagnostics": diagnostics,
        "profiles": [],
        "profile_token": "",
        "endpoint": f"{camera.ip}:{native['port']}",
        "sdk_brand": native["brand"],
        "sdk_port": native["port"],
        "sdk_channel": native["channel"],
        "reason": (
            reason
            if ptz_capable
            else "O SDK conectou, mas nao confirmou mecanismo PTZ neste canal."
        ),
    }


def _describe_ptz_uncached(camera: Camera, *, owner_id: int) -> dict[str, Any]:
    camera_id = int(camera.id)
    native = _native_sdk_candidate(camera)
    test_logs: list[str] = []

    onvif_payload: dict[str, Any] | None = None
    onvif_error: Exception | None = None
    onvif_port = getattr(camera, "onvif_port", 80) or 80
    skip_onvif = native is not None and (
        _onvif_recently_failed(camera_id) or native["brand"] == "intelbras"
    )
    if not skip_onvif:
        try:
            onvif_payload = describe_onvif_device(camera)
            onvif_payload["backend"] = "onvif"
            is_cap = bool((onvif_payload.get("capabilities") or {}).get("ptz"))
            onvif_payload["ptz_capable"] = is_cap
            if is_cap:
                test_logs.append(f"• Teste ONVIF (porta {onvif_port}): ✔ Sucesso (PTZ verificado).")
                onvif_payload["reason"] = "\n".join(test_logs)
            else:
                test_logs.append(f"• Teste ONVIF (porta {onvif_port}): ✖ Conectou, mas não expõe módulo PTZConfiguration nos perfis de vídeo.")
        except Exception as exc:
            onvif_error = exc
            test_logs.append(f"• Teste ONVIF (porta {onvif_port}): ✖ Falhou ({exc}).")
    else:
        test_logs.append("• Teste ONVIF: ⏩ Ignorado (marca Intelbras ou falha recente de conexão).")

    if onvif_payload is not None and onvif_payload["ptz_capable"]:
        _clear_onvif_failure(camera_id)
        return onvif_payload

    if native is not None:
        _remember_onvif_failure(camera_id)
        brand_name = str(native["brand"]).upper()
        sdk_port = native["port"]
        sdk_chan = native["channel"]
        try:
            native_payload = _native_describe(
                camera,
                owner_id,
                native,
                reason="Verificação nativa executada.",
            )
            native_cap = bool(native_payload.get("ptz_capable"))
            if native_cap:
                test_logs.append(f"• Teste SDK Nativo ({brand_name} porta {sdk_port}, canal {sdk_chan}): ✔ Sucesso (PTZ verificado).")
            else:
                test_logs.append(f"• Teste SDK Nativo ({brand_name} porta {sdk_port}, canal {sdk_chan}): ✖ Conectou, mas nao confirmou mecanismo PTZ neste canal.")

            if native["brand"] == "intelbras" and not bool(
                (native_payload.get("device") or {}).get("ptz_capability_verified")
            ):
                test_logs.append("• API HTTP Intelbras: ✖ Não confirma PTZ por canal. Requer ONVIF para PTZ.")
                raise DeviceSessionError("\n".join(test_logs))

            native_payload["reason"] = "\n".join(test_logs)
            return native_payload
        except Exception as exc:
            err_msg = str(exc)
            if not err_msg.startswith("• Teste"):
                test_logs.append(f"• Teste SDK Nativo ({brand_name} porta {sdk_port}, canal {sdk_chan}): ✖ Falhou ({exc}).")

    final_reason = "\n".join(test_logs) if test_logs else "Nenhum teste de PTZ obteve êxito neste dispositivo (nem via ONVIF nem via SDK nativo)."
    if onvif_payload is not None:
        onvif_payload["reason"] = final_reason
        return onvif_payload
    raise DeviceCapabilityError(f"Nenhum teste de PTZ obteve êxito (nem via ONVIF nem via SDK nativo):\n{final_reason}")


def describe_ptz(camera: Camera, *, owner_id: int, force: bool = False) -> dict[str, Any]:
    if not force:
        cached = _load_valid_profile(camera)
        if cached is not None:
            return cached
    payload = _describe_ptz_uncached(camera, owner_id=owner_id)
    capable = bool(payload.get("ptz_capable") or (payload.get("capabilities") or {}).get("ptz"))
    _save_profile(camera, status="controllable" if capable else "not_controllable", payload=payload)
    return payload


def _move_ptz_legacy(
    camera: Camera,
    *,
    owner_id: int,
    pan: float = 0.0,
    tilt: float = 0.0,
    zoom: float = 0.0,
    duration_ms: int = 300,
) -> dict[str, Any]:
    camera_id = int(camera.id)
    native = _native_sdk_candidate(camera)
    speed = _speed_from_magnitude(pan, tilt, zoom)
    last_error: Exception | None = None

    skip_onvif = native is not None and _onvif_recently_failed(camera_id)
    if not skip_onvif:
        try:
            onvif_ptz_move(camera, pan=pan, tilt=tilt, zoom=zoom)
            _clear_onvif_failure(camera_id)
            return {"backend": "onvif", "ptz_capable": True}
        except (DeviceCapabilityError, DeviceSessionError) as exc:
            if native is not None:
                _remember_onvif_failure(camera_id)
            last_error = exc

    if native is not None:
        try:
            session = _get_or_create_sdk_session(camera, owner_id, native["brand"], native["port"], native["channel"])
            # ptz_hold_start cobre as 3 marcas com start/stop de verdade
            # (HTTP CGI pra Intelbras, worker nativo pra Hikvision/Dahua) --
            # so o continuo real evita a camera "andar um pouco e parar" a
            # cada reenvio do D-pad/joystick. Se nao der (sessao ONVIF-dentro-
            # do-SDK, ou falha pontual), cai no pulso de sempre.
            continuous = sdk_lab.ptz_hold_start(
                session.token, owner_id,
                pan=_to_dir(pan), tilt=_to_dir(tilt), zoom=_to_dir(zoom), speed=speed,
            )
            if not continuous:
                sdk_lab.move_ptz(
                    session.token,
                    owner_id,
                    pan=_to_dir(pan),
                    tilt=_to_dir(tilt),
                    zoom=_to_dir(zoom),
                    speed=speed,
                    duration_ms=duration_ms,
                )
            return {"backend": "native_sdk", "ptz_capable": True, "continuous": continuous}
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise DeviceCapabilityError("Esta camera nao expoe controle PTZ via ONVIF nem via SDK nativo.")


def _stop_ptz_legacy(camera: Camera, *, owner_id: int | None = None) -> dict[str, Any]:
    camera_id = int(camera.id)
    native = _native_sdk_candidate(camera)
    last_error: Exception | None = None

    # Se ha uma sessao SDK ja aberta pra essa camera, tenta parar um
    # movimento continuo nela primeiro -- ptz_hold_stop e um no-op seguro
    # quando nada esta rastreado, entao chamar aqui e barato. Nao criamos uma
    # sessao nova so pra isso (evitaria conectar em cameras que so usam
    # ONVIF); so reaproveita se ja existir.
    if native is not None and owner_id is not None:
        token = _peek_sdk_session_token(camera, owner_id, native["brand"], native["port"], native["channel"])
        if token:
            try:
                sdk_lab.ptz_hold_stop(token, owner_id)
                return {"backend": "native_sdk", "ptz_capable": True, "stopped": True}
            except Exception as exc:
                last_error = exc

    skip_onvif = native is not None and _onvif_recently_failed(camera_id)
    if not skip_onvif:
        try:
            onvif_ptz_stop(camera)
            _clear_onvif_failure(camera_id)
            return {"backend": "onvif", "ptz_capable": True}
        except (DeviceCapabilityError, DeviceSessionError) as exc:
            if native is not None:
                _remember_onvif_failure(camera_id)
            last_error = exc

    if native is not None and owner_id is not None:
        try:
            session = _get_or_create_sdk_session(camera, owner_id, native["brand"], native["port"], native["channel"])
            # Pulso de fallback: os workers executam PTZ por janela de tempo,
            # entao basta manter a sessão viva e devolver sucesso.
            sdk_lab.get_session(session.token, owner_id)
            return {"backend": "native_sdk", "ptz_capable": True, "stopped": True}
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise DeviceCapabilityError("Esta camera nao expoe controle PTZ via ONVIF nem via SDK nativo.")


def _move_onvif(camera: Camera, *, pan: float, tilt: float, zoom: float) -> dict[str, Any]:
    onvif_ptz_move(camera, pan=pan, tilt=tilt, zoom=zoom)
    _clear_onvif_failure(int(camera.id))
    return {"backend": "onvif", "driver": "onvif", "ptz_capable": True, "continuous": True}


def _move_native(
    camera: Camera,
    owner_id: int,
    native: dict[str, Any],
    *,
    pan: float,
    tilt: float,
    zoom: float,
    duration_ms: int,
) -> dict[str, Any]:
    speed = _speed_from_magnitude(pan, tilt, zoom)
    session = _get_or_create_sdk_session(camera, owner_id, native["brand"], native["port"], native["channel"])
    continuous = sdk_lab.ptz_hold_start(
        session.token,
        owner_id,
        pan=_to_dir(pan),
        tilt=_to_dir(tilt),
        zoom=_to_dir(zoom),
        speed=speed,
    )
    if not continuous:
        sdk_lab.move_ptz(
            session.token,
            owner_id,
            pan=_to_dir(pan),
            tilt=_to_dir(tilt),
            zoom=_to_dir(zoom),
            speed=speed,
            duration_ms=duration_ms,
        )
    driver = "intelbras_http" if native["brand"] == "intelbras" else f"{native['brand']}_sdk"
    return {"backend": "native_sdk", "driver": driver, "ptz_capable": True, "continuous": continuous}


def _preferred_backend(camera: Camera) -> str:
    fingerprint = _configuration_fingerprint(camera)
    with _preferred_backend_cache_lock:
        cached = _preferred_backend_cache.get(int(camera.id))
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    cached = _load_valid_profile(camera)
    backend = str((cached or {}).get("backend") or "")
    if not backend:
        # Recupera a preferência mesmo após uma falha transitória registrada
        # por uma versão anterior. A configuração/fingerprint ainda precisa
        # ser a mesma para não reutilizar um diagnóstico obsoleto.
        db = SessionLocal()
        try:
            profile = (
                db.query(CameraPtzProfile)
                .filter(CameraPtzProfile.camera_id == int(camera.id))
                .first()
            )
            if (
                profile is not None
                and profile.configuration_fingerprint == fingerprint
                and profile.selected_backend
            ):
                backend = str(profile.selected_backend)
        except SQLAlchemyError:
            backend = ""
        finally:
            db.close()
    if backend:
        with _preferred_backend_cache_lock:
            _preferred_backend_cache[int(camera.id)] = (fingerprint, backend)
    return backend


def list_ptz_presets(camera: Camera, *, owner_id: int) -> list[dict[str, str]]:
    """Lista somente presets existentes usando o backend PTZ selecionado."""

    preferred = _preferred_backend(camera)
    native_names = {"native_sdk", "intelbras_http", "dahua_sdk", "hikvision_sdk"}
    if preferred in native_names:
        native = _native_sdk_candidate(camera)
        if native is None:
            raise DeviceCapabilityError("O backend SDK salvo nao esta disponivel.")
        session = _get_or_create_sdk_session(
            camera,
            owner_id,
            native["brand"],
            native["port"],
            native["channel"],
        )
        return sdk_lab.list_presets(session.token, owner_id)

    payload = describe_onvif_device(camera)
    if not bool((payload.get("capabilities") or {}).get("ptz")):
        raise DeviceCapabilityError("Esta camera nao expoe presets via ONVIF.")
    presets = payload.get("presets") or []
    return [
        {
            "token": str(item.get("token") or "").strip(),
            "name": str(item.get("name") or item.get("token") or "").strip(),
        }
        for item in presets
        if str(item.get("token") or "").strip()
    ]


def goto_ptz_preset(camera: Camera, *, owner_id: int, preset_token: str) -> dict[str, Any]:
    """Aciona um preset existente sem criar ou alterar configuracao."""

    token = str(preset_token or "").strip()
    if not token:
        raise DeviceCapabilityError("Selecione um preset existente.")

    preferred = _preferred_backend(camera)
    native_names = {"native_sdk", "intelbras_http", "dahua_sdk", "hikvision_sdk"}
    if preferred in native_names:
        native = _native_sdk_candidate(camera)
        if native is None:
            raise DeviceCapabilityError("O backend SDK salvo nao esta disponivel.")
        session = _get_or_create_sdk_session(
            camera,
            owner_id,
            native["brand"],
            native["port"],
            native["channel"],
        )
        sdk_lab.goto_preset(session.token, owner_id, token)
        return {"backend": "native_sdk", "driver": preferred, "preset_token": token}

    onvif_goto_preset(camera, token)
    return {"backend": "onvif", "driver": "onvif", "preset_token": token}


def _record_control_success(camera: Camera, result: dict[str, Any]) -> None:
    native = _native_sdk_candidate(camera)
    used_native = result.get("backend") == "native_sdk"
    driver = str(result.get("driver") or ("native_sdk" if used_native else "onvif"))
    if _preferred_backend(camera) == driver:
        return
    payload = {
        "backend": "onvif" if result.get("backend") == "onvif" else "native_sdk",
        "sdk_brand": _camera_brand(camera) if used_native else None,
        "sdk_port": (native or {}).get("port") if used_native else None,
        "sdk_channel": _camera_channel(camera) if used_native else None,
        "ptz_capable": True,
        "continuous": bool(result.get("continuous", True)),
        "capabilities": {"ptz": True, "zoom": True},
    }
    _save_profile(camera, status="controllable", payload=payload)


def move_ptz(
    camera: Camera,
    *,
    owner_id: int,
    pan: float = 0.0,
    tilt: float = 0.0,
    zoom: float = 0.0,
    duration_ms: int = 300,
) -> dict[str, Any]:
    camera_id = int(camera.id)
    native = _native_sdk_candidate(camera)
    preferred = _preferred_backend(camera)
    native_names = {"native_sdk", "intelbras_http", "dahua_sdk", "hikvision_sdk"}
    # Quando a avaliação já escolheu um SDK nativo, não faça fallback por
    # comando para ONVIF. Além de lento, câmeras/NVRs cadastrados pela porta
    # do SDK podem acabar recebendo SOAP em 37777/8000.
    order = ["native"] if preferred in native_names else ["onvif", "native"]
    last_error: Exception | None = None

    for backend in order:
        if backend == "native":
            if native is None:
                continue
            try:
                result = _move_native(
                    camera,
                    owner_id,
                    native,
                    pan=pan,
                    tilt=tilt,
                    zoom=zoom,
                    duration_ms=duration_ms,
                )
            except Exception as exc:
                last_error = exc
                continue
        else:
            if native is not None and not preferred and _onvif_recently_failed(camera_id):
                continue
            try:
                result = _move_onvif(camera, pan=pan, tilt=tilt, zoom=zoom)
            except (DeviceCapabilityError, DeviceSessionError) as exc:
                if native is not None:
                    _remember_onvif_failure(camera_id)
                last_error = exc
                continue

        movement_id = uuid.uuid4().hex
        result["movement_id"] = movement_id
        with _active_movements_lock:
            _active_movements[(camera_id, int(owner_id))] = {
                "movement_id": movement_id,
                "backend": result["backend"],
                "driver": result.get("driver"),
                "native": dict(native) if result["backend"] == "native_sdk" and native else None,
            }
        _record_control_success(camera, result)
        return result

    if last_error is not None:
        _save_profile(camera, status="unavailable", error=last_error, error_category="control")
        raise last_error
    raise DeviceCapabilityError("Esta camera nao expoe controle PTZ via ONVIF nem via SDK nativo.")


def stop_ptz(camera: Camera, *, owner_id: int | None = None) -> dict[str, Any]:
    camera_id = int(camera.id)
    movement: dict[str, Any] | None = None
    if owner_id is not None:
        with _active_movements_lock:
            movement = _active_movements.pop((camera_id, int(owner_id)), None)

    if movement and movement.get("backend") == "native_sdk" and owner_id is not None:
        native = movement.get("native") or _native_sdk_candidate(camera)
        if native is not None:
            token = _peek_sdk_session_token(camera, owner_id, native["brand"], native["port"], native["channel"])
            if token:
                sdk_lab.ptz_hold_stop(token, owner_id)
                return {
                    "backend": "native_sdk",
                    "driver": movement.get("driver"),
                    "movement_id": movement.get("movement_id"),
                    "ptz_capable": True,
                    "stopped": True,
                }

    if movement and movement.get("backend") == "onvif":
        onvif_ptz_stop(camera)
        _clear_onvif_failure(camera_id)
        return {
            "backend": "onvif",
            "driver": "onvif",
            "movement_id": movement.get("movement_id"),
            "ptz_capable": True,
            "stopped": True,
        }

    preferred = _preferred_backend(camera)
    native = _native_sdk_candidate(camera)
    if preferred in {"native_sdk", "intelbras_http", "dahua_sdk", "hikvision_sdk"} and owner_id is not None and native:
        token = _peek_sdk_session_token(camera, owner_id, native["brand"], native["port"], native["channel"])
        if token:
            sdk_lab.ptz_hold_stop(token, owner_id)
            return {"backend": "native_sdk", "driver": preferred, "ptz_capable": True, "stopped": True}

    # A câmera já foi avaliada para SDK nativo. Se não há movimento/sessão
    # ativa, parar é um no-op; não tente ONVIF em seguida, pois isso pode
    # bloquear vários segundos em uma porta que não oferece SOAP.
    if preferred in {"native_sdk", "intelbras_http", "dahua_sdk", "hikvision_sdk"} and native:
        return {
            "backend": "native_sdk",
            "driver": preferred,
            "ptz_capable": True,
            "stopped": True,
        }

    try:
        onvif_ptz_stop(camera)
        _clear_onvif_failure(camera_id)
        return {"backend": "onvif", "driver": "onvif", "ptz_capable": True, "stopped": True}
    except (DeviceCapabilityError, DeviceSessionError) as onvif_error:
        if owner_id is not None and native is not None:
            token = _peek_sdk_session_token(camera, owner_id, native["brand"], native["port"], native["channel"])
            if token:
                sdk_lab.ptz_hold_stop(token, owner_id)
                return {"backend": "native_sdk", "driver": preferred or "native_sdk", "ptz_capable": True, "stopped": True}
        raise onvif_error


def get_camera_stream_dimensions(camera: Camera, *, owner_id: int) -> tuple[int, int]:
    """Proporcao de referencia da camera para a geometria do PTZ 3D.

    O laboratorio consulta primeiro o stream principal mesmo quando o monitor
    exibe o substream, com fallback para o fluxo exibido.
    Reaproveita a mesma sessao SDK usada por position_ptz_3d."""
    native = _native_sdk_candidate(camera)
    if native is None:
        raise DeviceCapabilityError("O posicionamento 3D requer o SDK nativo desta camera.")
    session = _get_or_create_sdk_session(
        camera, owner_id, native["brand"], native["port"], native["channel"]
    )
    return sdk_lab.stream_dimensions(session.token, owner_id)


def position_ptz_3d(
    camera: Camera,
    *,
    owner_id: int,
    x_start: int,
    y_start: int,
    x_end: int,
    y_end: int,
) -> dict[str, Any]:
    """Aplica posicionamento 3D nativo no canal selecionado do monitor."""
    native = _native_sdk_candidate(camera)
    if native is None:
        raise DeviceCapabilityError("O posicionamento 3D requer o SDK nativo desta camera.")
    session = _get_or_create_sdk_session(
        camera, owner_id, native["brand"], native["port"], native["channel"]
    )
    sdk_lab.position_ptz_3d(
        session.token,
        owner_id,
        x_start=x_start,
        y_start=y_start,
        x_end=x_end,
        y_end=y_end,
    )
    return {
        "backend": "native_sdk",
        "driver": "intelbras_http" if native["brand"] == "intelbras" else f"{native['brand']}_sdk",
        "ptz_capable": True,
    }


def _session_key(camera: Camera, owner_id: int, brand: str, port: int, channel: int) -> tuple[int, int, str, str, int, int]:
    return (int(camera.id), int(owner_id), _normalize_text(getattr(camera, "ip", None)), brand, int(port), int(channel))


def _peek_sdk_session_token(camera: Camera, owner_id: int, brand: str, port: int, channel: int) -> str | None:
    key = _session_key(camera, owner_id, brand, port, channel)
    with _sdk_sessions_lock:
        entry = _sdk_sessions.get(key)
    return entry.token if entry else None


def _get_or_create_sdk_session(
    camera: Camera,
    owner_id: int,
    brand: str,
    port: int,
    channel: int,
) -> sdk_lab.HikSdkLabSession:
    key = _session_key(camera, owner_id, brand, port, channel)
    now = time.time()

    with _sdk_sessions_lock:
        cached = _sdk_sessions.get(key)
        if cached is not None:
            cached.last_used_at = now
            try:
                return sdk_lab.get_session(cached.token, owner_id)
            except sdk_lab.HikSdkLabError:
                _sdk_sessions.pop(key, None)

    session = sdk_lab.connect_device(
        owner_id=owner_id,
        label=str(getattr(camera, "name", "") or f"Camera {camera.id}"),
        device_type="camera",
        host=str(getattr(camera, "ip", "") or "").strip(),
        port=int(port),
        username=str(getattr(camera, "username", "") or ""),
        password=str(getattr(camera, "password", "") or ""),
        channel=int(channel),
        manufacturer=brand,
    )

    entry = _SdkSessionEntry(
        token=str(session["token"]),
        owner_id=int(owner_id),
        key=key,
        created_at=now,
        last_used_at=now,
    )
    with _sdk_sessions_lock:
        _sdk_sessions[key] = entry

    return sdk_lab.get_session(entry.token, owner_id)


def invalidate_camera_ptz_session(camera: Camera, owner_id: int | None = None) -> None:
    camera_id = int(camera.id)
    with _sdk_sessions_lock:
        keys = [
            key
            for key, entry in _sdk_sessions.items()
            if entry.key[0] == camera_id and (owner_id is None or entry.owner_id == int(owner_id))
        ]
    for key in keys:
        with _sdk_sessions_lock:
            entry = _sdk_sessions.pop(key, None)
        if entry is None:
            continue
        try:
            sdk_lab.disconnect(entry.token, entry.owner_id)
        except Exception:
            pass
    invalidate_ptz_profile(camera_id)
