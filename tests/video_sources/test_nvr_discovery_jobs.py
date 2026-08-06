import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.camera.rtsp_discovery import probe_rtsp_url_details_bounded
from app.services.nvr_discovery_jobs import NvrDiscoveryJobManager, probe_worker_count
from app.video_sources.models import StreamProfile
from app.web.infrastructure import templates
from app.web.routes.nvr_routes import _restore_nvr_discovery_password


def _profile(channel: int) -> StreamProfile:
    url = f"rtsp://camera.example/channel/{channel}"
    return StreamProfile(
        provider_type="generic_nvr",
        source_brand="dahua",
        channel=channel,
        stream_kind="sub",
        name=f"Canal {channel} sub",
        rtsp_url=url,
        masked_rtsp_url=url,
    )


def _wait_completed(job, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = job.public_snapshot()
        if snapshot["status"] == "completed":
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"job did not complete: {job.public_snapshot()}")


def test_bounded_probe_decodes_one_frame_without_reencoding(monkeypatch):
    def fake_run(command, **kwargs):
        assert command[0] == "ffmpeg"
        assert "rtsp://camera.example/stream" in command
        assert kwargs["timeout"] == 7.0
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert command[command.index("-f") + 1] == "null"
        assert "mjpeg" not in command
        assert "-rw_timeout" not in command
        return SimpleNamespace(stderr=b"", returncode=0)

    monkeypatch.setattr("app.camera.rtsp_discovery.subprocess.run", fake_run)

    result = probe_rtsp_url_details_bounded(
        "rtsp://camera.example/stream",
        timeout_seconds=7,
    )

    assert result["ok"] is True
    assert result["width"] is None
    assert result["height"] is None
    assert result["timed_out"] is False


def test_dahua_and_intelbras_discovery_use_one_probe_at_a_time():
    assert probe_worker_count({"brand": "dahua"}) == 1
    assert probe_worker_count({"brand": "intelbras"}) == 1
    assert probe_worker_count({"brand": "hikvision"}) == 4


