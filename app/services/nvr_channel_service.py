"""Regras compartilhadas para canais persistidos de NVR.

Este modulo concentra invariantes usadas pelas interfaces HTTP e web. Ele nao
conhece formularios ou modelos de resposta, apenas a representacao persistida
dos canais.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Camera
from app.db.query_helpers import get_active_cameras_query
from app.services.camera_factory import build_camera_model, resolve_initial_profile


_SUBSTREAM_ALIASES = {"sub", "secondary", "substream", "sub_stream"}


class NvrChannelError(ValueError):
    """Erro de validacao ao persistir canais selecionados."""


@dataclass(frozen=True, slots=True)
class NvrChannelSpec:
    channel: int
    stream_kind: str
    rtsp_url: str
    name: str | None = None
    provider_type: str | None = None
    source_brand: str | None = None


@dataclass(slots=True)
class NvrChannelCreationResult:
    created: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "skipped": self.skipped,
            "count": len(self.created),
            "skipped_count": len(self.skipped),
        }


def normalize_source_stream_kind(value: str | None) -> str:
    """Converte aliases de stream para os valores persistidos pelo sistema."""
    normalized = str(value or "main").strip().lower()
    return "sub" if normalized in _SUBSTREAM_ALIASES else "main"


def find_existing_nvr_channel(
    db: Session,
    *,
    host: str,
    channel: int,
    stream_kind: str,
    rtsp_url: str | None = None,
) -> Camera | None:
    """Localiza um canal ativo por URL ou pela identidade logica do NVR."""
    query = get_active_cameras_query(db)

    if rtsp_url:
        existing_by_url = next(
            (camera for camera in query.all() if camera.rtsp_url == rtsp_url),
            None,
        )
        if existing_by_url is not None:
            return existing_by_url

    return (
        query.filter(Camera.source_type == "nvr_channel")
        .filter(Camera.ip == host)
        .filter(Camera.source_channel == int(channel))
        .filter(Camera.source_stream_kind == stream_kind)
        .first()
    )


def create_nvr_channel_sources(
    db: Session,
    *,
    host: str,
    username: str,
    password: str,
    onvif_port: int | None,
    base_name: str,
    brand: str,
    provider_type: str,
    profiles: list[NvrChannelSpec],
    camera_family: str,
    scene_category: str,
    target_focus: str,
    nuisance_profile: dict[str, Any],
    analytics_profile: Any | None,
    coordinate_space_override: str | None = None,
) -> NvrChannelCreationResult:
    if not profiles:
        raise NvrChannelError("Nenhum canal/profile selecionado")

    base_profile = resolve_initial_profile(
        analytics_profile,
        camera_family=camera_family,
        scene_category=scene_category,
        target_focus=target_focus,
        nuisance_profile=nuisance_profile,
    )
    result = NvrChannelCreationResult()
    for selected in profiles:
        rtsp_url = str(selected.rtsp_url or "").strip()
        if not rtsp_url:
            raise NvrChannelError(f"Canal {selected.channel} sem RTSP")

        stream_kind = normalize_source_stream_kind(selected.stream_kind)
        existing = find_existing_nvr_channel(
            db,
            host=host,
            channel=int(selected.channel),
            stream_kind=stream_kind,
            rtsp_url=rtsp_url,
        )
        if existing:
            result.skipped.append(
                {
                    "id": existing.id,
                    "name": existing.name,
                    "source_channel": existing.source_channel,
                    "source_stream_kind": existing.source_stream_kind,
                    "reason": "already_exists",
                }
            )
            continue

        camera = build_camera_model(
            name=selected.name or f"{base_name} - Canal {selected.channel} {stream_kind}",
            ip=host,
            manufacturer=str(selected.source_brand or brand or "Nao informada").strip(),
            onvif_port=onvif_port if onvif_port is not None else 80,
            username=username,
            password=password,
            rtsp_url=rtsp_url,
            analytics_profile=base_profile.to_dict(),
            coordinate_space_override=coordinate_space_override,
        )
        camera.source_type = "nvr_channel"
        camera.source_parent_id = None
        camera.source_channel = int(selected.channel)
        camera.source_stream_kind = stream_kind
        camera.source_brand = selected.source_brand or brand
        camera.source_provider = selected.provider_type or provider_type
        db.add(camera)
        db.flush()
        result.created.append(
            {
                "id": camera.id,
                "name": camera.name,
                "source_type": camera.source_type,
                "source_channel": camera.source_channel,
                "source_stream_kind": camera.source_stream_kind,
                "source_brand": camera.source_brand,
                "source_provider": camera.source_provider,
            }
        )

    db.commit()
    return result
