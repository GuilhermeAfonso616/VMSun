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


# Metricas numericas guardadas por amostra. Cada uma vira uma serie temporal no
# grafico de historico. As chaves batem com o payload que o health monitor grava.
METRIC_KEYS = (
    "cpu",
    "ram_mb",
    "gpu",
    "gpu_mem_mb",
    "fps",
    "raw_fps",
    "workers",
    "running",
    "host_cpu",
    "host_ram",
)


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


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


class ResourceHistoryStore:
    """Persiste o uso agregado de recursos (CPU/RAM/GPU/FPS dos workers) em
    arquivos JSONL diarios e serve consultas agrupadas por bucket de tempo.

    Espelha o padrao de ``operational_history_store`` (mesma pasta base, mesma
    politica de retencao e limite de buckets), mas guarda series numericas em
    vez de estados por camera. O agrupamento por bucket usa a media das amostras.
    """

    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir or (Path(settings.runtime_state_dir) / "resource_history")
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._last_record_wall = 0.0
        self._last_cleanup_day: str | None = None

    def _path_for_day(self, day: datetime) -> Path:
        return self._base_dir / f"resource_usage_{day.strftime('%Y%m%d')}.jsonl"

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

    def _interval_seconds(self) -> float:
        raw = getattr(settings, "resource_history_sample_interval_seconds", None)
        if raw is None:
            raw = getattr(settings, "operational_history_sample_interval_seconds", 60.0)
        return max(5.0, float(raw or 60.0))

    def record_snapshot(self, snapshot: dict[str, Any], *, force: bool = False) -> bool:
        interval = self._interval_seconds()
        now_wall = time.monotonic()
        if not force and now_wall - self._last_record_wall < interval:
            return False

        generated_at = _parse_datetime(snapshot.get("generated_at")) or _utc_now_naive()
        payload: dict[str, Any] = {"ts": _iso(generated_at)}
        for key in METRIC_KEYS:
            payload[key] = _num(snapshot.get(key))
        gpu_total = _num(snapshot.get("gpu_mem_total_mb"))
        if gpu_total is not None:
            payload["gpu_mem_total_mb"] = gpu_total

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
        retention_days = getattr(settings, "resource_history_retention_days", None)
        if retention_days is None:
            retention_days = getattr(settings, "operational_history_retention_days", 365)
        retention_days = int(retention_days)
        if retention_days <= 0:
            # 0 ou negativo = retencao ilimitada: nunca poda o historico.
            return
        cutoff = now_dt - timedelta(days=retention_days)
        try:
            for path in self._base_dir.glob("resource_usage_*.jsonl"):
                try:
                    stamp = path.stem.removeprefix("resource_usage_")
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
        start_iso: str | None = None,
        end_iso: str | None = None,
    ) -> dict[str, Any]:
        now_dt = _utc_now_naive()
        requested_bucket_minutes = max(1, min(60, int(bucket_minutes or 5)))

        # Intervalo absoluto (dia/horario) tem prioridade sobre a janela relativa.
        # start_iso/end_iso chegam em UTC (a UI converte o horario local). Limita o
        # span a 31 dias para nao varrer arquivos JSONL demais por consulta.
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

        max_buckets = getattr(settings, "resource_history_max_buckets", None)
        if max_buckets is None:
            max_buckets = getattr(settings, "operational_history_max_buckets", 720)
        max_buckets = max(24, int(max_buckets or 720))

        raw_bucket_seconds = requested_bucket_minutes * 60
        requested_buckets = max(1, int(math.ceil((now_dt - start).total_seconds() / raw_bucket_seconds)))
        bucket_seconds = raw_bucket_seconds
        if requested_buckets > max_buckets:
            bucket_seconds = int(math.ceil((now_dt - start).total_seconds() / max_buckets))
            bucket_seconds = int(math.ceil(bucket_seconds / 60.0) * 60)
        bucket_count = max(1, int(math.ceil((now_dt - start).total_seconds() / bucket_seconds)))
        effective_bucket_minutes = max(1, int(bucket_seconds / 60))

        # Acumuladores por bucket: soma e contagem para calcular a media.
        sums: dict[str, list[float]] = {key: [0.0] * bucket_count for key in METRIC_KEYS}
        counts: dict[str, list[int]] = {key: [0] * bucket_count for key in METRIC_KEYS}
        totals: dict[str, dict[str, float]] = {
            key: {"sum": 0.0, "count": 0.0, "peak": 0.0, "last": 0.0, "has_last": 0.0} for key in METRIC_KEYS
        }
        gpu_mem_total_seen: float | None = None
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
                mem_total = _num(sample.get("gpu_mem_total_mb"))
                if mem_total is not None:
                    gpu_mem_total_seen = mem_total
                for key in METRIC_KEYS:
                    value = _num(sample.get(key))
                    if value is None:
                        continue
                    sums[key][bucket_index] += value
                    counts[key][bucket_index] += 1
                    agg = totals[key]
                    agg["sum"] += value
                    agg["count"] += 1
                    agg["peak"] = max(agg["peak"], value)
                    agg["last"] = value
                    agg["has_last"] = 1.0

        buckets: list[dict[str, Any]] = []
        for index in range(bucket_count):
            entry: dict[str, Any] = {
                "index": index,
                "start": _iso(start + timedelta(seconds=index * bucket_seconds)),
                "end": _iso(start + timedelta(seconds=(index + 1) * bucket_seconds)),
                "sampled": False,
            }
            for key in METRIC_KEYS:
                count = counts[key][index]
                if count > 0:
                    entry[key] = round(sums[key][index] / count, 2)
                    entry["sampled"] = True
                else:
                    entry[key] = None
            buckets.append(entry)

        summary_metrics: dict[str, Any] = {}
        for key in METRIC_KEYS:
            agg = totals[key]
            count = agg["count"]
            summary_metrics[key] = {
                "avg": round(agg["sum"] / count, 2) if count else None,
                "peak": round(agg["peak"], 2) if count else None,
                "last": round(agg["last"], 2) if agg["has_last"] else None,
            }

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
                "gpu_mem_total_mb": gpu_mem_total_seen,
                "metrics": summary_metrics,
            },
            "buckets": buckets,
            "metric_keys": list(METRIC_KEYS),
            "legend": {
                "cpu": "CPU workers (%)",
                "ram_mb": "RAM workers (MB)",
                "gpu": "GPU util. (%)",
                "gpu_mem_mb": "GPU memoria (MB)",
                "fps": "FPS processado",
                "raw_fps": "FPS capturado",
                "workers": "Workers ativos",
                "running": "Cameras rodando",
                "host_cpu": "CPU host (%)",
                "host_ram": "RAM host (%)",
            },
        }


resource_history_store = ResourceHistoryStore()
