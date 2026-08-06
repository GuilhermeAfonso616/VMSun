from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_stability_campaign.py"
SPEC = importlib.util.spec_from_file_location("run_stability_campaign", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
campaign = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = campaign
SPEC.loader.exec_module(campaign)


def _sample(*, healthy: bool = True, submitted: int = 10, dropped: int = 0):
    endpoint_ok = {"ok": True, "data": {}}
    state = "running_motion_test" if healthy else "degraded"
    return {
        "endpoints": {
            "runtime_ready": endpoint_ok,
            "runtime_cameras": {
                "ok": True,
                "data": {
                    "cameras": [
                        {
                            "camera_id": 7,
                            "health_status": state,
                            "is_running": healthy,
                        }
                    ]
                },
            },
            "gateway_health": endpoint_ok,
            "gateway_cameras": endpoint_ok,
        },
        "host": {"cpu_percent": 20, "ram_percent": 30},
        "gpu": [{"utilization_gpu_percent": 40, "memory_used_mb": 2000, "memory_total_mb": 10000}],
        "canary": {"ok": True, "duration_ms": 100, "data": {"ok": True, "total_ms": 90}},
        "worker_metrics": {
            "7": {
                "infer_ms": 80,
                "inference_pool_submitted": submitted,
                "inference_pool_dropped_oldest": dropped,
            }
        },
        "docker": {
            "inspect": [
                {
                    "Name": "/server-analiticos-runtime",
                    "RestartCount": 0,
                    "State": {"OOMKilled": False},
                }
            ]
        },
    }


def test_sanitize_redacts_secrets_and_url_credentials():
    payload = campaign.sanitize(
        {
            "password": "abc",
            "source_url": "rtsp://admin:secret@10.0.0.2:554/live",
            "plain": "ok",
        }
    )

    assert payload["password"] == "<redacted>"
    assert payload["source_url"] == "rtsp://<redacted>@10.0.0.2:554/live"
    assert payload["plain"] == "ok"
    assert campaign.sanitize(
        "camera source=rtsp://admin:secret@10.0.0.2/live?token=abc status=up"
    ) == "camera source=rtsp://<redacted>@10.0.0.2/live status=up"


def test_parse_env_file_keeps_operational_keys_only_and_redacts():
    class FakePath:
        @staticmethod
        def exists():
            return True

        @staticmethod
        def read_text(**_kwargs):
            return (
                "GATEWAY_NODE_MAX_ACTIVE_CAMERAS=16\n"
                "INFERENCE_POOL_CENTRAL_URL=http://user:pass@runtime:8001\n"
                "POSTGRES_PASSWORD=do-not-copy\n"
            )

    values = campaign.parse_env_file(FakePath())

    assert values["GATEWAY_NODE_MAX_ACTIVE_CAMERAS"] == "16"
    assert values["INFERENCE_POOL_CENTRAL_URL"] == "http://<redacted>@runtime:8001"
    assert "POSTGRES_PASSWORD" not in values


def test_stage_stats_approves_healthy_stage():
    stats = campaign.StageStats(
        name="ramp_1",
        planned_seconds=30,
        interval_seconds=15,
        expected_camera_ids={7},
        target_camera_count=1,
        thresholds=campaign.Thresholds(),
    )
    stats.add(_sample(submitted=10), [])
    stats.add(_sample(submitted=20), [])

    summary = stats.finish()

    assert summary["passed"] is True
    assert summary["collector_coverage_percent"] == 100.0
    assert summary["camera_availability_percent"] == 100.0
    assert summary["drop_rate_percent"] == 0.0


def test_stage_stats_marks_missing_camera_and_open_incident():
    stats = campaign.StageStats(
        name="ramp_1",
        planned_seconds=15,
        interval_seconds=15,
        expected_camera_ids={7},
        target_camera_count=1,
        thresholds=campaign.Thresholds(),
    )
    sample = _sample(healthy=False)
    stats.add(sample, [{"line": "ERROR inference_failed"}])

    summary = stats.finish()

    assert summary["passed"] is False
    assert "camera_availability" in summary["failures"]
    assert "target_camera_count_not_reached" in summary["failures"]
    assert "open_camera_incident" in summary["failures"]
    assert summary["log_categories"]["inference"] == 1


def test_discover_expected_camera_ids_prefers_supervisor_desired_state():
    sample = {
        "endpoints": {
            "supervisor_snapshot": {
                "data": {
                    "cameras": [
                        {"camera_id": 3, "desired_running": True},
                        {"camera_id": 4, "desired_running": False},
                    ]
                }
            },
            "runtime_cameras": {"data": {"cameras": [{"camera_id": 9, "is_running": True}]}},
        }
    }

    assert campaign.discover_expected_camera_ids(sample) == {3}


def test_control_preflight_requires_canary_and_all_control_dependencies():
    sample = {
        "endpoints": {
            name: {"ok": True}
            for name in (
                "runtime_ready",
                "runtime_cameras",
                "supervisor_snapshot",
                "gateway_health",
                "gateway_cameras",
            )
        },
        "canary": {"ok": False},
    }

    assert campaign.validate_control_preflight(sample) == ["inference_canary"]
