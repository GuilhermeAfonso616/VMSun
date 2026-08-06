from __future__ import annotations

from requests import exceptions as requests_exceptions

from app.video_sources.models import StreamProfile, VideoSourceCapabilities, VideoSourceHealth
from app.video_sources.providers.base import VideoSourceProvider
from app.video_sources.providers.dahua_http_api import DahuaHttpApiClient, DahuaHttpApiError
from app.video_sources.providers.generic_nvr import GenericNvrProvider


class SdkBackedProvider(GenericNvrProvider):
    """Adapter shape for proprietary SDK support.

    For now these providers still expose RTSP discovery through the generic NVR
    templates. Native playback, snapshots, recordings and device events should
    be wired here when the vendor SDK/runtime is installed.
    """

    sdk_name = "vendor_sdk"

    @property
    def capabilities(self) -> VideoSourceCapabilities:
        generic = super().capabilities
        return VideoSourceCapabilities(
            supports_discovery=generic.supports_discovery,
            supports_rtsp=generic.supports_rtsp,
            supports_snapshot=False,
            supports_recording_clip=False,
            supports_native_events=False,
            supports_health=True,
            requires_sdk=True,
        )

    def get_health(self) -> VideoSourceHealth:
        return VideoSourceHealth(
            ok=False,
            status="sdk_unavailable",
            reason=f"{self.sdk_name} ainda nao esta instalado/configurado; usando RTSP como fallback.",
            metadata={"fallback": "rtsp"},
        )

    def get_snapshot(self, *, channel: int) -> bytes | None:
        return None

    def get_recording_clip(self, *, channel: int, start_iso: str, end_iso: str) -> bytes | None:
        return None

    def get_native_events(self, *, channel: int | None = None) -> list[dict]:
        return []


class HikvisionProvider(SdkBackedProvider):
    sdk_name = "Hikvision ISAPI/SDK"


class DahuaProvider(SdkBackedProvider):
    sdk_name = "Dahua NetSDK"


class IntelbrasProvider(GenericNvrProvider):
    """Usa a API HTTP CGI padrao Dahua/Intelbras (sem gateway SDK nativo).

    Snapshot, gravacoes e eventos sao obtidos direto do dispositivo via
    ``cgi-bin`` (ver HTTP_API_V3_59_Intelbras.pdf), dispensando o gateway
    nativo que os outros provedores em `SdkBackedProvider` ainda aguardam.
    """

    sdk_name = "Intelbras HTTP API"

    def _client(self) -> DahuaHttpApiClient:
        return DahuaHttpApiClient(
            host=self.config.host,
            port=int(self.config.http_port or 80),
            username=self.config.username,
            password=self.config.password,
            preset_channel_one_based=True,
        )

    @property
    def capabilities(self) -> VideoSourceCapabilities:
        generic = super().capabilities
        return VideoSourceCapabilities(
            supports_discovery=generic.supports_discovery,
            supports_rtsp=generic.supports_rtsp,
            supports_snapshot=True,
            supports_recording_clip=True,
            supports_native_events=True,
            supports_health=True,
            supports_ptz=True,
            requires_sdk=False,
        )

    def get_health(self) -> VideoSourceHealth:
        try:
            serial_number = self._client().get_serial_number()
        except DahuaHttpApiError as exc:
            return VideoSourceHealth(ok=False, status="http_api_error", reason=str(exc))
        except requests_exceptions.RequestException as exc:
            return VideoSourceHealth(ok=False, status="unreachable", reason=str(exc))
        return VideoSourceHealth(ok=True, status="ok", metadata={"serial_number": serial_number})

    def get_snapshot(self, *, channel: int) -> bytes | None:
        try:
            return self._client().get_snapshot(channel=channel)
        except (DahuaHttpApiError, requests_exceptions.RequestException):
            return None

    def get_recording_clip(self, *, channel: int, start_iso: str, end_iso: str) -> bytes | None:
        try:
            return self._client().download_recording(channel=channel, start_iso=start_iso, end_iso=end_iso)
        except (DahuaHttpApiError, requests_exceptions.RequestException):
            return None

    def get_native_events(self, *, channel: int | None = None) -> list[dict]:
        try:
            return self._client().subscribe_events(channel=channel)
        except (DahuaHttpApiError, requests_exceptions.RequestException):
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
        try:
            self._client().ptz_move(
                channel=channel,
                pan=max(-1, min(1, int(pan))),
                tilt=max(-1, min(1, int(tilt))),
                zoom=max(-1, min(1, int(zoom))),
                speed=max(1, min(8, int(speed))),
                duration_ms=max(80, min(800, int(duration_ms))),
            )
            return True
        except (DahuaHttpApiError, requests_exceptions.RequestException):
            return False
