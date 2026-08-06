from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.config import settings
from app.core.timezone import utc_now_naive as _shared_utc_now_naive


RUNNING_STATUS = {"running", "running_motion_test"}
ONLINE_STATUS = {"running", "running_motion_test", "warming_up", "starting", "degraded", "reconnecting"}


def _utc_now_naive() -> datetime:
    return _shared_utc_now_naive()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _state_code(item: dict[str, Any]) -> str:
    status = str(item.get("health_status") or item.get("camera_status") or "idle").strip().lower()
    worker_mode = str(item.get("worker_mode") or "").strip().lower()
    metrics_age = item.get("metrics_age_seconds")
    last_inference = item.get("last_successful_inference_at")

    try:
        metrics_fresh = metrics_age is not None and float(metrics_age) <= 90.0
    except Exception:
        metrics_fresh = False

    ia_active = (
        status in RUNNING_STATUS
        and worker_mode not in {"", "stopped", "idle"}
        and (bool(last_inference) or metrics_fresh)
    )
    if ia_active:
        return "ia"
    if status in RUNNING_STATUS:
        return "online"
    if status in {"starting", "warming_up"}:
        return "warming"
    if status == "degraded":
        return "degraded"
    if status == "reconnecting":
        return "reconnecting"
    if status == "offline":
        return "offline"
    if status == "stopped":
        return "stopped"
    return "unknown"


def _compact_camera(item: dict[str, Any]) -> dict[str, Any]:
    camera_id = item.get("camera_id")
    try:
        camera_id = int(camera_id)
    except Exception:
        camera_id = 0
    return {
        "id": camera_id,
        "name": str(item.get("camera_name") or item.get("name") or f"Camera {camera_id}"),
        "health_status": str(item.get("health_status") or item.get("camera_status") or "idle"),
        "camera_status": str(item.get("camera_status") or item.get("health_status") or "idle"),
        "worker_mode": str(item.get("worker_mode") or "stopped"),
        "is_running": bool(item.get("is_running", False)),
        "worker_pid": item.get("worker_pid"),
        "last_frame_at": item.get("last_frame_at"),
        "last_processed_frame_at": item.get("last_processed_frame_at"),
        "last_successful_inference_at": item.get("last_successful_inference_at"),
        "last_metrics_at": item.get("last_metrics_at"),
        "metrics_age_seconds": item.get("metrics_age_seconds"),
        "gateway_state": item.get("gateway_state"),
        "state": _state_code(item),
    }


