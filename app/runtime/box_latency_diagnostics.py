"""Diagnostico agregado da latencia das boxes por camera."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from threading import Lock


LATENCY_FIELDS = (
    "gateway_to_worker_ms",
    "worker_to_inference_ms",
    "inference_queue_wait_ms",
    "inference_ms",
    "tracking_ms",
    "event_pipeline_ms",
    "ia2_ms",
    "ia3_ms",
    "ia1_to_track_publish_ms",
    "ia1_to_fast_publish_ms",
    "ia1_to_traditional_publish_ms",
    "track_store_write_ms",
    "track_store_read_ms",
    "backend_to_client_ms",
    "client_render_ms",
    "box_partial_age_ms",
    "box_total_age_ms",
)

COUNTER_FIELDS = (
    "visual_fast_path_published_total",
    "visual_fast_path_failed_total",
    "visual_fast_path_fallback_total",
    "visual_updates_stale_total",
    "visual_updates_out_of_order_total",
    "visual_updates_identity_rejected_total",
    "visual_updates_coalesced_total",
    "visual_empty_results_total",
    "visual_boxes_expired_total",
)


def camera_is_selected(camera_id: int, *, enabled: bool, camera_ids: str) -> bool:
    if not enabled:
        return False
    raw = str(camera_ids or "").strip()
    if raw == "*":
        return True
    selected: set[int] = set()
    for item in raw.split(","):
        try:
            value = int(item.strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            selected.add(value)
    return int(camera_id) in selected


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(
        0,
        min(len(ordered) - 1, math.ceil((percentile / 100.0) * len(ordered)) - 1),
    )
    return round(ordered[index], 3)


class BoxLatencyDiagnostics:
    """Mantem somente amostras numericas recentes, sem frames ou crops."""

    def __init__(self, *, max_samples: int = 300):
        self._max_samples = max(10, int(max_samples))
        self._samples: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._max_samples)
        )
        self._latest: dict = {}
        self._counters: dict[str, int] = {field: 0 for field in COUNTER_FIELDS}
        self._lock = Lock()

    def increment(self, field: str, amount: int = 1) -> None:
        if field not in self._counters:
            raise ValueError(f"contador de latencia desconhecido: {field}")
        with self._lock:
            self._counters[field] += max(0, int(amount))

    def record(self, sample: dict) -> None:
        cleaned = dict(sample)
        with self._lock:
            for field in LATENCY_FIELDS:
                value = cleaned.get(field)
                if value is None:
                    continue
                try:
                    numeric = max(0.0, float(value))
                except (TypeError, ValueError):
                    continue
                cleaned[field] = round(numeric, 3)
                self._samples[field].append(numeric)
            self._latest = cleaned

    def snapshot(self) -> dict:
        with self._lock:
            latest = dict(self._latest)
            summaries = {}
            for field in LATENCY_FIELDS:
                values = list(self._samples.get(field) or ())
                summaries[field] = {
                    "count": len(values),
                    "mean": (
                        round(sum(values) / len(values), 3) if values else None
                    ),
                    "p50": _percentile(values, 50.0),
                    "p90": _percentile(values, 90.0),
                    "p95": _percentile(values, 95.0),
                    "p99": _percentile(values, 99.0),
                    "max": round(max(values), 3) if values else None,
                }
            counters = dict(self._counters)
        return {
            "latest": latest,
            "summary": summaries,
            "counters": counters,
            "sample_window": self._max_samples,
        }
