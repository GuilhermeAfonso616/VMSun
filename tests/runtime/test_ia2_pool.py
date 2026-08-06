"""Etapa 3B — pool central da IA2: fila, prioridade, timeout, geração e falhas."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from app.core.config import settings
from app.runtime.ia2_pool import (
    IA2Pool,
    IA2PoolQueueFull,
    IA2PoolTimeout,
    IA2PoolUnavailable,
    STATE_DEGRADED,
    STATE_FAILED,
    STATE_READY,
)


class FakeResult:
    def __init__(self, person_score=0.9):
        self.enabled = True
        self.applied = True
        self.person_score = person_score
        self.not_person_score = 1.0 - person_score
        self.passed = person_score >= 0.5
        self.threshold = 0.5
        self.mode = "block"
        self.inference_ms = 7.0
        self.model_path = "models/ia2.pt"
        self.reason = "ok"
        self.block_eligible = False
        self.block_reason = None
        self.quality = {}
        self.device = "cpu"


class FakeRevalidator:
    """Revalidador de teste: nunca carrega modelo real."""

    def __init__(self, *, delay=0.0, raises=None, model=object(), load_error=None):
        self.delay = delay
        self.raises = raises
        self._model = model
        self._load_error = load_error
        self.model_path = "models/ia2.pt"
        self.device = "cpu"
        self.calls = 0
        self.seen_quality = []

    def _load_model(self):
        return self._model

    def infer_prepared_crop(self, crop, quality=None):
        self.calls += 1
        self.seen_quality.append(quality)
        if self.delay:
            time.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        return FakeResult()


def _crop(h=8, w=6):
    return np.zeros((h, w, 3), dtype=np.uint8)


@pytest.fixture
def pool_enabled(monkeypatch):
    monkeypatch.setattr(settings, "ia2_pool_enabled", True)
    monkeypatch.setattr(settings, "ia2_pool_worker_count", 1)
    monkeypatch.setattr(settings, "ia2_pool_max_queue_size", 4)
    monkeypatch.setattr(settings, "ia2_pool_timeout_ms", 1500)


def _started_pool(revalidator) -> IA2Pool:
    pool = IA2Pool(revalidator_provider=lambda: revalidator)
    pool.start()
    for _ in range(200):
        if pool.state in {STATE_READY, STATE_FAILED}:
            break
        time.sleep(0.01)
    return pool


# ------------------------------------------------------------------ ciclo de vida


def test_pool_desabilitada_nao_inicia(monkeypatch):
    monkeypatch.setattr(settings, "ia2_pool_enabled", False)
    pool = IA2Pool(revalidator_provider=lambda: FakeRevalidator())
    pool.start()
    assert pool.state == "disabled"
    assert not pool.ready()


def test_pool_carrega_modelo_e_fica_pronta(pool_enabled):
    pool = _started_pool(FakeRevalidator())
    try:
        assert pool.state == STATE_READY
        assert pool.ready()
        assert pool.health()["model_loaded"] is True
        assert pool.health()["generation"] == 1
    finally:
        pool.stop()


def test_falha_ao_carregar_modelo_marca_failed(pool_enabled):
    pool = _started_pool(FakeRevalidator(model=None, load_error="arquivo ausente"))
    try:
        assert pool.state == STATE_FAILED
        assert not pool.ready()
        with pytest.raises(IA2PoolUnavailable):
            pool.submit(_crop())
    finally:
        pool.stop()


def test_restart_muda_geracao(pool_enabled):
    pool = _started_pool(FakeRevalidator())
    try:
        primeira = pool.generation_id
        pool.restart()
        for _ in range(200):
            if pool.state == STATE_READY:
                break
            time.sleep(0.01)
        assert pool.generation_id == primeira + 1
        assert pool.stats.restarts_total == 1
    finally:
        pool.stop()


# ------------------------------------------------------------------ execução


def test_submit_executa_e_repassa_quality(pool_enabled):
    revalidator = FakeRevalidator()
    pool = _started_pool(revalidator)
    try:
        resultado = pool.submit(_crop(), quality={"blur": 0.3}, payload_bytes=144)
        assert resultado.person_score == 0.9
        assert revalidator.seen_quality == [{"blur": 0.3}]
        assert pool.stats.jobs_completed_total == 1
        assert pool.stats.payload_bytes_total == 144
    finally:
        pool.stop()


def test_uma_unica_instancia_atende_varios_chamadores(pool_enabled):
    revalidator = FakeRevalidator()
    pool = _started_pool(revalidator)
    resultados = []
    try:
        def chamar():
            resultados.append(pool.submit(_crop()))

        threads = [threading.Thread(target=chamar) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert len(resultados) == 5
        assert revalidator.calls == 5, "todas as inferencias usaram o mesmo revalidador"
    finally:
        pool.stop()


def test_fila_limitada_rejeita_sem_bloquear(monkeypatch):
    monkeypatch.setattr(settings, "ia2_pool_enabled", True)
    monkeypatch.setattr(settings, "ia2_pool_worker_count", 1)
    monkeypatch.setattr(settings, "ia2_pool_max_queue_size", 1)
    monkeypatch.setattr(settings, "ia2_pool_timeout_ms", 3000)
    pool = _started_pool(FakeRevalidator(delay=0.4))
    erros = []
    try:
        def chamar():
            try:
                pool.submit(_crop())
            except IA2PoolQueueFull as exc:
                erros.append(exc)

        threads = [threading.Thread(target=chamar) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=6)
        assert erros, "fila cheia precisa rejeitar em vez de crescer"
        assert pool.stats.jobs_dropped_total == len(erros)
        assert pool.queue_size() <= pool.queue_capacity()
    finally:
        pool.stop()


def test_timeout_nao_derruba_pool(monkeypatch):
    monkeypatch.setattr(settings, "ia2_pool_enabled", True)
    monkeypatch.setattr(settings, "ia2_pool_worker_count", 1)
    monkeypatch.setattr(settings, "ia2_pool_max_queue_size", 4)
    monkeypatch.setattr(settings, "ia2_pool_timeout_ms", 50)
    pool = _started_pool(FakeRevalidator(delay=0.5))
    try:
        with pytest.raises(IA2PoolTimeout):
            pool.submit(_crop())
        assert pool.stats.jobs_timed_out_total == 1
    finally:
        pool.stop()


def test_deadline_expirado_nao_ocupa_gpu(pool_enabled):
    revalidator = FakeRevalidator()
    pool = _started_pool(revalidator)
    try:
        with pytest.raises(IA2PoolTimeout):
            pool.submit(_crop(), deadline_monotonic_ns=time.monotonic_ns() - 1)
        assert revalidator.calls == 0, "job vencido nao pode executar o modelo"
        assert pool.stats.jobs_stale_total == 1
    finally:
        pool.stop()


def test_prioridade_menor_e_atendida_primeiro(monkeypatch):
    monkeypatch.setattr(settings, "ia2_pool_enabled", True)
    monkeypatch.setattr(settings, "ia2_pool_worker_count", 1)
    monkeypatch.setattr(settings, "ia2_pool_max_queue_size", 16)
    monkeypatch.setattr(settings, "ia2_pool_timeout_ms", 5000)

    ordem = []

    class Ordenado(FakeRevalidator):
        def infer_prepared_crop(self, crop, quality=None):
            ordem.append(int(quality.get("tag", -1)))
            time.sleep(0.05)
            return FakeResult()

    pool = _started_pool(Ordenado())
    try:
        bloqueio = threading.Thread(
            target=lambda: pool.submit(_crop(), quality={"tag": 0}, priority=10)
        )
        bloqueio.start()
        time.sleep(0.02)  # garante que o primeiro job ja saiu da fila
        baixa = threading.Thread(
            target=lambda: pool.submit(_crop(), quality={"tag": 30}, priority=30)
        )
        alta = threading.Thread(
            target=lambda: pool.submit(_crop(), quality={"tag": 5}, priority=5)
        )
        baixa.start()
        time.sleep(0.01)
        alta.start()
        for t in (bloqueio, baixa, alta):
            t.join(timeout=6)
        assert ordem[0] == 0
        assert ordem.index(5) < ordem.index(30), "prioridade alta deve furar a fila"
    finally:
        pool.stop()


def test_falha_de_inferencia_degrada_sem_matar_pool(pool_enabled):
    pool = _started_pool(FakeRevalidator(raises=RuntimeError("falhou")))
    try:
        with pytest.raises(RuntimeError):
            pool.submit(_crop())
        assert pool.state == STATE_DEGRADED
        assert pool.stats.jobs_failed_total == 1
        assert pool._threads, "as threads da pool continuam vivas"
    finally:
        pool.stop()


def test_oom_aplica_backoff_e_nao_entra_em_loop(pool_enabled):
    pool = _started_pool(FakeRevalidator(raises=RuntimeError("CUDA out of memory")))
    try:
        with pytest.raises(IA2PoolUnavailable):
            pool.submit(_crop())
        assert pool.state == STATE_DEGRADED
        assert pool.stats.last_error == "cuda_oom"
        # durante o backoff a pool recusa de imediato, sem tentar de novo
        with pytest.raises(IA2PoolUnavailable, match="backoff"):
            pool.submit(_crop())
    finally:
        pool.stop()


def test_health_e_metrics_expoem_estado(pool_enabled):
    pool = _started_pool(FakeRevalidator())
    try:
        health = pool.health()
        assert health["ready"] is True
        assert health["queue_capacity"] == 4
        assert health["workers"] == 1
        metrics = pool.metrics()
        assert metrics["ia2_pool_ready"] is True
        assert metrics["ia2_pool_generation"] == pool.generation_id
        for chave in metrics:
            assert "job_id" not in chave and "track_id" not in chave
    finally:
        pool.stop()
