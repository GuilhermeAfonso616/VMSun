from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.video_sources.models import StreamProfile, VideoSourceCapabilities, VideoSourceConfig
from app.video_sources.providers.generic_nvr import normalize_brand
from app.video_sources.registry import build_video_source_provider


@dataclass(frozen=True, slots=True)
class NvrDiscoveryResult:
    provider_type: str
    brand: str
    host: str
    rtsp_port: int
    channel_count: int
    profiles: list[StreamProfile] = field(default_factory=list)
    capabilities: VideoSourceCapabilities = field(default_factory=VideoSourceCapabilities)

    def to_dict(self) -> dict:
        return {
            "provider_type": self.provider_type,
            "brand": self.brand,
            "host": self.host,
            "rtsp_port": self.rtsp_port,
            "channel_count": self.channel_count,
            "capabilities": asdict(self.capabilities),
            "profiles": [asdict(profile) for profile in self.profiles],
            "working_count": sum(1 for profile in self.profiles if profile.ok),
        }


def discover_nvr_channels(
    *,
    host: str,
    username: str,
    password: str,
    rtsp_port: int = 554,
    brand: str = "generic",
    channel_count: int = 16,
    provider_type: str = "generic_nvr",
    stream_kinds: tuple[str, ...] = ("main", "sub"),
    probe: bool = True,
) -> NvrDiscoveryResult:
    normalized_brand = normalize_brand(brand)
    config = VideoSourceConfig(
        provider_type=provider_type or "generic_nvr",
        host=str(host or "").strip(),
        username=username or "",
        password=password or "",
        rtsp_port=int(rtsp_port or 554),
        channel_count=max(1, int(channel_count or 1)),
        brand=normalized_brand,
    )
    if not config.host:
        raise ValueError("Host/IP do NVR e obrigatorio")

    provider = build_video_source_provider(config)
    profiles = provider.discover_channels(stream_kinds=stream_kinds, probe=probe)
    return NvrDiscoveryResult(
        provider_type=config.provider_type,
        brand=normalized_brand,
        host=config.host,
        rtsp_port=config.rtsp_port,
        channel_count=config.channel_count,
        profiles=profiles,
        capabilities=provider.capabilities,
    )

