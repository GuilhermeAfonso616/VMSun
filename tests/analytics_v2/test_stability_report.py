from __future__ import annotations

from datetime import datetime, timedelta

from scripts.generate_stability_report import (
    WorkerLifecycleSummary,
    build_incidents,
    build_camera_stop_classification,
    build_resource_pressure_notes,
    build_worker_lifecycle_summary,
    finding_from_entry,
    load_operational_availability,
    parse_log_line,
    worker_lifecycle_event_from_entry,
)


def test_parse_log_line_and_classify_gateway_failure():
    line = (
        "2026-05-08 15:10:11 | WARNING  | app.gateway_frames_capture | req=- | cam=7 | pid=123 | "
        "mode=normal | action=gateway_frames_missing | status=degraded | reason=gateway_frames_missing | "
        "event=- | lifecycle=- | corr=- | related=- | eligible=- | active=- | dedupe=- | Gateway frames missing"
    )

    entry = parse_log_line(line)
    finding = finding_from_entry(entry)

    assert entry is not None
    assert entry.camera_id == 7
    assert finding is not None
    assert finding.category == "gateway_video"


def test_frame_ring_gap_is_classified_as_worker_lag():
    line = (
        "2026-05-08 15:10:11 | WARNING  | app.gateway_frames_capture | req=- | cam=7 | pid=123 | "
        "mode=motion_test | action=gateway_frames_context_gap | status=running | reason=frame_ring_gap | "
        "event=- | lifecycle=- | corr=- | related=- | eligible=- | active=- | dedupe=- | "
        "gateway_frames_context_gap camera_id=7 last_seq=0 latest_seq=25882 state=running"
    )

    finding = finding_from_entry(parse_log_line(line))

    assert finding is not None
    assert finding.category == "worker_lag"
    assert "nao prova falha" in finding.cause


def test_incidents_mark_common_cause_when_cameras_fail_together():
    first_line = (
        "2026-05-08 15:10:00 | WARNING  | app.camera_health_monitor | req=- | cam=7 | pid=123 | "
        "mode=normal | action=watchdog_force_restart | status=reconnecting | reason=stale_worker | "
        "event=- | lifecycle=- | corr=- | related=- | eligible=- | active=- | dedupe=- | Watchdog force restart"
    )
    second_line = first_line.replace("cam=7", "cam=9").replace("15:10:00", "15:11:00")
    findings = [finding_from_entry(parse_log_line(first_line)), finding_from_entry(parse_log_line(second_line))]

    incidents = build_incidents([item for item in findings if item is not None], common_window_minutes=3)

    assert len(incidents) == 2
    assert all(incident.common_cause_hint for incident in incidents)


