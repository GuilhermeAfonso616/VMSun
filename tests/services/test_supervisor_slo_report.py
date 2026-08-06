from datetime import datetime, timedelta, timezone

from scripts.generate_supervisor_slo_report import summarize_window


def test_slo_excludes_stopped_and_unknown_from_denominator():
    now = datetime.now(timezone.utc)
    samples = [
        {
            "ts": (now - timedelta(minutes=index)).isoformat(),
            "cameras": [
                {"id": 41, "name": "Camera 41", "state": state},
            ],
        }
        for index, state in enumerate(["ia", "online", "degraded", "stopped", "unknown"])
    ]

    result = summarize_window(samples, [], now=now, hours=24)
    camera = result["cameras"][0]

    assert camera["eligible_samples"] == 3
    assert camera["availability_percent"] == 66.67
    assert camera["ia_availability_percent"] == 33.33
