from __future__ import annotations

from app.video_sources.models import VideoSourceConfig
from app.video_sources.providers.base import VideoSourceProvider
from app.video_sources.providers.generic_nvr import GenericNvrProvider
from app.video_sources.providers.sdk import DahuaProvider, HikvisionProvider, IntelbrasProvider


def build_video_source_provider(config: VideoSourceConfig) -> VideoSourceProvider:
    provider_type = str(config.provider_type or "generic_nvr").strip().lower()

    if provider_type in {"hikvision", "hikvision_nvr", "hikvision_sdk"}:
        return HikvisionProvider(config)
    if provider_type in {"dahua", "dahua_nvr", "dahua_sdk"}:
        return DahuaProvider(config)
    if provider_type in {"intelbras", "intelbras_nvr", "intelbras_sdk"}:
        return IntelbrasProvider(config)
    return GenericNvrProvider(config)
