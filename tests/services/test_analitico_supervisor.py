import argparse

import scripts.analitico_supervisor as supervisor_module
from scripts.analitico_supervisor import (
    AnaliticoSupervisor,
    detect_configuration_drift,
    evaluate_camera,
)


def test_email_notification_is_optional_redacted_and_rate_limited(tmp_path, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def starttls(self, **_kwargs):
            return None

        def login(self, *_args):
            return None

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(supervisor_module.smtplib, "SMTP", FakeSMTP)
    supervisor = AnaliticoSupervisor(build_args(
        tmp_path,
        smtp_host="smtp.example.test",
        smtp_from="supervisor@example.test",
        smtp_to="ops@example.test",
    ))

    supervisor.send_email({"event": "circuit_open", "password": "secret"})
    supervisor.send_email({"event": "circuit_open", "password": "secret"})

    assert len(sent) == 1
    assert sent[0]["Subject"] == "[SunOrus] Supervisor: circuit_open"
    assert "secret" not in sent[0].get_content()


def build_args(tmp_path, **overrides):
    values = {
        "mode": "recover",
        "runtime_url": "http://127.0.0.1:8001",
        "gateway_url": "http://127.0.0.1:8090/healthz",
        "token": "",
        "state_dir": str(tmp_path),
        "prometheus_file": str(tmp_path / "supervisor.prom"),
        "compose_env_file": str(tmp_path / ".env.docker"),
        "interval_seconds": 10.0,
        "timeout": 1.0,
        "stale_seconds": 90.0,
        "failure_threshold": 1,
        "cooldown_seconds": 0.0,
        "max_actions_per_hour": 3,
        "max_backoff_seconds": 900.0,
        "healthy_reset_cycles": 3,
        "quarantine_after_actions": 3,
        "quarantine_seconds": 900.0,
        "broad_failure_ratio": 0.9,
        "orphan_failure_threshold": 3,
        "max_orphan_cleanup_per_cycle": 8,
        "orphan_cleanup_cooldown_seconds": 300.0,
        "circuit_log_interval_seconds": 300.0,
        "canary_enabled": False,
        "canary_interval_seconds": 300.0,
        "webhook_url": "",
        "webhook_cooldown_seconds": 300.0,
        "incident_retention_count": 20,
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_from": "",
        "smtp_to": "",
        "smtp_starttls": True,
        "smtp_ssl": False,
        "smtp_cooldown_seconds": 300.0,
        "allow_runtime_restart": False,
        "runtime_restart_threshold": 6,
        "max_runtime_restarts_per_hour": 2,
        "docker_host": "unix:///tmp/docker.sock",
        "runtime_container": "runtime",
        "once": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_camera_decisions_cover_missing_dead_stale_and_healthy():
    base = {"camera_id": 41, "desired_running": True, "health_status": "running"}

    assert evaluate_camera(base, stale_seconds=90).reason == "worker_missing"
    assert evaluate_camera({**base, "worker": {"alive": False}}, stale_seconds=90).reason == "worker_dead"
    ownership = evaluate_camera(
        {**base, "worker": {"alive": True}, "ownership_matches": False},
        stale_seconds=90,
    )
    assert ownership.reason == "worker_ownership_mismatch"
    assert ownership.force_restart is True
    stale = evaluate_camera(
        {**base, "worker": {"alive": True}, "latest_activity_age_seconds": 100},
        stale_seconds=90,
    )
    assert stale.reason == "activity_stale"
    assert stale.force_restart is True
    healthy = evaluate_camera(
        {**base, "worker": {"alive": True}, "latest_activity_age_seconds": 1},
        stale_seconds=90,
    )
    assert healthy.healthy is True


def test_gateway_queue_is_upstream_and_not_recoverable_by_worker_restart():
    decision = evaluate_camera(
        {
            "camera_id": 41,
            "desired_running": True,
            "health_status": "warming_up",
            "gateway_state": "queued",
            "worker": {"alive": True},
        },
        stale_seconds=90,
    )

    assert decision.healthy is False
    assert decision.reason == "gateway_queued"
    assert decision.recoverable is False


def test_configuration_drift_reads_compose_env(tmp_path):
    env_path = tmp_path / ".env.docker"
    env_path.write_text(
        "GATEWAY_NODE_MAX_ACTIVE_CAMERAS=24\n"
        "ANALYTIC_GPU_MAX_ACTIVE_WORKERS=24\n",
        encoding="utf-8",
    )

    drift = detect_configuration_drift(
        snapshot={"runtime_tuning": {"max_active_workers": 24}},
        gateway={"node_max_active_cameras": 16},
        compose_env_file=env_path,
    )

    assert drift == [{
        "key": "GATEWAY_NODE_MAX_ACTIVE_CAMERAS",
        "expected": "24",
        "actual": 16,
        "component": "camera-gateway",
    }]


def test_run_once_recovers_only_unhealthy_camera(tmp_path):
    supervisor = AnaliticoSupervisor(build_args(tmp_path))
    supervisor.fetch_gateway = lambda: (True, {"ok": True, "running": 1})
    supervisor.fetch_snapshot = lambda: {
        "runtime": {"ready": True},
        "summary": {},
        "gateway": {"orphan_camera_ids": []},
        "cameras": [
            {"camera_id": 41, "desired_running": True, "health_status": "running", "worker": None},
            {
                "camera_id": 42,
                "desired_running": True,
                "health_status": "running",
                "worker": {"alive": True},
                "latest_activity_age_seconds": 1,
            },
        ],
    }
    recovered = []
    supervisor.reconcile_camera = lambda decision: recovered.append(decision.camera_id) or {"ok": True}

    supervisor.run_once()

    assert recovered == [41]
    assert supervisor.circuit_open is False
    assert supervisor.recovery_total == 1


def test_broad_failure_opens_circuit_and_blocks_recovery(tmp_path):
    supervisor = AnaliticoSupervisor(build_args(tmp_path, broad_failure_ratio=0.3))
    supervisor.capture_incident = lambda **_kwargs: None
    supervisor.fetch_gateway = lambda: (True, {"ok": True, "running": 1})
    supervisor.fetch_snapshot = lambda: {
        "runtime": {"ready": True},
        "summary": {},
        "gateway": {"orphan_camera_ids": []},
        "cameras": [
            {"camera_id": 41, "desired_running": True, "health_status": "offline", "worker": None},
            {"camera_id": 42, "desired_running": True, "health_status": "offline", "worker": None},
        ],
    }
    recovered = []
    supervisor.reconcile_camera = lambda decision: recovered.append(decision.camera_id) or {"ok": True}

    supervisor.run_once()

    assert supervisor.circuit_open is True
    assert recovered == []
    assert supervisor.audit_action_total == 2

    supervisor.run_once()
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "circuit_closed" not in events


def test_audit_never_cleans_gateway_orphans(tmp_path):
    supervisor = AnaliticoSupervisor(build_args(tmp_path, mode="audit", orphan_failure_threshold=2))
    supervisor.fetch_gateway = lambda: (True, {"ok": True, "running": 0})
    supervisor.fetch_snapshot = lambda: {
        "runtime": {"ready": True},
        "summary": {},
        "gateway": {"orphan_camera_ids": [99]},
        "cameras": [],
    }
    cleaned = []
    supervisor.reconcile_gateway = lambda **kwargs: cleaned.append(kwargs) or {"ok": True}

    supervisor.run_once()
    supervisor.run_once()

    assert cleaned == []
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "gateway_would_cleanup" in events


def test_recover_cleans_confirmed_gateway_orphans_once(tmp_path):
    supervisor = AnaliticoSupervisor(
        build_args(
            tmp_path,
            mode="recover",
            orphan_failure_threshold=2,
            orphan_cleanup_cooldown_seconds=0.0,
        )
    )
    supervisor.fetch_gateway = lambda: (True, {"ok": True, "running": 0})
    supervisor.fetch_snapshot = lambda: {
        "runtime": {"ready": True},
        "summary": {},
        "gateway": {"orphan_camera_ids": [99]},
        "cameras": [],
    }
    cleaned = []
    supervisor.reconcile_gateway = lambda **kwargs: cleaned.append(kwargs) or {"ok": True}

    supervisor.run_once()
    supervisor.run_once()

    assert cleaned == [{"recover": True}]


def test_repeated_recovery_quarantines_camera(tmp_path):
    supervisor = AnaliticoSupervisor(
        build_args(
            tmp_path,
            failure_threshold=1,
            cooldown_seconds=0.0,
            quarantine_after_actions=2,
        )
    )
    recovered = []
    supervisor.reconcile_camera = lambda decision: recovered.append(decision.camera_id) or {"ok": True}
    decision = evaluate_camera(
        {"camera_id": 41, "desired_running": True, "health_status": "running"},
        stale_seconds=90,
    )

    supervisor.handle_decision(decision, now=100.0, actions_enabled=True)
    supervisor.handle_decision(decision, now=101.0, actions_enabled=True)

    assert recovered == [41]
    assert supervisor.camera_states[41].quarantine_until > 101.0
