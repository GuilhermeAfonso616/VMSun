import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Camera, User
from app.web import monitor_presenter
from app.web.infrastructure import get_web_user
from app.web.routes import monitor_routes


@pytest.fixture
def monitor_http(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        first = Camera(
            name="Portaria",
            ip="10.0.0.10",
            username="operator",
            password="secret",
            rtsp_url="rtsp://operator:secret@10.0.0.10/main",
            is_deleted=False,
        )
        second = Camera(
            name="Garagem",
            ip="10.0.0.11",
            username="operator",
            password="secret",
            is_deleted=False,
        )
        db.add_all([first, second])
        db.commit()
        camera_ids = (first.id, second.id)

    application = FastAPI()
    application.include_router(monitor_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=21,
        username="admin",
        role="admin",
        is_active=True,
    )
    monkeypatch.setattr(monitor_routes, "get_scoped_db", session_factory)
    monitor_presenter._MONITOR_RESPONSE_CACHE.clear()
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(
                client=client,
                session_factory=session_factory,
                camera_ids=camera_ids,
            )
    finally:
        application.dependency_overrides.clear()
        monitor_presenter._MONITOR_RESPONSE_CACHE.clear()
        engine.dispose()


def test_monitor_data_preserves_filter_payload_and_server_cache_contract(
    monitor_http,
    monkeypatch,
):
    calls = []

    def build_payload(**kwargs):
        calls.append(kwargs)
        return {
            "stats": {"displayed": 0},
            "gateway_health": {"status": "ok"},
            "visible_cameras": [],
        }

    monkeypatch.setattr(monitor_routes, "build_monitor_live_payload", build_payload)

    url = "/monitor/data?site_name=Matriz&camera_priority=high&only_running=on&grid=9"
    first = monitor_http.client.get(url)
    second = monitor_http.client.get(url)

    assert first.status_code == 200
    assert first.json() == {
        "stats": {"displayed": 0},
        "gateway_health": {"status": "ok"},
        "cameras": [],
    }
    assert first.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert first.headers["x-server-cache"] in {"MISS", "BYPASS"}
    if first.headers["x-server-cache"] == "MISS":
        assert second.headers["x-server-cache"] == "HIT"
        assert len(calls) == 1
    assert calls[0]["site_name"] == "Matriz"
    assert calls[0]["camera_priority"] == "high"
    assert calls[0]["only_running"] is True
    assert calls[0]["only_alarm"] is False
    assert calls[0]["grid"] == 9


def test_monitor_alarm_route_normalizes_invalid_priority_and_serializes_contract(
    monitor_http,
    monkeypatch,
):
    observed = {}

    def build_payload(_db, **kwargs):
        observed.update(kwargs)
        return {
            "alarm_queue_signature": "",
            "latest_alarm_signature": "",
            "alarm_should_play": False,
            "popup_should_show": False,
            "latest_popup_alarm": None,
            "panel_alarms": [],
        }

    monkeypatch.setattr(monitor_routes, "build_monitor_alarm_payload", build_payload)

    response = monitor_http.client.get(
        "/monitor/alarms?camera_priority=unknown&only_alarm=true&grid=25"
    )

    assert response.status_code == 200
    assert response.json() == {
        "queue_session_key": "",
        "queue_session_started_at": "",
        "alarm_queue_signature": "",
        "latest_alarm_signature": "",
        "alarm_should_play": False,
        "popup_should_show": False,
        "latest_popup_alarm": None,
        "alarms": [],
    }
    assert observed["camera_priority"] is None
    assert observed["only_alarm"] is True
    assert response.headers["pragma"] == "no-cache"


def test_webrtc_diagnostics_ignores_invalid_ids_and_filters_requested_cameras(
    monitor_http,
    monkeypatch,
):
    monkeypatch.setattr(monitor_routes, "webrtc_gateway_is_enabled", lambda: True)
    monkeypatch.setattr(monitor_routes, "webrtc_public_base_url", lambda: "https://media.test")
    monkeypatch.setattr(
        monitor_routes,
        "get_webrtc_path_diagnostics",
        lambda path: {"path": path, "ready": True},
    )
    selected_id = monitor_http.camera_ids[1]

    response = monitor_http.client.get(
        f"/monitor/webrtc/diagnostics?camera_ids=invalid,{selected_id},{selected_id}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["public_base_url"] == "https://media.test"
    assert [item["id"] for item in payload["items"]] == [selected_id]
    assert payload["items"][0]["diagnostics"]["ready"] is True


def test_webrtc_monitor_page_keeps_dev_only_access_contract(monitor_http):
    response = monitor_http.client.get("/monitor/webrtc", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_tracks_endpoint_disables_cache_and_passes_database_explicitly(
    monitor_http,
    monkeypatch,
):
    observed = {}

    def build_payload(db, **kwargs):
        observed["has_query"] = hasattr(db, "query")
        observed.update(kwargs)
        return {"ok": True, "cameras": {}}

    monkeypatch.setattr(monitor_routes, "build_monitor_tracks_payload", build_payload)

    response = monitor_http.client.get(
        "/monitor/tracks?camera_ids=1,2&max_age_seconds=2.5&min_confidence=0.7"
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "cameras": {}}
    assert observed == {
        "has_query": True,
        "camera_ids": "1,2",
        "max_age_seconds": 2.5,
        "min_confidence": 0.7,
    }
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert response.headers["expires"] == "0"


def test_tracks_stream_preserves_sse_event_and_proxy_headers(monkeypatch):
    closed = []

    class FakeDb:
        def close(self):
            closed.append(True)

    class ConnectedRequest:
        async def is_disconnected(self):
            return False

    monkeypatch.setattr(monitor_routes, "get_scoped_db", FakeDb)
    monkeypatch.setattr(
        monitor_routes,
        "build_monitor_tracks_payload",
        lambda _db, **_kwargs: {"ok": True, "cameras": {}},
    )

    async def read_first_event():
        response = await monitor_routes.monitor_tracks_stream(
            ConnectedRequest(),
            camera_ids="7",
            interval_ms=10,
        )
        chunk = await anext(response.body_iterator)
        await response.body_iterator.aclose()
        return response, chunk

    response, chunk = asyncio.run(read_first_event())

    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    event_line, data_line = chunk.rstrip("\n").split("\n")
    payload = json.loads(data_line.removeprefix("data: "))

    assert event_line == "event: tracks"
    assert chunk.endswith("\n\n")
    assert payload["ok"] is True
    assert payload["cameras"] == {}
    assert int(payload["sse_sent_at_ns"]) > 0
    assert closed == [True]


def test_ptz_inspect_returns_persisted_state_and_schedules_first_probe(
    monitor_http,
    monkeypatch,
):
    camera_id = monitor_http.camera_ids[0]
    discovered = []
    monkeypatch.setattr(
        monitor_routes,
        "prepare_ptz_inspection",
        lambda selected_id, force=False: (
            {
                "status": "probing",
                "status_label": "Verificando PTZ...",
                "ptz_capable": False,
                "capabilities": {"ptz": False},
            },
            True,
        ),
    )
    monkeypatch.setattr(
        monitor_routes,
        "discover_and_persist_ptz",
        lambda selected_id, owner_id: discovered.append((selected_id, owner_id)),
    )

    response = monitor_http.client.get(f"/monitor/cameras/{camera_id}/ptz/inspect")

    assert response.status_code == 202
    assert response.json()["status"] == "probing"
    assert discovered == [(camera_id, 21)]


def test_ptz_move_and_stop_preserve_backend_contract(monitor_http, monkeypatch):
    camera_id = monitor_http.camera_ids[0]
    monkeypatch.setattr(
        monitor_routes,
        "move_ptz",
        lambda camera, **kwargs: {
            "backend": "native_sdk",
            "driver": "intelbras_http",
            "continuous": True,
            "movement_id": "movement-1",
        },
    )
    monkeypatch.setattr(
        monitor_routes,
        "stop_ptz",
        lambda camera, **kwargs: {
            "backend": "native_sdk",
            "driver": "intelbras_http",
            "stopped": True,
            "movement_id": "movement-1",
        },
    )

    moved = monitor_http.client.post(
        f"/monitor/cameras/{camera_id}/ptz/move",
        json={"pan": 1, "tilt": 0, "zoom": 0},
    )
    stopped = monitor_http.client.post(f"/monitor/cameras/{camera_id}/ptz/stop", json={})

    assert moved.status_code == 200
    assert moved.json()["movement_id"] == "movement-1"
    assert moved.json()["continuous"] is True
    assert stopped.status_code == 200
    assert stopped.json()["movement_id"] == "movement-1"
    assert stopped.json()["stopped"] is True


def test_ptz_3d_forwards_normalized_selection_to_monitor_service(monitor_http, monkeypatch):
    camera_id = monitor_http.camera_ids[0]
    calls = []
    monkeypatch.setattr(
        monitor_routes,
        "position_ptz_3d",
        lambda camera, **kwargs: calls.append((camera.id, kwargs)) or {"backend": "native_sdk"},
    )

    response = monitor_http.client.post(
        f"/monitor/cameras/{camera_id}/ptz/3d",
        json={"x_start": 20, "y_start": 30, "x_end": 220, "y_end": 230},
    )

    assert response.status_code == 200
    assert response.json()["backend"] == "native_sdk"
    assert calls == [(camera_id, {"owner_id": 21, "x_start": 20, "y_start": 30, "x_end": 220, "y_end": 230})]


def test_ptz_3d_dimensions_returns_displayed_stream_size(monitor_http, monkeypatch):
    camera_id = monitor_http.camera_ids[0]
    calls = []
    monkeypatch.setattr(
        monitor_routes,
        "get_camera_stream_dimensions",
        lambda camera, owner_id: calls.append((camera.id, owner_id)) or (1920, 1080),
    )

    response = monitor_http.client.get(
        f"/monitor/cameras/{camera_id}/ptz/3d/dimensoes"
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "width": 1920, "height": 1080}
    assert calls == [(camera_id, 21)]


def test_ptz_presets_can_be_listed_and_activated_from_monitor(monitor_http, monkeypatch):
    camera_id = monitor_http.camera_ids[0]
    monkeypatch.setattr(
        monitor_routes,
        "list_ptz_presets",
        lambda camera, owner_id: [{"token": "2", "name": "Portao"}],
    )
    calls = []
    monkeypatch.setattr(
        monitor_routes,
        "goto_ptz_preset",
        lambda camera, **kwargs: calls.append((camera.id, kwargs)) or {
            "backend": "native_sdk",
            "driver": "intelbras_http",
            "preset_token": kwargs["preset_token"],
        },
    )
    listed = monitor_http.client.get(f"/monitor/cameras/{camera_id}/ptz/presets")
    activated = monitor_http.client.post(
        f"/monitor/cameras/{camera_id}/ptz/presets/goto",
        json={"preset_token": "2"},
    )
    assert listed.status_code == 200
    assert listed.json()["presets"] == [{"token": "2", "name": "Portao"}]
    assert activated.status_code == 200
    assert activated.json()["preset_token"] == "2"
    assert calls == [(camera_id, {"owner_id": 21, "preset_token": "2"})]


def test_ptz_unknown_payload_triggers_first_inspection_in_monitor_ui():
    source = Path("app/static/js/monitor_vms.js").read_text(encoding="utf-8")

    assert 'status === "unknown"' in source
    assert "ptzInfoNeedsInspection(selectedCameraPtzInfo)" in source


def test_monitor_ptz_refreshes_presets_on_connection_and_exposes_dropdown():
    source = Path("app/static/js/monitor_vms.js").read_text(encoding="utf-8")
    template = Path("templates/monitor_vms_new.html").read_text(encoding="utf-8")

    assert 'id="monitorPtzPresetSelect"' in template
    assert 'id="monitorPtzPresetSet"' not in template
    assert "refreshSelectedCameraPtzPresets(normalizedId)" in source
    assert '"/ptz/presets?_ts="' in source
    assert '"/ptz/presets/goto"' in source
    assert '"/ptz/presets/set"' not in source


def test_monitor_sandbox_mode_present():
    template = Path("templates/monitor_vms_new.html").read_text(encoding="utf-8")
    source = Path("app/static/js/monitor_vms.js").read_text(encoding="utf-8")

    assert '<option value="sandbox">Sandbox (Livre)</option>' in template
    assert 'id="vmsSandboxControls"' in template
    assert 'id="sandboxAddTileBtn"' in template
    assert 'id="sandboxRemoveTileBtn"' in template
    assert '"sandbox"' in source
    assert "vms-sandbox-resize-handle" in source
    assert "vms-span-btn" in source
