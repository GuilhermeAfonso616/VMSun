from types import SimpleNamespace
from unittest.mock import patch

from starlette.responses import RedirectResponse

from app.web.routes.event_actions_routes import _remote_clip_redirect, _valid_local_file


def test_remote_clip_redirect_uses_onedrive_download_url():
    event = SimpleNamespace(id=123, clip_remote_status="uploaded", clip_remote_item_id="item-1")

    with patch(
        "app.web.routes.event_actions_routes.onedrive_client.item_download_url",
        return_value="https://download.example.test/clip.mp4",
    ) as download_url:
        response = _remote_clip_redirect(event)

    download_url.assert_called_once_with("item-1")
    assert isinstance(response, RedirectResponse)
    assert response.status_code == 307
    assert response.headers["location"] == "https://download.example.test/clip.mp4"


def test_valid_local_file_rejects_empty_clip(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"")

    assert _valid_local_file(clip) is False

    clip.write_bytes(b"mp4")
    assert _valid_local_file(clip) is True
