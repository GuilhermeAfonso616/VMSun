from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class VideoSourceConfig:
    provider_type: str
    host: str
    username: str = ""
    password: str = ""
    rtsp_port: int = 554
    http_port: int = 80
    onvif_port: int | None = None
    channel_count: int = 16
    brand: str = "generic"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VideoSourceCapabilities:
    supports_discovery: bool = False
    supports_rtsp: bool = False
    supports_snapshot: bool = False
    supports_recording_clip: bool = False
    supports_native_events: bool = False
    supports_health: bool = False
    supports_ptz: bool = False
    requires_sdk: bool = False


@dataclass(frozen=True, slots=True)
class VideoSourceHealth:
    ok: bool
    status: str
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StreamProfile:
    provider_type: str
    source_brand: str
    channel: int
    stream_kind: str
    name: str
    rtsp_url: str
    masked_rtsp_url: str
    ok: bool = False
    error: str = ""
    encoding: str | None = None
    width: int | None = None
    height: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

