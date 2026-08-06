from __future__ import annotations

from app.camera.rtsp_discovery import build_rtsp_url, probe_rtsp_candidates
from app.core.url_safety import mask_url_credentials
from app.video_sources.models import StreamProfile, VideoSourceCapabilities
from app.video_sources.providers.base import VideoSourceProvider


NVR_RTSP_TEMPLATES: dict[str, tuple[tuple[str, str], ...]] = {
    "hikvision": (
        ("main", "/Streaming/Channels/{channel}01"),
        ("sub", "/Streaming/Channels/{channel}02"),
    ),
    "dahua": (
        ("main", "/cam/realmonitor?channel={channel}&subtype=0"),
        ("sub", "/cam/realmonitor?channel={channel}&subtype=1"),
    ),
    "intelbras": (
        ("main", "/cam/realmonitor?channel={channel}&subtype=0"),
        ("sub", "/cam/realmonitor?channel={channel}&subtype=1"),
        ("main", "/Streaming/Channels/{channel}01"),
        ("sub", "/Streaming/Channels/{channel}02"),
    ),
    "generic": (
        ("main", "/Streaming/Channels/{channel}01"),
        ("sub", "/Streaming/Channels/{channel}02"),
        ("main", "/cam/realmonitor?channel={channel}&subtype=0"),
        ("sub", "/cam/realmonitor?channel={channel}&subtype=1"),
        ("main", "/stream{channel}"),
        ("sub", "/stream{channel}_sub"),
        ("main", "/channel/{channel}/stream"),
    ),
}

NVR_PROBE_MAX_WORKERS = 8
NVR_PROBE_MAX_CANDIDATES = 256


def normalize_brand(value: str | None) -> str:
    brand = str(value or "generic").strip().lower()
    if brand in {"hik", "hikvision", "hikvision_nvr"}:
        return "hikvision"
    if brand in {"dahua", "dahua_nvr"}:
        return "dahua"
    if brand in {"intelbras", "intelbras_nvr"}:
        return "intelbras"
    return "generic"


class GenericNvrProvider(VideoSourceProvider):
    @property
    def capabilities(self) -> VideoSourceCapabilities:
        return VideoSourceCapabilities(
            supports_discovery=True,
            supports_rtsp=True,
            supports_health=True,
        )

    def discover_channels(
        self,
        *,
        stream_kinds: tuple[str, ...] = ("main", "sub"),
        probe: bool = True,
    ) -> list[StreamProfile]:
        brand = normalize_brand(self.config.brand)
        templates = NVR_RTSP_TEMPLATES.get(brand) or NVR_RTSP_TEMPLATES["generic"]
        allowed_kinds = {str(kind).strip().lower() for kind in stream_kinds if str(kind).strip()}
        channel_count = max(1, int(self.config.channel_count or 1))
        rtsp_port = int(self.config.rtsp_port or 554)

        candidates: list[tuple[int, str, str]] = []
        seen_urls: set[str] = set()
        for channel in range(1, channel_count + 1):
            for stream_kind, template in templates:
                if allowed_kinds and stream_kind not in allowed_kinds:
                    continue
                path = template.format(channel=channel)
                rtsp_url = build_rtsp_url(
                    ip=self.config.host,
                    port=rtsp_port,
                    path=path,
                    username=self.config.username,
                    password=self.config.password,
                )
                if rtsp_url in seen_urls:
                    continue
                seen_urls.add(rtsp_url)
                candidates.append((channel, stream_kind, rtsp_url))

        probe_by_url: dict[str, dict] = {}
        if probe and candidates:
            if len(candidates) > NVR_PROBE_MAX_CANDIDATES:
                raise RuntimeError(
                    f"A busca gerou {len(candidates)} URLs para teste. "
                    "Reduza os canais/streams ou selecione a marca correta do NVR."
                )
            probe_results = probe_rtsp_candidates(
                [url for _, _, url in candidates],
                max_workers=NVR_PROBE_MAX_WORKERS,
                allow_transport_fallback=False,
            )
            probe_by_url = {str(item.get("url") or ""): item for item in probe_results}

        profiles: list[StreamProfile] = []
        for channel, stream_kind, rtsp_url in candidates:
            result = probe_by_url.get(rtsp_url, {})
            ok = bool(result.get("ok")) if probe else True
            error = str(result.get("error") or "") if probe else ""
            width = int(result.get("width") or 0) or None
            height = int(result.get("height") or 0) or None
            fps = result.get("fps")
            profiles.append(
                StreamProfile(
                    provider_type=self.config.provider_type,
                    source_brand=brand,
                    channel=channel,
                    stream_kind=stream_kind,
                    name=f"Canal {channel} {stream_kind}",
                    rtsp_url=rtsp_url,
                    masked_rtsp_url=mask_url_credentials(rtsp_url) or rtsp_url,
                    ok=ok,
                    error=error,
                    width=width,
                    height=height,
                    metadata={
                        "probe_enabled": probe,
                        "rtsp_port": rtsp_port,
                        "fps": fps,
                    },
                )
            )

        return profiles