def test_bounded_probe_reports_hard_timeout(monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("app.camera.rtsp_discovery.subprocess.run", fake_run)

    result = probe_rtsp_url_details_bounded(
        "rtsp://camera.example/stream",
        timeout_seconds=5,
    )

    assert result["ok"] is False
    assert result["timed_out"] is True
    assert "5s" in result["error"]


def test_bounded_probe_reports_authentication_failure(monkeypatch):
    def fake_run(command, **kwargs):
        return SimpleNamespace(
            stdout=b"",
            stderr=b"Server returned 401 Unauthorized (authorization failed)",
            returncode=8,
        )

    monkeypatch.setattr("app.camera.rtsp_discovery.subprocess.run", fake_run)

    result = probe_rtsp_url_details_bounded("rtsp://user:secret@camera.example/stream")

    assert result["ok"] is False
    assert result["error"] == "NVR recusou o usuario ou a senha (401 Unauthorized)"
    assert "secret" not in str(result)


def test_discovery_reuses_cached_password(monkeypatch):
    monkeypatch.setattr(
        "app.web.routes.nvr_routes.get_nvr_discovery_cache",
        lambda token: {
            "host": "camera.example",
            "username": "admin",
            "password": "secret",
        }
        if token == "valid-token"
        else None,
    )
    values = {
        "host": "camera.example",
        "username": "admin",
        "password": "",
        "credential_token": "valid-token",
    }

    _restore_nvr_discovery_password(values)

    assert values["password"] == "secret"


def test_discovery_rejects_username_without_password():
    values = {
        "host": "camera.example",
        "username": "admin",
        "password": "",
        "credential_token": "",
    }

    with pytest.raises(ValueError, match="Informe a senha"):
        _restore_nvr_discovery_password(values)


def test_job_checkpoints_and_retries_only_timeouts(monkeypatch):
    attempts = {}

    def fake_probe(url, **kwargs):
        attempts[url] = attempts.get(url, 0) + 1
        if url.endswith("/2") and attempts[url] == 1:
            return {"ok": False, "error": "timeout", "timed_out": True}
        return {
            "ok": True,
            "error": "",
            "timed_out": False,
            "width": 640,
            "height": 360,
            "fps": None,
        }

    monkeypatch.setattr("app.services.nvr_discovery_jobs.probe_rtsp_url_details_bounded", fake_probe)
    manager = NvrDiscoveryJobManager()
    job = manager.create(
        owner_user_id=42,
        host="camera.example",
        username="user",
        password="secret",
        form_values={"brand": "dahua", "probe": True},
        profiles=[_profile(1), _profile(2), _profile(3)],
        probe_enabled=True,
    )

    first = _wait_completed(job)
    assert first["counts"]["ok"] == 2
    assert first["counts"]["timeout"] == 1
    assert "secret" not in str(first)
    assert "rtsp://" not in str(first)

    resumed = manager.resume(job.token, owner_user_id=42)
    assert resumed is job
    second = _wait_completed(job)
    assert second["counts"]["ok"] == 3
    assert second["counts"]["timeout"] == 0
    assert attempts["rtsp://camera.example/channel/1"] == 1
    assert attempts["rtsp://camera.example/channel/2"] == 2
    assert attempts["rtsp://camera.example/channel/3"] == 1


def test_job_token_is_scoped_to_owner(monkeypatch):
    monkeypatch.setattr(
        "app.services.nvr_discovery_jobs.probe_rtsp_url_details_bounded",
        lambda *args, **kwargs: {"ok": True, "error": "", "timed_out": False},
    )
    manager = NvrDiscoveryJobManager()
    job = manager.create(
        owner_user_id=7,
        host="camera.example",
        username="user",
        password="secret",
        form_values={},
        profiles=[_profile(1)],
        probe_enabled=True,
    )

    assert manager.get(job.token, owner_user_id=8) is None
    assert manager.get(job.token, owner_user_id=7) is job


def test_nvr_template_uses_real_job_progress_and_resume_controls():
    template = Path("templates/nvr_sources.html").read_text(encoding="utf-8")

    assert "/video-sources/nvr/discover/start" in template
    assert "/status" in template
    assert "Tentar novamente os timeouts" in template
    assert "A busca continua no servidor" in template
    assert 'name="credential_token"' in template
    assert "{% if not values.credential_token %}required{% endif %}" in template
    assert "estimatedSeconds" not in template


def test_nvr_template_can_hide_failed_profiles_without_hidden_selection():
    template = Path("templates/nvr_sources.html").read_text(encoding="utf-8")

    assert 'id="nvrHideFailures"' in template
    assert 'id="nvrProfilesTable"' in template
    assert 'class="nvr-profile-row"' in template
    assert 'data-failed="{% if values.probe and not item.ok and not item.existing_id %}true' in template
    assert 'row.hidden = shouldHide && failed;' in template
    assert 'if (box && !box.disabled) box.checked = false;' in template
    assert 'if (mode !== "clear" && rowIsHidden(box)) return;' in template


def test_nvr_template_marks_only_real_probe_failures_as_hidden_candidates():
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/video-sources/nvr",
            "raw_path": b"/video-sources/nvr",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "root_path": "",
        }
    )
    common = {
        "resolution_label": None,
        "fps": None,
        "selected": False,
        "masked_rtsp_url": "rtsp://user:***@camera/stream",
        "existing_name": None,
    }
    profiles = [
        {**common, "index": 0, "channel": 1, "stream_kind": "main", "ok": False, "existing_id": None, "error": "failed"},
        {**common, "index": 1, "channel": 1, "stream_kind": "sub", "ok": True, "existing_id": None, "error": ""},
        {**common, "index": 2, "channel": 2, "stream_kind": "main", "ok": False, "existing_id": 73, "existing_name": "Camera", "error": ""},
    ]

    rendered = templates.env.get_template("nvr_sources.html").render(
        request=request,
        error=None,
        message=None,
        created=None,
        skipped=None,
        credential_token="credential",
        form_values={"brand": "dahua", "stream_kinds": ["main", "sub"], "probe": True},
        brand_options=[],
        profiles=profiles,
        channel_health=[],
        active_job_token=None,
        completed_job_token=None,
        discovery_job_counts={"timeout": 0},
    )

    assert rendered.count('data-failed="true"') == 1
    assert rendered.count('data-failed="false"') == 2


def test_nvr_template_renders_active_job_token():
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/video-sources/nvr",
            "raw_path": b"/video-sources/nvr",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "root_path": "",
        }
    )
    rendered = templates.env.get_template("nvr_sources.html").render(
        request=request,
        error=None,
        message=None,
        created=None,
        skipped=None,
        credential_token=None,
        form_values={"brand": "dahua", "stream_kinds": ["main", "sub"]},
        brand_options=[],
        profiles=None,
        channel_health=[],
        active_job_token="job-123",
        discovery_job_counts={"timeout": 0},
    )

    assert 'const initialJobToken = "job-123"' in rendered
    assert 'const completedJobToken = null' in rendered
