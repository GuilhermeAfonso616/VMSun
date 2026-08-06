from __future__ import annotations

import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from app.core.config import settings
from app.services.runtime_client import get_runtime_health_snapshot


RUNNING = {"running", "running_motion_test"}

# Estados de saude que viram alerta diretamente: (severidade, tipo, motivo).
# offline/degradada/reconectando ja saem consolidados do watchdog com os
# thresholds de saude, entao a central so os traduz para alertas.
_STATE_ALERTS: dict[str, tuple[str, str, str]] = {
    "offline": ("critical", "offline", "Camera offline (sem frames)"),
    "reconnecting": ("warning", "reconnecting", "Reconectando (stream instavel)"),
    "degraded": ("warning", "degraded", "Sinal instavel (frames atrasados)"),
}

_CACHE_LOCK = Lock()
_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0
_CACHE_TTL = 10.0


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _age_seconds(value: Any, now: datetime) -> float | None:
    dt = _parse(value)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds())


def _duration_label(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}min"
    h, mm = divmod(m, 60)
    if h < 24:
        return f"{h}h {mm}min" if mm else f"{h}h"
    d, hh = divmod(h, 24)
    return f"{d}d {hh}h" if hh else f"{d}d"


def _make_alert(cam: dict[str, Any], atype: str, severity: str, reason: str, since_raw: Any, age: float | None) -> dict[str, Any]:
    since_dt = _parse(since_raw)
    return {
        "camera_id": cam.get("camera_id"),
        "camera": str(cam.get("camera_name") or cam.get("name") or f"Camera {cam.get('camera_id')}"),
        "type": atype,
        "severity": severity,
        "reason": reason,
        "since": since_dt.replace(microsecond=0).isoformat() if since_dt else None,
        "duration_seconds": int(age) if age is not None else None,
        "duration_label": _duration_label(age),
        "restart_count": int(cam.get("restart_count") or 0),
        "health_status": str(cam.get("health_status") or ""),
    }


def _compute() -> dict[str, Any]:
    snapshot = get_runtime_health_snapshot()
    cameras = snapshot.get("cameras", []) if isinstance(snapshot, dict) else []
    now = _now()

    ia_enabled = bool(getattr(settings, "alert_ia_inactive_enabled", True))
    ia_threshold = float(getattr(settings, "alert_ia_inactive_seconds", 900.0) or 900.0)
    fresh_max = float(getattr(settings, "alert_metrics_fresh_seconds", 90.0) or 90.0)

    alerts: list[dict[str, Any]] = []
    for cam in cameras:
        if not isinstance(cam, dict):
            continue
        status = str(cam.get("health_status") or "").strip().lower()

        if status in _STATE_ALERTS:
            severity, atype, reason = _STATE_ALERTS[status]
            since_raw = cam.get("last_status_change_at") or (cam.get("last_frame_at") if status == "offline" else None)
            alerts.append(_make_alert(cam, atype, severity, reason, since_raw, _age_seconds(since_raw, now)))
            continue

        if status in RUNNING and ia_enabled:
            metrics_age = cam.get("metrics_age_seconds")
            try:
                fresh = metrics_age is not None and float(metrics_age) <= fresh_max
            except (TypeError, ValueError):
                fresh = False
            inf_age = _age_seconds(cam.get("last_successful_inference_at"), now)
            # So alerta se a IA JA funcionou e ficou muda (com o stream saudavel):
            # evita falso positivo em camera recem-iniciada ou cena parada eterna.
            if fresh and inf_age is not None and inf_age > ia_threshold:
                reason = f"Sem deteccoes da IA ha {_duration_label(inf_age)} (pode ser cena parada)"
                alerts.append(_make_alert(cam, "ia_inactive", "warning", reason, cam.get("last_successful_inference_at"), inf_age))

    sev_order = {"critical": 0, "warning": 1}
    alerts.sort(key=lambda a: (sev_order.get(a["severity"], 9), -(a["duration_seconds"] or 0)))

    counts = {
        "critical": sum(1 for a in alerts if a["severity"] == "critical"),
        "warning": sum(1 for a in alerts if a["severity"] == "warning"),
        "total": len(alerts),
    }
    return {
        "generated_at": now.replace(microsecond=0).isoformat(),
        "counts": counts,
        "alerts": alerts,
        "config": {"ia_inactive_enabled": ia_enabled, "ia_inactive_seconds": ia_threshold},
        "runtime_error": snapshot.get("runtime_error") if isinstance(snapshot, dict) else None,
    }


def compute_alerts(force: bool = False) -> dict[str, Any]:
    """Alertas ativos derivados do snapshot de saude. Cacheado por poucos
    segundos porque o snapshot faz probe de gateway e pode ser pollado por
    varias telas (pagina de alertas + badge do menu)."""
    global _CACHE, _CACHE_AT
    now = time.monotonic()
    with _CACHE_LOCK:
        if not force and _CACHE is not None and (now - _CACHE_AT) < _CACHE_TTL:
            return _CACHE
    report = _compute()
    with _CACHE_LOCK:
        _CACHE = report
        _CACHE_AT = now
    return report
