"""Sessao viva de dispositivo ONVIF: prototipo de "camera como objeto".

Diferente do fluxo atual (ONVIF usado uma unica vez para descobrir o RTSP),
este servico mantem uma conexao por camera em cache, permitindo consultar
capacidades e enviar comandos de controle (PTZ) com baixa latencia.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from onvif import ONVIFCamera

from app.camera.onvif_client import _rewrite_xaddrs, get_wsdl_dir, onvif_transport
from app.db.models import Camera


class DeviceSessionError(RuntimeError):
    """Falha de comunicacao ou de comando com o dispositivo."""


class DeviceCapabilityError(DeviceSessionError):
    """O dispositivo nao expoe a capacidade solicitada."""


@dataclass(slots=True)
class DeviceSession:
    camera_id: int
    host: str
    port: int
    onvif: Any
    media: Any
    ptz: Any
    profile_token: str
    ptz_capable: bool
    device_info: dict
    capabilities: dict
    profiles: list[dict]
    connected_at: float
    lock: threading.Lock = field(default_factory=threading.Lock)


_sessions: dict[int, DeviceSession] = {}
_sessions_lock = threading.Lock()


def _clamp(value: float, minimum: float = -1.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, float(value)))


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _connect(camera: Camera) -> DeviceSession:
    host = os.getenv("ONVIF_CONNECT_HOST", "").strip() or str(camera.ip)
    port_env = os.getenv("ONVIF_CONNECT_PORT", "").strip()
    port = int(port_env) if port_env.isdigit() else int(camera.onvif_port or 80)

    try:
        onvif_cam = ONVIFCamera(
            host,
            port,
            camera.username,
            camera.password,
            wsdl_dir=get_wsdl_dir(),
            transport=onvif_transport(),
        )
        _rewrite_xaddrs(onvif_cam, host, port)
        devicemgmt = onvif_cam.devicemgmt
        raw_info = devicemgmt.GetDeviceInformation()
    except Exception as exc:
        raise DeviceSessionError(
            f"Falha ao conectar via ONVIF em {host}:{port}: {exc}"
        ) from exc

    device_info = {
        "manufacturer": _text(getattr(raw_info, "Manufacturer", None)),
        "model": _text(getattr(raw_info, "Model", None)),
        "firmware_version": _text(getattr(raw_info, "FirmwareVersion", None)),
        "serial_number": _text(getattr(raw_info, "SerialNumber", None)),
        "hardware_id": _text(getattr(raw_info, "HardwareId", None)),
    }

    capabilities: dict[str, bool] = {"media": True}
    try:
        caps = devicemgmt.GetCapabilities({"Category": "All"})
        for key, attr in (
            ("ptz", "PTZ"),
            ("events", "Events"),
            ("analytics", "Analytics"),
            ("imaging", "Imaging"),
        ):
            capabilities[key] = getattr(caps, attr, None) is not None
    except Exception:
        capabilities.update({"ptz": False, "events": False, "analytics": False, "imaging": False})

    try:
        media = onvif_cam.create_media_service()
        raw_profiles = media.GetProfiles() or []
    except Exception as exc:
        raise DeviceSessionError(f"Falha ao listar profiles de midia: {exc}") from exc

    profiles: list[dict] = []
    profile_token = ""
    for raw_profile in raw_profiles:
        token = _text(getattr(raw_profile, "token", None))
        if not token:
            continue
        has_ptz = getattr(raw_profile, "PTZConfiguration", None) is not None
        profiles.append(
            {
                "token": token,
                "name": _text(getattr(raw_profile, "Name", None)) or token,
                "ptz": has_ptz,
            }
        )
        if not profile_token or (has_ptz and not any(p["ptz"] for p in profiles[:-1])):
            profile_token = token

    if not profiles:
        raise DeviceSessionError("Nenhum profile ONVIF encontrado no dispositivo.")

    ptz_profile = next((p for p in profiles if p["ptz"]), None)
    if ptz_profile:
        profile_token = ptz_profile["token"]

    ptz_service = None
    ptz_capable = False
    if capabilities.get("ptz") or ptz_profile is not None:
        try:
            ptz_service = onvif_cam.create_ptz_service()
            ptz_capable = True
        except Exception:
            ptz_service = None
            ptz_capable = False
    capabilities["ptz"] = ptz_capable

    return DeviceSession(
        camera_id=int(camera.id),
        host=host,
        port=port,
        onvif=onvif_cam,
        media=media,
        ptz=ptz_service,
        profile_token=profile_token,
        ptz_capable=ptz_capable,
        device_info=device_info,
        capabilities=capabilities,
        profiles=profiles,
        connected_at=time.time(),
    )


def invalidate_session(camera_id: int) -> None:
    with _sessions_lock:
        _sessions.pop(int(camera_id), None)


def _get_session(camera: Camera) -> DeviceSession:
    camera_id = int(camera.id)
    with _sessions_lock:
        session = _sessions.get(camera_id)
    if session is not None:
        return session

    session = _connect(camera)
    with _sessions_lock:
        _sessions[camera_id] = session
    return session


def _with_session(camera: Camera, operation: Callable[[DeviceSession], Any]) -> Any:
    """Executa a operacao na sessao viva; reconecta uma vez se ela caiu."""
    session = _get_session(camera)
    try:
        with session.lock:
            return operation(session)
    except DeviceCapabilityError:
        raise
    except Exception:
        invalidate_session(camera.id)
        session = _get_session(camera)
        try:
            with session.lock:
                return operation(session)
        except DeviceCapabilityError:
            raise
        except Exception as exc:
            invalidate_session(camera.id)
            raise DeviceSessionError(f"Comando falhou apos reconexao: {exc}") from exc


def _require_ptz(session: DeviceSession) -> Any:
    if not session.ptz_capable or session.ptz is None:
        raise DeviceCapabilityError(
            "Este dispositivo nao expoe o servico PTZ via ONVIF."
        )
    return session.ptz


def _serialize_presets(raw_presets: Any) -> list[dict]:
    presets: list[dict] = []
    for preset in raw_presets or []:
        token = _text(getattr(preset, "token", None))
        if not token:
            continue
        presets.append(
            {
                "token": token,
                "name": _text(getattr(preset, "Name", None)) or f"Preset {token}",
            }
        )
    return presets


def describe_device(camera: Camera) -> dict:
    """Inspeciona o dispositivo: identidade, capacidades, profiles e presets."""

    def operation(session: DeviceSession) -> dict:
        presets: list[dict] = []
        if session.ptz_capable and session.ptz is not None:
            try:
                presets = _serialize_presets(
                    session.ptz.GetPresets({"ProfileToken": session.profile_token})
                )
            except Exception:
                presets = []
        return {
            "device": session.device_info,
            "capabilities": session.capabilities,
            "profiles": session.profiles,
            "profile_token": session.profile_token,
            "presets": presets,
            "endpoint": f"{session.host}:{session.port}",
            "session_age_seconds": round(time.time() - session.connected_at, 1),
        }

    return _with_session(camera, operation)



# Sem o campo Timeout, o ContinuousMove deveria (pela spec ONVIF) girar ate
# um Stop explicito -- mas boa parte das cameras baratas aplica um timeout
# interno de seguranca bem curto (1-2s) quando esse campo vem vazio, entao a
# rotacao para sozinha mesmo com o botao pressionado. O Stop ja e enviado de
# forma confiavel ao soltar o botao (pointerup/blur/visibilitychange), entao
# um timeout longo aqui e so uma rede de seguranca contra requests perdidos.
_PTZ_CONTINUOUS_MOVE_TIMEOUT = timedelta(seconds=60)


def ptz_continuous_move(
    camera: Camera,
    pan: float = 0.0,
    tilt: float = 0.0,
    zoom: float = 0.0,
) -> None:
    def operation(session: DeviceSession) -> None:
        ptz = _require_ptz(session)
        request = ptz.create_type("ContinuousMove")
        request.ProfileToken = session.profile_token
        request.Velocity = {
            "PanTilt": {"x": _clamp(pan), "y": _clamp(tilt)},
            "Zoom": {"x": _clamp(zoom)},
        }
        request.Timeout = _PTZ_CONTINUOUS_MOVE_TIMEOUT
        ptz.ContinuousMove(request)

    _with_session(camera, operation)


def ptz_stop(camera: Camera) -> None:
    def operation(session: DeviceSession) -> None:
        ptz = _require_ptz(session)
        ptz.Stop(
            {
                "ProfileToken": session.profile_token,
                "PanTilt": True,
                "Zoom": True,
            }
        )

    _with_session(camera, operation)


def ptz_goto_preset(camera: Camera, preset_token: str) -> None:
    def operation(session: DeviceSession) -> None:
        ptz = _require_ptz(session)
        ptz.GotoPreset(
            {
                "ProfileToken": session.profile_token,
                "PresetToken": str(preset_token),
            }
        )

    _with_session(camera, operation)


def ptz_set_preset(camera: Camera, name: str) -> dict:
    def operation(session: DeviceSession) -> dict:
        ptz = _require_ptz(session)
        token = ptz.SetPreset(
            {
                "ProfileToken": session.profile_token,
                "PresetName": str(name).strip() or None,
            }
        )
        return {"token": _text(token)}

    return _with_session(camera, operation)
