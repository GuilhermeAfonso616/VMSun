from __future__ import annotations

from dataclasses import dataclass, field

from app.camera.onvif_client import RTSPDiscoveryResult, RTSPProfile, discover_rtsp
from app.camera.rtsp_discovery import build_common_rtsp_candidates, probe_rtsp_candidates

AUTO_FALLBACK_RTSP_PORTS = (554, 8554)
AUTO_FALLBACK_RTSP_PATHS = (
    "/Streaming/Channels/101",
    "/Streaming/Channels/1",
    "/cam/realmonitor?channel=1&subtype=0",
    "/stream1",
)


@dataclass(frozen=True, slots=True)
class CameraDiscoveryAttempt:
    method: str
    ok: bool
    message: str = ""


@dataclass(frozen=True, slots=True)
class CameraDiscovery:
    rtsp_url: str
    onvif_port: int
    profiles: list[RTSPProfile] = field(default_factory=list)
    method: str = "onvif"
    attempts: list[CameraDiscoveryAttempt] = field(default_factory=list)


def discover_camera_streams(
    *,
    ip: str,
    onvif_port: int | None,
    username: str,
    password: str,
    allow_rtsp_fallback: bool = True,
) -> CameraDiscovery:
    attempts: list[CameraDiscoveryAttempt] = []

    try:
        onvif_result: RTSPDiscoveryResult = discover_rtsp(ip, onvif_port, username, password)
        attempts.append(CameraDiscoveryAttempt(method="onvif", ok=True, message="ONVIF encontrou stream RTSP."))
        return CameraDiscovery(
            rtsp_url=onvif_result.rtsp_url,
            onvif_port=onvif_result.onvif_port,
            profiles=onvif_result.profiles,
            method="onvif",
            attempts=attempts,
        )
    except Exception as exc:
        attempts.append(
            CameraDiscoveryAttempt(
                method="onvif",
                ok=False,
                message=str(exc).strip() or exc.__class__.__name__,
            )
        )

    if not allow_rtsp_fallback:
        details = " | ".join(attempt.message for attempt in attempts if attempt.message)
        raise RuntimeError(details or "Nao foi possivel descobrir a camera via ONVIF.")

    candidates = build_common_rtsp_candidates(
        ip=ip,
        username=username,
        password=password,
        ports=AUTO_FALLBACK_RTSP_PORTS,
        paths=AUTO_FALLBACK_RTSP_PATHS,
    )
    results = probe_rtsp_candidates(candidates)
    working = [item for item in results if bool(item.get("ok")) and str(item.get("url") or "").strip()]

    if not working:
        failures = [str(item.get("error") or "").strip() for item in results if str(item.get("error") or "").strip()]
        sample = " | ".join(failures[:3])
        attempts.append(
            CameraDiscoveryAttempt(
                method="rtsp_fallback",
                ok=False,
                message=sample or "Nenhum padrao RTSP comum abriu frame.",
            )
        )
        details = " | ".join(attempt.message for attempt in attempts if attempt.message)
        raise RuntimeError(details or "Nao foi possivel descobrir RTSP automaticamente.")

    profiles: list[RTSPProfile] = []
    for index, item in enumerate(working, start=1):
        profiles.append(
            RTSPProfile(
                token=f"rtsp_fallback_{index}",
                name=f"RTSP fallback {index}",
                rtsp_url=str(item["url"]),
            )
        )

    attempts.append(
        CameraDiscoveryAttempt(
            method="rtsp_fallback",
            ok=True,
            message=f"{len(profiles)} padrao(oes) RTSP abriram frame.",
        )
    )

    return CameraDiscovery(
        rtsp_url=profiles[0].rtsp_url,
        onvif_port=int(onvif_port or 80),
        profiles=profiles,
        method="rtsp_fallback",
        attempts=attempts,
    )
