from __future__ import annotations

from abc import ABC, abstractmethod

from app.video_sources.models import (
    StreamProfile,
    VideoSourceCapabilities,
    VideoSourceConfig,
    VideoSourceHealth,
)


class VideoSourceProvider(ABC):
    """Common interface for cameras, NVR channels and future SDK integrations."""

    def __init__(self, config: VideoSourceConfig):
        self.config = config

    @property
    @abstractmethod
    def capabilities(self) -> VideoSourceCapabilities:
        raise NotImplementedError

    @abstractmethod
    def discover_channels(
        self,
        *,
        stream_kinds: tuple[str, ...] = ("main", "sub"),
        probe: bool = True,
    ) -> list[StreamProfile]:
        raise NotImplementedError

    def get_stream_profiles(
        self,
        *,
        channel: int,
        stream_kinds: tuple[str, ...] = ("main", "sub"),
        probe: bool = True,
    ) -> list[StreamProfile]:
        return [
            profile
            for profile in self.discover_channels(stream_kinds=stream_kinds, probe=probe)
            if profile.channel == int(channel)
        ]

    def get_snapshot(self, *, channel: int) -> bytes | None:
        return None

    def get_recording_clip(self, *, channel: int, start_iso: str, end_iso: str) -> bytes | None:
        return None

    def get_native_events(self, *, channel: int | None = None) -> list[dict]:
        return []

    def move_ptz(
        self,
        *,
        channel: int,
        pan: int = 0,
        tilt: int = 0,
        zoom: int = 0,
        speed: int = 4,
        duration_ms: int = 300,
    ) -> bool:
        return False

    def get_health(self) -> VideoSourceHealth:
        return VideoSourceHealth(ok=False, status="unsupported", reason="Health check not implemented")