class OperationalHistoryStore:
    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir or (Path(settings.runtime_state_dir) / "operational_history")
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._last_record_wall = 0.0
        self._last_cleanup_day: str | None = None

    def _path_for_day(self, day: datetime) -> Path:
        return self._base_dir / f"camera_status_{day.strftime('%Y%m%d')}.jsonl"

    def _iter_paths(self, start: datetime, end: datetime) -> list[Path]:
        paths: list[Path] = []
        day = datetime(start.year, start.month, start.day)
        end_day = datetime(end.year, end.month, end.day)
        while day <= end_day:
            path = self._path_for_day(day)
            if path.exists():
                paths.append(path)
            day += timedelta(days=1)
        return paths

    def record_snapshot(self, snapshot: dict[str, Any], *, force: bool = False) -> bool:
        interval = max(5.0, float(getattr(settings, "operational_history_sample_interval_seconds", 60.0) or 60.0))
        now_wall = time.monotonic()
        if not force and now_wall - self._last_record_wall < interval:
            return False

        generated_at = _parse_datetime(snapshot.get("generated_at")) or _utc_now_naive()
        cameras = [_compact_camera(item) for item in snapshot.get("cameras", []) if isinstance(item, dict)]
        payload = {
            "ts": _iso(generated_at),
            "camera_count": int(snapshot.get("camera_count", len(cameras)) or len(cameras)),
            "running_count": int(snapshot.get("running_count", 0) or 0),
            "degraded_count": int(snapshot.get("degraded_count", 0) or 0),
            "warming_up_count": int(snapshot.get("warming_up_count", 0) or 0),
            "reconnecting_count": int(snapshot.get("reconnecting_count", 0) or 0),
            "offline_count": int(snapshot.get("offline_count", 0) or 0),
            "stopped_count": int(snapshot.get("stopped_count", 0) or 0),
            "cameras": cameras,
        }

        line = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            with self._path_for_day(generated_at).open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._last_record_wall = now_wall
            self._cleanup_old_files(generated_at)
        return True

    def _cleanup_old_files(self, now_dt: datetime) -> None:
        today_key = now_dt.strftime("%Y%m%d")
        if self._last_cleanup_day == today_key:
            return
        self._last_cleanup_day = today_key
        retention_days = int(getattr(settings, "operational_history_retention_days", 365))
        if retention_days <= 0:
            # 0 ou negativo = retencao ilimitada: nunca poda o historico.
            return
        cutoff = now_dt - timedelta(days=retention_days)
        try:
            for path in self._base_dir.glob("camera_status_*.jsonl"):
                try:
                    stamp = path.stem.removeprefix("camera_status_")
                    day = datetime.strptime(stamp, "%Y%m%d")
                except Exception:
                    continue
                if day < datetime(cutoff.year, cutoff.month, cutoff.day):
                    path.unlink(missing_ok=True)
        except Exception:
            pass

    def query(
        self,
        *,
        hours: int = 24,
        bucket_minutes: int = 5,
        camera_id: int | None = None,
        start_iso: str | None = None,
        end_iso: str | None = None,
    ) -> dict[str, Any]:
        now_dt = _utc_now_naive()
        requested_bucket_minutes = max(1, min(60, int(bucket_minutes or 5)))

        # Intervalo absoluto (dia/horario) tem prioridade sobre a janela relativa.
        # start_iso/end_iso chegam em UTC (a UI converte o horario local). Limita o
        # span a 31 dias para nao varrer um ano de arquivos JSONL por consulta.
        range_start = _parse_datetime(start_iso)
        range_end = _parse_datetime(end_iso)
        if range_start is not None and range_end is not None and range_end > range_start:
            max_span = timedelta(days=31)
            if range_end - range_start > max_span:
                range_start = range_end - max_span
            start = range_start
            now_dt = range_end
        else:
            hours = max(1, min(168, int(hours or 24)))
            start = now_dt - timedelta(hours=hours)

        max_buckets = max(24, int(getattr(settings, "operational_history_max_buckets", 720) or 720))
        raw_bucket_seconds = requested_bucket_minutes * 60
        requested_buckets = max(1, int(math.ceil((now_dt - start).total_seconds() / raw_bucket_seconds)))
        bucket_seconds = raw_bucket_seconds
        if requested_buckets > max_buckets:
            bucket_seconds = int(math.ceil((now_dt - start).total_seconds() / max_buckets))
            bucket_seconds = int(math.ceil(bucket_seconds / 60.0) * 60)
        bucket_count = max(1, int(math.ceil((now_dt - start).total_seconds() / bucket_seconds)))
        effective_bucket_minutes = max(1, int(bucket_seconds / 60))

        buckets = [
            {
                "index": index,
                "start": _iso(start + timedelta(seconds=index * bucket_seconds)),
                "end": _iso(start + timedelta(seconds=(index + 1) * bucket_seconds)),
                "online": 0,
                "ia": 0,
                "degraded": 0,
                "offline": 0,
                "stopped": 0,
                "sampled": False,
            }
            for index in range(bucket_count)
        ]
        cameras: dict[int, dict[str, Any]] = {}
        total_samples = 0
        first_sample_at: str | None = None
        last_sample_at: str | None = None

        for path in self._iter_paths(start, now_dt):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    sample = json.loads(line)
                except Exception:
                    continue
                ts = _parse_datetime(sample.get("ts"))
                if ts is None or ts < start or ts > now_dt:
                    continue
                bucket_index = int((ts - start).total_seconds() // bucket_seconds)
                if bucket_index < 0 or bucket_index >= bucket_count:
                    continue
                total_samples += 1
                first_sample_at = first_sample_at or _iso(ts)
                last_sample_at = _iso(ts)
                bucket = buckets[bucket_index]
                bucket["sampled"] = True

                for item in sample.get("cameras", []):
                    if not isinstance(item, dict):
                        continue
                    try:
                        item_id = int(item.get("id") or item.get("camera_id") or 0)
                    except Exception:
                        item_id = 0
                    if not item_id:
                        continue
                    if camera_id is not None and int(camera_id) != item_id:
                        continue
                    state = str(item.get("state") or _state_code(item))
                    camera = cameras.get(item_id)
                    if camera is None:
                        camera = {
                            "id": item_id,
                            "name": str(item.get("name") or item.get("camera_name") or f"Camera {item_id}"),
                            "points": ["unknown"] * bucket_count,
                            "minutes": {
                                "ia": 0,
                                "online": 0,
                                "warming": 0,
                                "degraded": 0,
                                "reconnecting": 0,
                                "offline": 0,
                                "stopped": 0,
                                "unknown": 0,
                            },
                        }
                        cameras[item_id] = camera
                    camera["name"] = str(item.get("name") or camera["name"])
                    previous = camera["points"][bucket_index]
                    if previous != state:
                        if previous in camera["minutes"]:
                            camera["minutes"][previous] = max(0, int(camera["minutes"][previous]) - effective_bucket_minutes)
                        camera["points"][bucket_index] = state
                        if state in camera["minutes"]:
                            camera["minutes"][state] = int(camera["minutes"][state]) + effective_bucket_minutes

        camera_rows = sorted(cameras.values(), key=lambda item: str(item["name"]).lower())
        for index, bucket in enumerate(buckets):
            states = [str(camera["points"][index]) for camera in camera_rows]
            bucket["online"] = sum(1 for state in states if state in {"ia", "online", "warming", "degraded", "reconnecting"})
            bucket["ia"] = sum(1 for state in states if state == "ia")
            bucket["degraded"] = sum(1 for state in states if state == "degraded")
            bucket["offline"] = sum(1 for state in states if state == "offline")
            bucket["stopped"] = sum(1 for state in states if state == "stopped")
        return {
            "generated_at": _iso(now_dt),
            "range": {
                "hours": hours,
                "start": _iso(start),
                "end": _iso(now_dt),
                "requested_bucket_minutes": requested_bucket_minutes,
                "bucket_minutes": effective_bucket_minutes,
                "bucket_count": bucket_count,
            },
            "summary": {
                "samples": total_samples,
                "first_sample_at": first_sample_at,
                "last_sample_at": last_sample_at,
                "cameras": len(camera_rows),
            },
            "buckets": buckets,
            "cameras": camera_rows,
            "legend": {
                "ia": "IA ativa",
                "online": "Online sem IA confirmada",
                "warming": "Inicializando",
                "degraded": "Degradada",
                "reconnecting": "Reconectando",
                "offline": "Offline",
                "stopped": "Parada",
                "unknown": "Sem amostra",
            },
        }


operational_history_store = OperationalHistoryStore()
