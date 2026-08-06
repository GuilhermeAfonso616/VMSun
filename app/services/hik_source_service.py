"""Descoberta e persistencia compartilhadas para HikCentral e Hik-Connect."""

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from sqlalchemy.orm import Session

from app.db.models import Camera
from app.services.camera_factory import build_camera_model


HikProvider = Literal["hikcentral", "hikconnect"]


class HikSourceError(ValueError):
    """Erro de validacao ou persistencia de uma fonte Hik."""


@dataclass(frozen=True, slots=True)
class HikSourceCreationResult:
    created_count: int
    skipped_count: int


def build_hik_source_url(
    provider: HikProvider,
    *,
    camera_index_code: str = "",
    serial_number: str = "",
    verification_code: str = "",
    channel_no: int = 1,
) -> str:
    if provider == "hikcentral":
        index_code = str(camera_index_code or "").strip()
        if not index_code:
            raise HikSourceError("Canal HikCentral sem cameraIndexCode.")
        return f"hc://{index_code}"
    if provider == "hikconnect":
        serial = str(serial_number or "").strip()
        if not serial:
            raise HikSourceError("Canal Hik-Connect sem número de série.")
        return (
            f"hcp2p://{serial}?verify={verification_code}"
            f"&channel={int(channel_no)}"
        )
    raise HikSourceError(f"Provedor Hik inválido: {provider}")


def find_existing_hik_source(db: Session, source_url: str) -> Camera | None:
    candidates = (
        db.query(Camera)
        .filter(Camera.is_deleted == False)
        .all()
    )
    return next((camera for camera in candidates if camera.rtsp_url == source_url), None)


def build_hik_discovery_profiles(
    db: Session,
    *,
    provider: HikProvider,
    discovered_cameras: list[Mapping[str, Any]],
    serial_number: str = "",
    verification_code: str = "",
    channel_no: int = 1,
) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for index, camera in enumerate(discovered_cameras):
        index_code = str(camera["cameraIndexCode"])
        source_url = build_hik_source_url(
            provider,
            camera_index_code=index_code,
            serial_number=serial_number,
            verification_code=verification_code,
            channel_no=channel_no,
        )
        existing = find_existing_hik_source(db, source_url)
        profile = {
            "index": index,
            "cameraIndexCode": index_code,
            "cameraName": camera["cameraName"],
            "channelNo": camera["channelNo"],
            "status": camera["status"],
            "existing_id": existing.id if existing else None,
            "existing_name": existing.name if existing else None,
        }
        if provider == "hikconnect":
            profile["serial_number"] = serial_number
        profiles.append(profile)
    return profiles


def create_hik_sources(
    db: Session,
    *,
    provider: HikProvider,
    host: str,
    username: str,
    password: str,
    verification_code: str,
    selected_indexes: set[str],
    cached_profiles: Mapping[int, Mapping[str, Any]],
    camera_names: Mapping[int, str],
) -> HikSourceCreationResult:
    created_count = 0
    skipped_count = 0
    try:
        for index_text in selected_indexes:
            index = int(index_text)
            profile = cached_profiles.get(index)
            if not profile:
                continue

            channel = int(profile["channelNo"])
            serial_number = str(profile.get("serial_number") or host)
            source_url = build_hik_source_url(
                provider,
                camera_index_code=str(profile.get("cameraIndexCode") or ""),
                serial_number=serial_number,
                verification_code=verification_code,
                channel_no=channel,
            )
            if find_existing_hik_source(db, source_url):
                skipped_count += 1
                continue

            camera_name = str(camera_names.get(index) or "").strip()
            if not camera_name:
                camera_name = str(profile["cameraName"])

            camera = build_camera_model(
                name=camera_name,
                ip=host if provider == "hikcentral" else serial_number,
                manufacturer="Hikvision",
                onvif_port=80,
                username=username,
                password=password,
                rtsp_url=source_url,
            )
            camera.source_type = f"{provider}_channel"
            camera.source_parent_id = None
            camera.source_channel = channel
            camera.source_stream_kind = "main"
            camera.source_brand = "hikvision"
            camera.source_provider = provider
            db.add(camera)
            created_count += 1

        db.commit()
    except Exception:
        db.rollback()
        raise

    return HikSourceCreationResult(
        created_count=created_count,
        skipped_count=skipped_count,
    )
