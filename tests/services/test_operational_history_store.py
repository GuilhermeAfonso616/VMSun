from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.operational_history_store import OperationalHistoryStore


def _ts(minutes_ago: int) -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    return (now - timedelta(minutes=minutes_ago)).isoformat()


def test_operational_history_query_groups_camera_states():
    base_dir = Path("data/runtime_state/test_operational_history_store") / uuid.uuid4().hex
    shutil.rmtree(base_dir, ignore_errors=True)
    store = OperationalHistoryStore(base_dir=base_dir)
    store.record_snapshot(
        {
            "generated_at": _ts(10),
            "camera_count": 2,
            "running_count": 1,
            "cameras": [
                {
                    "camera_id": 1,
                    "camera_name": "Entrada",
                    "health_status": "running_motion_test",
                    "worker_mode": "motion_test",
                    "last_successful_inference_at": _ts(10),
                    "metrics_age_seconds": 1.0,
                    "is_running": True,
                },
                {
                    "camera_id": 2,
                    "camera_name": "Patio",
                    "health_status": "stopped",
                    "worker_mode": "stopped",
                    "is_running": False,
                },
            ],
        },
        force=True,
    )

    payload = store.query(hours=1, bucket_minutes=5)

    cameras = {item["id"]: item for item in payload["cameras"]}
    assert cameras[1]["points"].count("ia") == 1
    assert cameras[2]["points"].count("stopped") == 1
    assert payload["summary"]["samples"] == 1
    shutil.rmtree(base_dir, ignore_errors=True)


def test_operational_history_query_absolute_range():
    base_dir = Path("data/runtime_state/test_operational_history_store") / uuid.uuid4().hex
    shutil.rmtree(base_dir, ignore_errors=True)
    store = OperationalHistoryStore(base_dir=base_dir)
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    sample_at = now - timedelta(minutes=30)
    store.record_snapshot(
        {
            "generated_at": sample_at.isoformat(),
            "camera_count": 1,
            "running_count": 1,
            "cameras": [
                {
                    "camera_id": 1,
                    "camera_name": "Entrada",
                    "health_status": "running_motion_test",
                    "worker_mode": "motion_test",
                    "last_successful_inference_at": sample_at.isoformat(),
                    "metrics_age_seconds": 1.0,
                    "is_running": True,
                },
            ],
        },
        force=True,
    )

    inside = store.query(
        bucket_minutes=5,
        start_iso=(sample_at - timedelta(minutes=10)).isoformat(),
        end_iso=(sample_at + timedelta(minutes=10)).isoformat(),
    )
    assert inside["summary"]["samples"] == 1

    outside = store.query(
        bucket_minutes=5,
        start_iso=(sample_at - timedelta(hours=3)).isoformat(),
        end_iso=(sample_at - timedelta(hours=2)).isoformat(),
    )
    assert outside["summary"]["samples"] == 0
    shutil.rmtree(base_dir, ignore_errors=True)