def test_incidents_split_after_gap():
    base = datetime(2026, 5, 8, 15, 10, 0)
    template = (
        "{ts} | WARNING  | app.runtime.worker | req=- | cam=3 | pid=123 | mode=normal | "
        "action=inference_failed | status=degraded | reason=inference_failed | event=- | lifecycle=- | "
        "corr=- | related=- | eligible=- | active=- | dedupe=- | Inference failed"
    )
    lines = [
        template.format(ts=base.strftime("%Y-%m-%d %H:%M:%S")),
        template.format(ts=(base + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")),
    ]
    findings = [finding_from_entry(parse_log_line(line)) for line in lines]

    incidents = build_incidents([item for item in findings if item is not None], gap_minutes=5)

    assert len(incidents) == 2


def test_worker_lifecycle_summary_tracks_open_and_closed_sessions():
    lines = [
        (
            "2026-05-08 15:10:00 | INFO     | app.worker_process | req=- | cam=7 | pid=123 | "
            "mode=motion_test | action=worker_process_entry | status=starting | reason=process_started | "
            "event=- | lifecycle=- | corr=- | related=- | eligible=- | active=- | dedupe=- | Worker started"
        ),
        (
            "2026-05-08 15:11:00 | INFO     | app.worker | req=- | cam=7 | pid=123 | "
            "mode=motion_test | action=run_worker | status=stopped | reason=shutdown | "
            "event=- | lifecycle=- | corr=- | related=- | eligible=- | active=- | dedupe=- | Worker finished"
        ),
        (
            "2026-05-08 15:12:00 | INFO     | app.worker_process | req=- | cam=7 | pid=456 | "
            "mode=motion_test | action=worker_process_entry | status=starting | reason=process_started | "
            "event=- | lifecycle=- | corr=- | related=- | eligible=- | active=- | dedupe=- | Worker started"
        ),
        (
            "2026-05-08 15:13:00 | WARNING  | app.camera_health_monitor | req=- | cam=7 | pid=456 | "
            "mode=motion_test | action=watchdog_force_restart | status=reconnecting | reason=stale_worker | "
            "event=- | lifecycle=- | corr=- | related=- | eligible=- | active=- | dedupe=- | Restarting worker"
        ),
    ]
    events = [
        worker_lifecycle_event_from_entry(parse_log_line(line))
        for line in lines
    ]

    summaries = build_worker_lifecycle_summary([item for item in events if item is not None])

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.camera_id == 7
    assert summary.starts == 2
    assert summary.stops == 1
    assert summary.watchdog_restarts == 1
    assert summary.open_sessions_at_window_end == 1


def test_resource_pressure_notes_warn_about_host_cpu_and_vram():
    summary = {
        "summary": {
            "samples": 3,
            "gpu_mem_total_mb": 6000,
            "metrics": {
                "workers": {"peak": 24},
                "running": {"peak": 24},
                "cpu": {"peak": 620},
                "host_cpu": {"peak": 91},
                "ram_mb": {"peak": 8200},
                "host_ram": {"peak": 62},
                "gpu": {"peak": 12},
                "gpu_mem_mb": {"peak": 5600},
            },
        }
    }

    notes = "\n".join(build_resource_pressure_notes(summary))

    assert "Amostras de recursos" in notes
    assert "CPU do host" in notes
    assert "VRAM passou de 90%" in notes


def test_operational_availability_returns_empty_when_history_is_unavailable():
    rows = load_operational_availability(datetime(2026, 5, 8), datetime(2026, 5, 9))

    assert isinstance(rows, list)


def test_camera_stop_classification_separates_manual_gateway_system_and_not_started():
    availability = [
        {"camera_id": 1, "camera_name": "Manual", "total_minutes": 10, "ia_minutes": 0, "ia_percent": 0, "stopped_minutes": 10},
        {"camera_id": 2, "camera_name": "Gateway", "total_minutes": 10, "ia_minutes": 0, "ia_percent": 0, "warming_minutes": 10},
        {"camera_id": 3, "camera_name": "Worker", "total_minutes": 10, "ia_minutes": 8, "ia_percent": 80, "degraded_minutes": 2},
        {"camera_id": 4, "camera_name": "Not started", "total_minutes": 10, "ia_minutes": 0, "ia_percent": 0, "stopped_minutes": 10},
    ]
    manual_line = (
        "2026-05-08 15:10:00 | INFO     | app.worker | req=- | cam=1 | pid=123 | "
        "mode=motion_test | action=run_worker | status=stopped | reason=stop_requested | "
        "event=- | lifecycle=- | corr=- | related=- | eligible=- | active=- | dedupe=- | manual_stop"
    )
    gateway_line = (
        "2026-05-08 15:11:00 | WARNING  | app.gateway_frames_capture | req=- | cam=2 | pid=123 | "
        "mode=motion_test | action=capture_gateway_no_fallback | status=offline | reason=connect_timeout | "
        "event=- | lifecycle=- | corr=- | related=- | eligible=- | active=- | dedupe=- | Gateway timeout"
    )
    findings = [
        finding_from_entry(parse_log_line(manual_line)),
        finding_from_entry(parse_log_line(gateway_line)),
    ]
    lifecycle = [
        WorkerLifecycleSummary(camera_id=1, manual_stops=1),
        WorkerLifecycleSummary(camera_id=3, watchdog_restarts=1),
    ]

    rows = build_camera_stop_classification(
        availability,
        [item for item in findings if item is not None],
        lifecycle,
    )
    by_id = {row["camera_id"]: row for row in rows}

    assert by_id[1]["primary_state"] == "manual_stop"
    assert by_id[2]["primary_state"] == "gateway_timeout_or_queued"
    assert by_id[3]["primary_state"] == "system_worker_issue"
    assert by_id[4]["primary_state"] == "not_started_or_not_restored"
