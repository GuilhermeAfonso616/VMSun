"""Clientes de inferencia auxiliar (IA2/IA3) — Etapa 3A.

Objetivo: o worker deixa de saber ONDE o modelo roda. Ele monta uma requisicao
tipada e recebe um resultado tipado; se a execucao e local ou central passa a
ser decisao da factory, nao do pipeline.

Nesta etapa apenas a execucao local esta implementada de fato. Os clientes
centrais existem com o contrato definido e reportam `pool_unavailable` ate que
as Etapas 3B/3C tragam as pools. Isso permite exercitar modos, canario,
fallback e politica segura antes de mover qualquer modelo.

Politica segura (obrigatoria): falha, timeout ou pool ausente NUNCA viram
REJECT. O resultado degradado e equivalente a "revalidador nao aplicado", que e
exatamente como o pipeline ja trata a ausencia de IA2/IA3 hoje.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable

from app.core.config import settings
from app.core.logging import get_logger

from .aux_inference_types import (
    ERROR_INTERNAL,
    ERROR_POOL_UNAVAILABLE,
    ERROR_STALE,
    IA2Request,
    IA2Result,
    IA3Request,
    IA3Result,
    MODEL_IA2,
    MODEL_IA3,
    MODEL_SHADOW,
)


logger = get_logger("app.analytics.aux_inference")

MODE_LOCAL = "local"
MODE_CENTRAL_PREFER = "central_prefer"
MODE_CENTRAL_STRICT = "central_strict"
VALID_MODES = {MODE_LOCAL, MODE_CENTRAL_PREFER, MODE_CENTRAL_STRICT}

_MODE_SETTING = {
    MODEL_IA2: "ia2_execution_mode",
    MODEL_IA3: "ia3_execution_mode",
    MODEL_SHADOW: "shadow_execution_mode",
}
_IDS_SETTING = {
    MODEL_IA2: "ia2_central_camera_ids",
    MODEL_IA3: "ia3_central_camera_ids",
    MODEL_SHADOW: "shadow_central_camera_ids",
}


class AuxInferenceUnavailable(RuntimeError):
    """A pool central nao esta disponivel para atender o job."""


# --------------------------------------------------------------------------- modos


def execution_mode(model_type: str) -> str:
    """Modo configurado para o modelo. Erro explicito se o valor for invalido."""
    key = _MODE_SETTING.get(model_type)
    if key is None:
        raise ValueError(f"modelo auxiliar desconhecido: {model_type}")
    mode = str(getattr(settings, key, MODE_LOCAL) or MODE_LOCAL).strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(
            f"{key.upper()} deve ser local, central_prefer ou central_strict"
        )
    return mode


def central_selected(model_type: str, camera_id: int | None) -> bool:
    """Seleção canário centralizada. Em modo local a lista e ignorada."""
    if execution_mode(model_type) == MODE_LOCAL:
        return False
    if camera_id is None:
        return False
    raw = str(getattr(settings, _IDS_SETTING[model_type], "") or "").strip()
    if raw == "*":
        return True
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            if int(item) == int(camera_id):
                return True
        except (TypeError, ValueError):
            continue
    return False


# --------------------------------------------------------------------------- métricas


class _AuxMetrics:
    """Contadores por modelo, sem labels de alta cardinalidade."""

    _FIELDS = (
        "jobs_submitted_total",
        "jobs_completed_total",
        "jobs_failed_total",
        "jobs_timed_out_total",
        "jobs_dropped_total",
        "jobs_stale_total",
        "fallback_local_total",
        "payload_bytes_total",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, float]] = {}

    def incr(self, model_type: str, field: str, value: float = 1.0) -> None:
        with self._lock:
            bucket = self._data.setdefault(model_type, {})
            bucket[field] = bucket.get(field, 0.0) + value

    def observe_latency(self, model_type: str, latency_ms: float) -> None:
        with self._lock:
            bucket = self._data.setdefault(model_type, {})
            bucket["last_latency_ms"] = round(float(latency_ms), 3)
            bucket["latency_ms_sum"] = bucket.get("latency_ms_sum", 0.0) + float(latency_ms)
            bucket["latency_samples"] = bucket.get("latency_samples", 0.0) + 1.0

    def snapshot(self, model_type: str) -> dict[str, float]:
        with self._lock:
            bucket = dict(self._data.get(model_type, {}))
        for field in self._FIELDS:
            bucket.setdefault(field, 0.0)
        samples = bucket.get("latency_samples", 0.0)
        if samples:
            bucket["latency_ms_avg"] = round(bucket.get("latency_ms_sum", 0.0) / samples, 3)
        return bucket


aux_metrics = _AuxMetrics()


class _RateLimitedLog:
    def __init__(self, interval_seconds: float = 5.0) -> None:
        self._interval = interval_seconds
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def should_log(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if now - self._last.get(key, 0.0) < self._interval:
                return False
            self._last[key] = now
        return True


_rate_limited = _RateLimitedLog()


# --------------------------------------------------------------------------- interfaces


class IA2InferenceClient(ABC):
    backend_name = "abstract"

    @abstractmethod
    def infer(self, request: IA2Request) -> IA2Result:
        ...


class IA3InferenceClient(ABC):
    backend_name = "abstract"

    @abstractmethod
    def infer(self, request: IA3Request) -> IA3Result:
        ...


# --------------------------------------------------------------------------- resultados degradados


def _unapplied_ia2_native(reason: str) -> Any:
    from .person_crop_revalidator import CropRevalidationResult

    return CropRevalidationResult(
        enabled=True,
        applied=False,
        mode=str(settings.person_revalidator_mode or "audit"),
        reason=reason,
    )


def _unapplied_ia3_native(reason: str) -> Any:
    from .far_person_revalidator import FarPersonRevalidationResult

    return FarPersonRevalidationResult(
        enabled=True,
        triggered=False,
        applied=False,
        reason=reason,
    )


def degraded_ia2_result(request: IA2Request, error_code: str, *, backend: str = "central") -> IA2Result:
    """Resultado seguro: equivale a 'IA2 nao aplicada', nunca a REJECT."""
    native = _unapplied_ia2_native(error_code)
    result = IA2Result.from_native(request, native, backend=backend)
    result.error_code = error_code
    result.applied = False
    return result


def degraded_ia3_result(request: IA3Request, error_code: str, *, backend: str = "central") -> IA3Result:
    native = _unapplied_ia3_native(error_code)
    result = IA3Result.from_native(request, native, backend=backend)
    result.error_code = error_code
    result.applied = False
    result.triggered = False
    return result


# --------------------------------------------------------------------------- clientes locais


class LocalIA2InferenceClient(IA2InferenceClient):
    """Executa a IA2 no proprio processo do worker, como hoje."""

    backend_name = "local"

    def __init__(self, revalidator_provider: Callable[[], Any] | None = None) -> None:
        self._provider = revalidator_provider

    def _revalidator(self) -> Any:
        if self._provider is not None:
            return self._provider()
        from .person_crop_revalidator import get_person_crop_revalidator

        return get_person_crop_revalidator()

    def infer(self, request: IA2Request) -> IA2Result:
        aux_metrics.incr(MODEL_IA2, "jobs_submitted_total")
        started = time.perf_counter()
        try:
            native = self._revalidator().validate(
                request.metadata.get("frame"),
                request.metadata.get("bbox"),
            )
        except Exception as exc:
            aux_metrics.incr(MODEL_IA2, "jobs_failed_total")
            if _rate_limited.should_log("ia2_local_failed"):
                logger.exception(
                    "IA2 local falhou camera_id=%s",
                    request.camera_id,
                    extra={
                        "camera_id": request.camera_id,
                        "action": "aux_inference_local_failed",
                        "status": "degraded",
                        "reason": exc.__class__.__name__,
                    },
                )
            return degraded_ia2_result(request, ERROR_INTERNAL, backend="local")
        latency_ms = (time.perf_counter() - started) * 1000.0
        aux_metrics.incr(MODEL_IA2, "jobs_completed_total")
        aux_metrics.observe_latency(MODEL_IA2, latency_ms)
        return IA2Result.from_native(request, native, backend="local", latency_ms=latency_ms)


class LocalIA3InferenceClient(IA3InferenceClient):
    """Executa a IA3 no proprio processo do worker, como hoje."""

    backend_name = "local"

    def __init__(self, revalidator_provider: Callable[[], Any] | None = None) -> None:
        self._provider = revalidator_provider

    def _revalidator(self) -> Any:
        if self._provider is not None:
            return self._provider()
        from .far_person_revalidator import get_far_person_revalidator

        return get_far_person_revalidator()

    def infer(self, request: IA3Request) -> IA3Result:
        aux_metrics.incr(MODEL_IA3, "jobs_submitted_total")
        started = time.perf_counter()
        try:
            native = self._revalidator().validate(
                request.metadata.get("frame"),
                request.metadata.get("bbox"),
                base_quality=request.base_quality,
                ia2_result=request.metadata.get("ia2_result"),
            )
        except Exception as exc:
            aux_metrics.incr(MODEL_IA3, "jobs_failed_total")
            if _rate_limited.should_log("ia3_local_failed"):
                logger.exception(
                    "IA3 local falhou camera_id=%s",
                    request.camera_id,
                    extra={
                        "camera_id": request.camera_id,
                        "action": "aux_inference_local_failed",
                        "status": "degraded",
                        "reason": exc.__class__.__name__,
                    },
                )
            return degraded_ia3_result(request, ERROR_INTERNAL, backend="local")
        latency_ms = (time.perf_counter() - started) * 1000.0
        aux_metrics.incr(MODEL_IA3, "jobs_completed_total")
        aux_metrics.observe_latency(MODEL_IA3, latency_ms)
        return IA3Result.from_native(request, native, backend="local", latency_ms=latency_ms)


# --------------------------------------------------------------------------- clientes centrais


class CentralIA2InferenceClient(IA2InferenceClient):
    """Cliente da pool central de IA2 (Etapa 3B).

    O recorte é preparado aqui com a MESMA função usada pelo caminho local
    (`crop_with_quality`), e só então enviado à pool. Assim o preprocessamento
    não existe em dois lugares e não pode divergir.
    """

    backend_name = "central"

    def __init__(self, transport: Any | None = None, *, revalidator_provider: Callable[[], Any] | None = None) -> None:
        self._transport = transport
        self._provider = revalidator_provider

    def _revalidator(self) -> Any:
        if self._provider is not None:
            return self._provider()
        from .person_crop_revalidator import get_person_crop_revalidator

        return get_person_crop_revalidator()

    def _transport_or_default(self) -> Any:
        if self._transport is not None:
            return self._transport
        from app.runtime.ia2_transport import IA2BinaryTransport, ia2_transport_mode

        if ia2_transport_mode() == "http":
            raise AuxInferenceUnavailable("ia2_transport_http_only")
        self._transport = IA2BinaryTransport()
        return self._transport

    def infer(self, request: IA2Request) -> IA2Result:
        if not bool(settings.ia2_pool_enabled):
            raise AuxInferenceUnavailable("ia2_pool_disabled")

        frame = request.metadata.get("frame")
        bbox = request.metadata.get("bbox")
        revalidator = self._revalidator()

        # Casos que o caminho local resolve sem tocar no modelo continuam
        # resolvidos localmente: nao faz sentido ocupar a pool com eles, e o
        # resultado precisa ser identico ao de hoje.
        if not getattr(revalidator, "enabled", True):
            return IA2Result.from_native(request, revalidator._validate_direct(frame, bbox), backend="local")
        if frame is None or not hasattr(frame, "shape"):
            return IA2Result.from_native(request, revalidator._validate_direct(frame, bbox), backend="local")

        crop, quality = revalidator.crop_with_quality(frame, bbox)
        if crop is None:
            return IA2Result.from_native(request, revalidator._validate_direct(frame, bbox), backend="local")

        aux_metrics.incr(MODEL_IA2, "jobs_submitted_total")
        started = time.perf_counter()
        payload = self._transport_or_default().submit(request, crop, quality)
        latency_ms = (time.perf_counter() - started) * 1000.0

        if int(payload.get("camera_id", -1)) != int(request.camera_id):
            aux_metrics.incr(MODEL_IA2, "jobs_identity_rejected_total")
            raise AuxInferenceUnavailable(ERROR_STALE)
        for field_name in ("frame_id", "generation_id", "track_id"):
            expected = getattr(request, field_name)
            received = payload.get(field_name)
            if expected is not None and received is not None and str(expected) != str(received):
                aux_metrics.incr(MODEL_IA2, "jobs_identity_rejected_total")
                raise AuxInferenceUnavailable(ERROR_STALE)

        native = self._native_from_payload(payload)
        result = IA2Result.from_native(request, native, backend="central", latency_ms=latency_ms)
        result.pool_generation_id = payload.get("pool_generation_id")
        aux_metrics.incr(MODEL_IA2, "jobs_completed_total")
        aux_metrics.observe_latency(MODEL_IA2, latency_ms)
        return result

    @staticmethod
    def _native_from_payload(payload: dict[str, Any]) -> Any:
        from .person_crop_revalidator import CropRevalidationResult

        return CropRevalidationResult(
            enabled=bool(payload.get("enabled", True)),
            applied=bool(payload.get("applied", False)),
            person_score=payload.get("person_score"),
            not_person_score=payload.get("not_person_score"),
            passed=payload.get("passed"),
            threshold=payload.get("threshold"),
            mode=str(payload.get("mode") or "audit"),
            inference_ms=float(payload.get("inference_ms") or 0.0),
            model_path=payload.get("model_path"),
            reason=payload.get("reason"),
            block_eligible=bool(payload.get("block_eligible", False)),
            block_reason=payload.get("block_reason"),
            quality=payload.get("quality") or {},
            device=payload.get("device"),
        )


class CentralIA3InferenceClient(IA3InferenceClient):
    """Contrato da pool central de IA3. A pool chega na Etapa 3C."""

    backend_name = "central"

    def __init__(self, transport: Any | None = None) -> None:
        self._transport = transport

    def infer(self, request: IA3Request) -> IA3Result:
        if self._transport is None or not bool(settings.ia3_pool_enabled):
            raise AuxInferenceUnavailable("ia3_pool_disabled")
        result = self._transport.submit(request)
        if not isinstance(result, IA3Result) or not result.matches(request):
            aux_metrics.incr(MODEL_IA3, "jobs_stale_total")
            raise AuxInferenceUnavailable(ERROR_STALE)
        return result


# --------------------------------------------------------------------------- fallback


class _FallbackMixin:
    model_type = MODEL_IA2

    def _handle_unavailable(self, request, exc: Exception, local_call, degrade):
        strict = self._strict  # type: ignore[attr-defined]
        reason = str(exc) or ERROR_POOL_UNAVAILABLE
        if strict:
            aux_metrics.incr(self.model_type, "jobs_failed_total")
            if _rate_limited.should_log(f"{self.model_type}_strict_blocked"):
                logger.warning(
                    "Pool central obrigatoria indisponivel; fallback local bloqueado "
                    "model=%s camera_id=%s reason=%s",
                    self.model_type,
                    request.camera_id,
                    reason,
                    extra={
                        "camera_id": request.camera_id,
                        "action": "aux_inference_strict_blocked",
                        "status": "degraded",
                        "reason": reason,
                    },
                )
            return degrade(request, ERROR_POOL_UNAVAILABLE)

        aux_metrics.incr(self.model_type, "fallback_local_total")
        if _rate_limited.should_log(f"{self.model_type}_fallback"):
            logger.warning(
                "Pool central indisponivel; fallback local explicito model=%s "
                "camera_id=%s reason=%s",
                self.model_type,
                request.camera_id,
                reason,
                extra={
                    "camera_id": request.camera_id,
                    "action": "aux_inference_fallback_local",
                    "status": "degraded",
                    "reason": reason,
                },
            )
        result = local_call(request)
        result.fallback_used = True
        return result


class FallbackIA2InferenceClient(_FallbackMixin, IA2InferenceClient):
    """Tenta a pool central; em `central_prefer` cai para local com log e metrica."""

    backend_name = "central_fallback"
    model_type = MODEL_IA2

    def __init__(self, central: IA2InferenceClient, local: IA2InferenceClient, *, strict: bool) -> None:
        self._central = central
        self._local = local
        self._strict = bool(strict)

    def infer(self, request: IA2Request) -> IA2Result:
        try:
            return self._central.infer(request)
        except AuxInferenceUnavailable as exc:
            return self._handle_unavailable(request, exc, self._local.infer, degraded_ia2_result)
        except Exception as exc:  # noqa: BLE001 - falha inesperada nao pode virar REJECT
            return self._handle_unavailable(request, exc, self._local.infer, degraded_ia2_result)


class FallbackIA3InferenceClient(_FallbackMixin, IA3InferenceClient):
    backend_name = "central_fallback"
    model_type = MODEL_IA3

    def __init__(self, central: IA3InferenceClient, local: IA3InferenceClient, *, strict: bool) -> None:
        self._central = central
        self._local = local
        self._strict = bool(strict)

    def infer(self, request: IA3Request) -> IA3Result:
        try:
            return self._central.infer(request)
        except AuxInferenceUnavailable as exc:
            return self._handle_unavailable(request, exc, self._local.infer, degraded_ia3_result)
        except Exception as exc:  # noqa: BLE001
            return self._handle_unavailable(request, exc, self._local.infer, degraded_ia3_result)


# --------------------------------------------------------------------------- factory


def build_ia2_client(
    camera_id: int | None,
    *,
    local_provider: Callable[[], Any] | None = None,
    transport: Any | None = None,
) -> IA2InferenceClient:
    local = LocalIA2InferenceClient(local_provider)
    if not central_selected(MODEL_IA2, camera_id):
        return local
    return FallbackIA2InferenceClient(
        CentralIA2InferenceClient(transport, revalidator_provider=local_provider),
        local,
        strict=execution_mode(MODEL_IA2) == MODE_CENTRAL_STRICT,
    )


def build_ia3_client(
    camera_id: int | None,
    *,
    local_provider: Callable[[], Any] | None = None,
    transport: Any | None = None,
) -> IA3InferenceClient:
    local = LocalIA3InferenceClient(local_provider)
    if not central_selected(MODEL_IA3, camera_id):
        return local
    return FallbackIA3InferenceClient(
        CentralIA3InferenceClient(transport),
        local,
        strict=execution_mode(MODEL_IA3) == MODE_CENTRAL_STRICT,
    )


def camera_execution_state(camera_id: int | None) -> dict[str, Any]:
    """Estado por camera para o payload operacional. Nao expoe crops."""
    ia2_central = central_selected(MODEL_IA2, camera_id)
    ia3_central = central_selected(MODEL_IA3, camera_id)
    ia2_stats = aux_metrics.snapshot(MODEL_IA2)
    ia3_stats = aux_metrics.snapshot(MODEL_IA3)
    return {
        "ia2_execution_mode": "central" if ia2_central else "local",
        "ia2_configured_mode": execution_mode(MODEL_IA2),
        "ia2_pool_ready": bool(settings.ia2_pool_enabled) and ia2_central,
        "ia2_fallback_active": bool(ia2_stats.get("fallback_local_total", 0.0)) and ia2_central,
        "ia2_last_latency_ms": ia2_stats.get("last_latency_ms", 0.0),
        "ia2_last_queue_wait_ms": ia2_stats.get("last_queue_wait_ms", 0.0),
        "ia2_fallback_total": int(ia2_stats.get("fallback_local_total", 0.0)),
        "ia2_identity_rejected_total": int(ia2_stats.get("jobs_identity_rejected_total", 0.0)),
        "ia2_last_error": ia2_stats.get("last_error"),
        "ia2_timeouts_total": int(ia2_stats.get("jobs_timed_out_total", 0.0)),
        "ia3_execution_mode": "central" if ia3_central else "local",
        "ia3_configured_mode": execution_mode(MODEL_IA3),
        "ia3_pool_ready": bool(settings.ia3_pool_enabled) and ia3_central,
        "ia3_fallback_active": bool(ia3_stats.get("fallback_local_total", 0.0)) and ia3_central,
        "ia3_last_latency_ms": ia3_stats.get("last_latency_ms", 0.0),
        "ia3_timeouts_total": int(ia3_stats.get("jobs_timed_out_total", 0.0)),
    }


__all__ = [
    "AuxInferenceUnavailable",
    "CentralIA2InferenceClient",
    "CentralIA3InferenceClient",
    "FallbackIA2InferenceClient",
    "FallbackIA3InferenceClient",
    "IA2InferenceClient",
    "IA3InferenceClient",
    "LocalIA2InferenceClient",
    "LocalIA3InferenceClient",
    "MODE_CENTRAL_PREFER",
    "MODE_CENTRAL_STRICT",
    "MODE_LOCAL",
    "aux_metrics",
    "build_ia2_client",
    "build_ia3_client",
    "camera_execution_state",
    "central_selected",
    "degraded_ia2_result",
    "degraded_ia3_result",
    "execution_mode",
]
