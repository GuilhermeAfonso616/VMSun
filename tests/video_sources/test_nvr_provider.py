import pytest

from app.video_sources.models import VideoSourceConfig
from app.video_sources.providers.generic_nvr import GenericNvrProvider
from app.video_sources.registry import build_video_source_provider
from app.services.nvr_discovery_service import discover_nvr_channels


def test_hikvision_channel_urls_are_generated_without_probe():
    provider = GenericNvrProvider(
        VideoSourceConfig(
            provider_type="generic_nvr",
            host="192.168.10.50",
            username="admin",
            password="secret",
            brand="hikvision",
            channel_count=2,
        )
    )

    profiles = provider.discover_channels(stream_kinds=("main",), probe=False)

    assert [profile.channel for profile in profiles] == [1, 2]
    assert profiles[0].stream_kind == "main"
    assert profiles[0].rtsp_url == "rtsp://admin:secret@192.168.10.50:554/Streaming/Channels/101"
    assert profiles[1].rtsp_url == "rtsp://admin:secret@192.168.10.50:554/Streaming/Channels/201"


def test_generic_nvr_probe_marks_working_profiles(monkeypatch):
    observed_options = {}

    def fake_probe(urls, **options):
        observed_options.update(options)
        return [
            {
                "url": url,
                "masked_url": url,
                "ok": "channel=2" in url,
                "error": "" if "channel=2" in url else "fail",
                "width": 1920 if "channel=2" in url else None,
                "height": 1080 if "channel=2" in url else None,
                "fps": 15.0 if "channel=2" in url else None,
            }
            for url in urls
        ]

    monkeypatch.setattr("app.video_sources.providers.generic_nvr.probe_rtsp_candidates", fake_probe)

    result = discover_nvr_channels(
        host="10.0.0.8",
        username="u",
        password="p",
        brand="dahua",
        channel_count=2,
        stream_kinds=("main",),
        probe=True,
    )

    assert result.to_dict()["working_count"] == 1
    assert observed_options == {
        "max_workers": 8,
        "allow_transport_fallback": False,
    }
    profiles = result.profiles
    assert len(profiles) == 2
    assert profiles[0].ok is False
    assert profiles[1].ok is True
    assert profiles[1].width == 1920
    assert profiles[1].height == 1080
    assert profiles[1].metadata["fps"] == 15.0
    assert profiles[1].rtsp_url.endswith("/cam/realmonitor?channel=2&subtype=0")


def test_nvr_probe_rejects_excessive_generic_candidate_count():
    provider = GenericNvrProvider(
        VideoSourceConfig(
            provider_type="generic_nvr",
            host="192.168.10.50",
            username="admin",
            password="secret",
            brand="generic",
            channel_count=128,
        )
    )

    with pytest.raises(RuntimeError, match="896 URLs"):
        provider.discover_channels(stream_kinds=("main", "sub"), probe=True)


def test_brand_uses_rtsp_templates_without_forcing_sdk_provider():
    provider = build_video_source_provider(
        VideoSourceConfig(
            provider_type="generic_nvr",
            host="10.0.0.9",
            brand="hikvision",
            channel_count=1,
        )
    )

    assert provider.capabilities.supports_rtsp is True
    assert provider.capabilities.requires_sdk is False


def test_sdk_provider_is_explicit():
    provider = build_video_source_provider(
        VideoSourceConfig(
            provider_type="hikvision_sdk",
            host="10.0.0.9",
            brand="hikvision",
            channel_count=1,
        )
    )

    assert provider.capabilities.requires_sdk is True
