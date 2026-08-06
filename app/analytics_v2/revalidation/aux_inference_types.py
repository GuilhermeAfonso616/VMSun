"""Tipos versionados de requisicao/resultado das inferencias auxiliares.

Etapa 3A. Estes tipos existem para que o worker deixe de depender do local de
execucao (IA2/IA3 locais ou pool central) sem alterar nenhuma decisao do
pipeline. O resultado nativo continua sendo transportado em `native`, de modo
que o restante do runtime segue lendo exatamente os mesmos campos de antes.

Nao transportar objetos Python arbitrarios entre processos: quando a execucao
central for ativada (3B/3C), apenas os campos primitivos abaixo cruzam o
transporte, e `native` e reconstruido no lado do worker.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


MODEL_IA2 = "ia2"
MODEL_IA3 = "ia3"
MODEL_SHADOW = "shadow"

CROP_FORMAT_BGR = "bgr24"
CROP_FORMAT_JPEG = "jpeg"

# Prioridades: menor valor e atendido primeiro.
PRIORITY_IA1 = 0
PRIORITY_IA2 = 10
PRIORITY_IA3 = 20
PRIORITY_SHADOW = 30
PRIORITY_OFFLINE = 40

ERROR_TIMEOUT = "timeout"
ERROR_POOL_UNAVAILABLE = "pool_unavailable"
ERROR_POOL_QUEUE_FULL = "pool_queue_full"
ERROR_STALE = "stale_job"
ERROR_IDENTITY_MISMATCH = "identity_mismatch"
ERROR_MODEL_UNAVAILABLE = "model_unavailable"
ERROR_INTERNAL = "internal_error"


def new_job_id() -> str:
    return uuid.uuid4().hex


def monotonic_ns() -> int:
    return time.monotonic_ns()


def deadline_from_ms(timeout_ms: float | int | None) -> int:
    """Converte um timeout relativo em deadline monotonico absoluto."""
    try:
        timeout = max(0.0, float(timeout_ms or 0.0))
    except Exception:
        timeout = 0.0
    return monotonic_ns() + int(timeout * 1_000_000)


@dataclass(slots=True)
class _JobIdentity:
    """Campos de identidade compartilhados por requisicoes e resultados."""

    job_id: str
    camera_id: int
    model_type: str
    frame_id: int | None = None
    generation_id: int | None = None
    track_id: str | int | None = None
    event_candidate_id: str | None = None


@dataclass(slots=True)
class AuxInferenceRequest(_JobIdentity):
    """Base das requisicoes auxiliares."""

    captured_at_monotonic_ns: int = field(default_factory=monotonic_ns)
    deadline_monotonic_ns: int = 0
    priority: int = PRIORITY_IA2
    crop: bytes | memoryview | None = None
    crop_format: str = CROP_FORMAT_BGR
    crop_width: int = 0
    crop_height: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def expired(self, *, now_ns: int | None = None) -> bool:
        if not self.deadline_monotonic_ns:
            return False
        return (now_ns if now_ns is not None else monotonic_ns()) > self.deadline_monotonic_ns

    def remaining_seconds(self, *, now_ns: int | None = None) -> float:
        if not self.deadline_monotonic_ns:
            return 0.0
        now = now_ns if now_ns is not None else monotonic_ns()
        return max(0.0, (self.deadline_monotonic_ns - now) / 1_000_000_000)

    def identity(self) -> tuple:
        return (
            self.job_id,
            int(self.camera_id),
            self.model_type,
            self.frame_id,
            self.generation_id,
            None if self.track_id is None else str(self.track_id),
        )


@dataclass(slots=True)
class AuxInferenceResult(_JobIdentity):
    """Base dos resultados auxiliares."""

    latency_ms: float = 0.0
    backend: str = "local"
    model_version: str | None = None
    pool_generation_id: int | None = None
    error_code: str | None = None
    fallback_used: bool = False
    native: Any = None

    @property
    def ok(self) -> bool:
        return self.error_code is None

    def matches(self, request: AuxInferenceRequest) -> bool:
        """Valida identidade para impedir resposta cruzada ou de outra geracao."""
        if self.job_id != request.job_id:
            return False
        if int(self.camera_id) != int(request.camera_id):
            return False
        if self.model_type != request.model_type:
            return False
        if request.frame_id is not None and self.frame_id is not None:
            if int(self.frame_id) != int(request.frame_id):
                return False
        if request.generation_id is not None and self.generation_id is not None:
            if int(self.generation_id) != int(request.generation_id):
                return False
        if request.track_id is not None and self.track_id is not None:
            if str(self.track_id) != str(request.track_id):
                return False
        return True


@dataclass(slots=True)
class IA2Request(AuxInferenceRequest):
    """Revalidacao de recorte de pessoa (IA2)."""

    def __post_init__(self) -> None:
        self.model_type = MODEL_IA2


@dataclass(slots=True)
class IA2Result(AuxInferenceResult):
    person_score: float | None = None
    not_person_score: float | None = None
    passed: bool | None = None
    threshold: float | None = None
    applied: bool = False
    enabled: bool = True
    mode: str = "audit"
    reason: str | None = None
    device: str | None = None

    @classmethod
    def from_native(cls, request: IA2Request, native: Any, *, backend: str = "local", latency_ms: float = 0.0) -> "IA2Result":
        return cls(
            job_id=request.job_id,
            camera_id=request.camera_id,
            model_type=MODEL_IA2,
            frame_id=request.frame_id,
            generation_id=request.generation_id,
            track_id=request.track_id,
            event_candidate_id=request.event_candidate_id,
            latency_ms=latency_ms or float(getattr(native, "inference_ms", 0.0) or 0.0),
            backend=backend,
            model_version=getattr(native, "model_path", None),
            person_score=getattr(native, "person_score", None),
            not_person_score=getattr(native, "not_person_score", None),
            passed=getattr(native, "passed", None),
            threshold=getattr(native, "threshold", None),
            applied=bool(getattr(native, "applied", False)),
            enabled=bool(getattr(native, "enabled", True)),
            mode=str(getattr(native, "mode", "audit")),
            reason=getattr(native, "reason", None),
            device=getattr(native, "device", None),
            native=native,
        )


@dataclass(slots=True)
class IA3Request(AuxInferenceRequest):
    """Revalidacao de pessoa distante (IA3).

    `base_quality` e `ia2_person_score` reproduzem os insumos que o gate atual
    usa para decidir se a IA3 deve rodar; sem eles a execucao central mudaria o
    comportamento do gate.
    """

    base_quality: dict[str, Any] | None = None
    ia2_person_score: float | None = None
    ia2_not_person_score: float | None = None
    ia2_applied: bool = False

    def __post_init__(self) -> None:
        self.model_type = MODEL_IA3
        if self.priority == PRIORITY_IA2:
            self.priority = PRIORITY_IA3


@dataclass(slots=True)
class IA3Result(AuxInferenceResult):
    person_far_score: float | None = None
    not_person_far_score: float | None = None
    passed: bool | None = None
    threshold: float | None = None
    applied: bool = False
    triggered: bool = False
    enabled: bool = True
    reason: str | None = None
    trigger_reason: str | None = None
    device: str | None = None

    @classmethod
    def from_native(cls, request: IA3Request, native: Any, *, backend: str = "local", latency_ms: float = 0.0) -> "IA3Result":
        return cls(
            job_id=request.job_id,
            camera_id=request.camera_id,
            model_type=MODEL_IA3,
            frame_id=request.frame_id,
            generation_id=request.generation_id,
            track_id=request.track_id,
            event_candidate_id=request.event_candidate_id,
            latency_ms=latency_ms or float(getattr(native, "inference_ms", 0.0) or 0.0),
            backend=backend,
            model_version=getattr(native, "model_path", None),
            person_far_score=getattr(native, "person_far_score", None),
            not_person_far_score=getattr(native, "not_person_far_score", None),
            passed=getattr(native, "passed", None),
            threshold=getattr(native, "threshold", None),
            applied=bool(getattr(native, "applied", False)),
            triggered=bool(getattr(native, "triggered", False)),
            enabled=bool(getattr(native, "enabled", True)),
            reason=getattr(native, "reason", None),
            trigger_reason=getattr(native, "trigger_reason", None),
            device=getattr(native, "device", None),
            native=native,
        )


__all__ = [
    "AuxInferenceRequest",
    "AuxInferenceResult",
    "CROP_FORMAT_BGR",
    "CROP_FORMAT_JPEG",
    "ERROR_IDENTITY_MISMATCH",
    "ERROR_INTERNAL",
    "ERROR_MODEL_UNAVAILABLE",
    "ERROR_POOL_QUEUE_FULL",
    "ERROR_POOL_UNAVAILABLE",
    "ERROR_STALE",
    "ERROR_TIMEOUT",
    "IA2Request",
    "IA2Result",
    "IA3Request",
    "IA3Result",
    "MODEL_IA2",
    "MODEL_IA3",
    "MODEL_SHADOW",
    "PRIORITY_IA1",
    "PRIORITY_IA2",
    "PRIORITY_IA3",
    "PRIORITY_OFFLINE",
    "PRIORITY_SHADOW",
    "deadline_from_ms",
    "monotonic_ns",
    "new_job_id",
]
