"""Pool central da IA2 — Etapa 3B.

Decisão de arquitetura (Opção A): a pool vive no processo principal do runtime,
como componente, seguindo o mesmo padrão já usado pela IA1
(`inference_pool.InferencePool` + `InferenceSocketServer`). Motivos:

- reaproveita a infraestrutura de socket da Etapa 2B, sem container novo;
- health check e ciclo de vida ficam junto do runtime, que já é supervisionado;
- os workers de câmera são processos filhos e continuam sem carregar o modelo.

Risco aceito e mitigado: uma falha da IA2 poderia afetar o runtime. Mitigação:
fila e threads próprias (sem head-of-line blocking com a IA1), todo job isolado
em try/except, estados explícitos de degradação e backoff em OOM.

A pool NÃO decide nada: recebe recorte já preprocessado pelo worker, executa o
modelo e devolve o resultado. Threshold, ACCEPT/REJECT/UNCERTAIN e política de
evento continuam no worker.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger


logger = get_logger("app.runtime.ia2_pool")

STATE_DISABLED = "disabled"
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_DEGRADED = "degraded"
STATE_FAILED = "failed"

_OOM_BACKOFF_SECONDS = 30.0


class IA2PoolQueueFull(RuntimeError):
    pass


class IA2PoolTimeout(RuntimeError):
    pass


class IA2PoolUnavailable(RuntimeError):
    pass


@dataclass(order=True)
class _QueuedJob:
    priority: int
    sequence: int
    submitted_at: float = field(compare=False, default=0.0)
    deadline_monotonic_ns: int = field(compare=False, default=0)
    payload: Any = field(compare=False, default=None)
    done: threading.Event = field(compare=False, default_factory=threading.Event)
    result: Any = field(compare=False, default=None)
    error: BaseException | None = field(compare=False, default=None)
    queue_wait_ms: float = field(compare=False, default=0.0)


@dataclass
class IA2PoolStats:
    jobs_submitted_total: int = 0
    jobs_completed_total: int = 0
    jobs_failed_total: int = 0
    jobs_timed_out_total: int = 0
    jobs_dropped_total: int = 0
    jobs_stale_total: int = 0
    payload_bytes_total: int = 0
    restarts_total: int = 0
    last_queue_wait_ms: float = 0.0
    last_inference_ms: float = 0.0
    model_load_seconds: float = 0.0
    last_error: str | None = None
    last_success_monotonic: float = 0.0


class IA2Pool:
    """Executor central do modelo da IA2."""

    def __init__(self, revalidator_provider: Any = None) -> None:
        self._provider = revalidator_provider
        self._lock = threading.Lock()
        self._queue: queue.PriorityQueue[_QueuedJob] | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._counter = itertools.count()
        self._state = STATE_DISABLED
        self._generation_id = 0
        self._revalidator: Any = None
        self._oom_until = 0.0
        self.stats = IA2PoolStats()

    # ------------------------------------------------------------- ciclo de vida

    @property
    def generation_id(self) -> int:
        return self._generation_id

    @property
    def state(self) -> str:
        return self._state

    def start(self) -> None:
        """Sobe a pool. Idempotente; nunca propaga excecao para o bootstrap."""
        if not bool(settings.ia2_pool_enabled):
            self._state = STATE_DISABLED
            return
        with self._lock:
            if self._threads:
                return
            self._stop.clear()
            self._generation_id += 1
            self._state = STATE_LOADING
            maxsize = max(1, int(settings.ia2_pool_max_queue_size))
            self._queue = queue.PriorityQueue(maxsize=maxsize)
            worker_count = max(1, int(settings.ia2_pool_worker_count))
            for index in range(worker_count):
                thread = threading.Thread(
                    target=self._run,
                    name=f"ia2-pool-{index}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)
        logger.info(
            "Pool IA2 iniciada generation=%s workers=%s queue_capacity=%s",
            self._generation_id,
            len(self._threads),
            self._queue.maxsize if self._queue else 0,
            extra={
                "action": "ia2_pool_started",
                "status": "running",
                "reason": "pool_start",
            },
        )
        threading.Thread(target=self._warmup, name="ia2-pool-warmup", daemon=True).start()

    def stop(self, *, timeout: float = 5.0) -> None:
        with self._lock:
            threads = list(self._threads)
            self._threads = []
        self._stop.set()
        for thread in threads:
            thread.join(timeout=timeout)
        self._state = STATE_DISABLED
        logger.info(
            "Pool IA2 parada generation=%s",
            self._generation_id,
            extra={
                "action": "ia2_pool_stopped",
                "status": "stopped",
                "reason": "pool_stop",
            },
        )

    def restart(self) -> None:
        """Reinicia a pool; a geracao muda e resultados antigos sao rejeitados."""
        self.stop()
        self.stats.restarts_total += 1
        self._revalidator = None
        self.start()

    def _resolve_revalidator(self) -> Any:
        if self._revalidator is not None:
            return self._revalidator
        if self._provider is not None:
            self._revalidator = self._provider()
        else:
            from app.analytics_v2.revalidation.person_crop_revalidator import (
                get_person_crop_revalidator,
            )

            self._revalidator = get_person_crop_revalidator()
        return self._revalidator

    def _warmup(self) -> None:
        """Carrega o modelo uma unica vez e marca a pool pronta."""
        started = time.perf_counter()
        try:
            revalidator = self._resolve_revalidator()
            model = revalidator._load_model()
            if model is None:
                self._state = STATE_FAILED
                self.stats.last_error = getattr(revalidator, "_load_error", None) or "model_unavailable"
                logger.error(
                    "Pool IA2 nao carregou o modelo generation=%s reason=%s",
                    self._generation_id,
                    self.stats.last_error,
                    extra={
                        "action": "ia2_pool_model_failed",
                        "status": "error",
                        "reason": self.stats.last_error,
                    },
                )
                return
            self.stats.model_load_seconds = time.perf_counter() - started
            self._state = STATE_READY
            logger.info(
                "Pool IA2 pronta generation=%s model=%s device=%s load_seconds=%.3f",
                self._generation_id,
                getattr(revalidator, "model_path", None),
                getattr(revalidator, "device", None),
                self.stats.model_load_seconds,
                extra={
                    "action": "ia2_pool_ready",
                    "status": "running",
                    "reason": "model_loaded",
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._state = STATE_FAILED
            self.stats.last_error = exc.__class__.__name__
            logger.exception(
                "Pool IA2 falhou no warm-up generation=%s",
                self._generation_id,
                extra={
                    "action": "ia2_pool_warmup_failed",
                    "status": "error",
                    "reason": exc.__class__.__name__,
                },
            )

    # ------------------------------------------------------------- submissao

    def ready(self) -> bool:
        return self._state == STATE_READY and bool(self._threads)

    def submit(
        self,
        crop: Any,
        *,
        quality: dict[str, Any] | None = None,
        priority: int = 10,
        deadline_monotonic_ns: int = 0,
        timeout_ms: int | None = None,
        payload_bytes: int = 0,
    ) -> Any:
        """Enfileira e aguarda o resultado. Sincrono, como o caminho local."""
        if not bool(settings.ia2_pool_enabled):
            raise IA2PoolUnavailable("ia2_pool_disabled")
        if self._state in {STATE_FAILED, STATE_DISABLED} or self._queue is None:
            raise IA2PoolUnavailable(f"ia2_pool_{self._state}")
        if self._oom_until and time.monotonic() < self._oom_until:
            raise IA2PoolUnavailable("ia2_pool_oom_backoff")

        self.stats.jobs_submitted_total += 1
        self.stats.payload_bytes_total += max(0, int(payload_bytes))
        job = _QueuedJob(
            priority=int(priority),
            sequence=next(self._counter),
            submitted_at=time.monotonic(),
            deadline_monotonic_ns=int(deadline_monotonic_ns or 0),
            payload=(crop, quality or {}),
        )
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self.stats.jobs_dropped_total += 1
            logger.warning(
                "Fila da IA2 cheia capacity=%s; job descartado",
                self._queue.maxsize,
                extra={
                    "action": "ia2_pool_queue_full",
                    "status": "degraded",
                    "reason": "queue_full",
                },
            )
            raise IA2PoolQueueFull("ia2_pool_queue_full") from None

        timeout_seconds = max(
            0.05,
            float(timeout_ms if timeout_ms is not None else settings.ia2_pool_timeout_ms) / 1000.0,
        )
        if not job.done.wait(timeout=timeout_seconds):
            self.stats.jobs_timed_out_total += 1
            logger.warning(
                "Timeout na pool IA2 timeout_ms=%s queue_size=%s",
                int(timeout_seconds * 1000),
                self.queue_size(),
                extra={
                    "action": "ia2_pool_timeout",
                    "status": "degraded",
                    "reason": "timeout",
                },
            )
            raise IA2PoolTimeout("ia2_pool_timeout")

        if job.error is not None:
            raise job.error
        self.stats.last_queue_wait_ms = job.queue_wait_ms
        return job.result

    def queue_size(self) -> int:
        return self._queue.qsize() if self._queue is not None else 0

    def queue_capacity(self) -> int:
        return self._queue.maxsize if self._queue is not None else 0

    # ------------------------------------------------------------- execucao

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._queue is None:
                time.sleep(0.05)
                continue
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._execute(job)
            finally:
                job.done.set()
                self._queue.task_done()

    def _execute(self, job: _QueuedJob) -> None:
        job.queue_wait_ms = (time.monotonic() - job.submitted_at) * 1000.0

        # Job que ja perdeu o deadline nao ocupa GPU: e descartado antes de rodar.
        if job.deadline_monotonic_ns and time.monotonic_ns() > job.deadline_monotonic_ns:
            self.stats.jobs_stale_total += 1
            job.error = IA2PoolTimeout("ia2_pool_deadline_expired")
            return

        crop, quality = job.payload
        try:
            revalidator = self._resolve_revalidator()
            result = revalidator.infer_prepared_crop(crop, quality)
            job.result = result
            self.stats.jobs_completed_total += 1
            self.stats.last_inference_ms = float(getattr(result, "inference_ms", 0.0) or 0.0)
            self.stats.last_success_monotonic = time.monotonic()
            if self._state == STATE_DEGRADED:
                self._state = STATE_READY
        except MemoryError as exc:
            self._handle_oom(exc)
            job.error = IA2PoolUnavailable("ia2_pool_oom")
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if "out of memory" in message or "cuda" in message and "memory" in message:
                self._handle_oom(exc)
                job.error = IA2PoolUnavailable("ia2_pool_oom")
                return
            self.stats.jobs_failed_total += 1
            self.stats.last_error = exc.__class__.__name__
            self._state = STATE_DEGRADED
            logger.exception(
                "Job da IA2 falhou na pool",
                extra={
                    "action": "ia2_pool_job_failed",
                    "status": "degraded",
                    "reason": exc.__class__.__name__,
                },
            )
            job.error = exc

    def _handle_oom(self, exc: BaseException) -> None:
        self.stats.jobs_failed_total += 1
        self.stats.last_error = "cuda_oom"
        self._state = STATE_DEGRADED
        self._oom_until = time.monotonic() + _OOM_BACKOFF_SECONDS
        logger.error(
            "Pool IA2 sem memoria; aplicando backoff de %.0fs",
            _OOM_BACKOFF_SECONDS,
            extra={
                "action": "ia2_pool_oom",
                "status": "degraded",
                "reason": "cuda_oom",
            },
        )
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    # ------------------------------------------------------------- health

    def health(self) -> dict[str, Any]:
        revalidator = self._revalidator
        last_success_age_ms = None
        if self.stats.last_success_monotonic:
            last_success_age_ms = round(
                (time.monotonic() - self.stats.last_success_monotonic) * 1000.0, 1
            )
        return {
            "enabled": bool(settings.ia2_pool_enabled),
            "ready": self.ready(),
            "state": self._state,
            "generation": self._generation_id,
            "backend": "ultralytics",
            "model_loaded": bool(getattr(revalidator, "_model", None) is not None),
            "model_path": getattr(revalidator, "model_path", None),
            "device": getattr(revalidator, "device", None),
            "queue_size": self.queue_size(),
            "queue_capacity": self.queue_capacity(),
            "workers": len(self._threads),
            "model_load_seconds": round(self.stats.model_load_seconds, 3),
            "last_success_age_ms": last_success_age_ms,
            "last_error": self.stats.last_error,
            "restarts_total": self.stats.restarts_total,
        }

    def metrics(self) -> dict[str, Any]:
        return {
            "ia2_pool_enabled": bool(settings.ia2_pool_enabled),
            "ia2_pool_ready": self.ready(),
            "ia2_pool_state": self._state,
            "ia2_pool_generation": self._generation_id,
            "ia2_pool_queue_size": self.queue_size(),
            "ia2_pool_queue_capacity": self.queue_capacity(),
            "ia2_jobs_submitted_total": self.stats.jobs_submitted_total,
            "ia2_jobs_completed_total": self.stats.jobs_completed_total,
            "ia2_jobs_failed_total": self.stats.jobs_failed_total,
            "ia2_jobs_timed_out_total": self.stats.jobs_timed_out_total,
            "ia2_jobs_dropped_total": self.stats.jobs_dropped_total,
            "ia2_jobs_stale_total": self.stats.jobs_stale_total,
            "ia2_payload_bytes_total": self.stats.payload_bytes_total,
            "ia2_queue_wait_ms": round(self.stats.last_queue_wait_ms, 3),
            "ia2_inference_latency_ms": round(self.stats.last_inference_ms, 3),
            "ia2_model_load_seconds": round(self.stats.model_load_seconds, 3),
            "ia2_pool_restarts_total": self.stats.restarts_total,
        }


_POOL: IA2Pool | None = None
_POOL_LOCK = threading.Lock()


def get_ia2_pool() -> IA2Pool:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = IA2Pool()
    return _POOL


__all__ = [
    "IA2Pool",
    "IA2PoolQueueFull",
    "IA2PoolStats",
    "IA2PoolTimeout",
    "IA2PoolUnavailable",
    "STATE_DEGRADED",
    "STATE_DISABLED",
    "STATE_FAILED",
    "STATE_LOADING",
    "STATE_READY",
    "get_ia2_pool",
]
