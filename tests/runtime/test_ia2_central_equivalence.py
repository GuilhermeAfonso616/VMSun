"""Etapa 3B — equivalência entre IA2 local e IA2 central, e política segura.

O teste central aqui é: para o MESMO recorte, o caminho local e o caminho
central precisam produzir o mesmo resultado. Como a pool executa
`infer_prepared_crop` sobre o recorte preparado por `crop_with_quality` — a
mesma função usada pelo caminho local — a equivalência é estrutural, não
coincidência.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from app.analytics_v2.revalidation.aux_inference_client import (
    CentralIA2InferenceClient,
    FallbackIA2InferenceClient,
    LocalIA2InferenceClient,
    aux_metrics,
    build_ia2_client,
)
from app.analytics_v2.revalidation.aux_inference_types import (
    ERROR_POOL_UNAVAILABLE,
    IA2Request,
    MODEL_IA2,
    new_job_id,
)
from app.analytics_v2.revalidation.person_crop_revalidator import CropRevalidationResult
from app.core.config import settings
from app.runtime.ia2_pool import IA2Pool, STATE_FAILED, STATE_READY
from app.runtime.ia2_transport import IA2SocketServer

TOLERANCIA = 1e-6


class ScriptedRevalidator:
    """IA2 falsa determinística: mesma entrada, mesma saída."""

    def __init__(self, *, enabled=True, crop_valido=True):
        self.enabled = enabled
        self._crop_valido = crop_valido
        self._model = object()
        self._load_error = None
        self.model_path = "models/revalidator/v5.pt"
        self.device = "cpu"
        self.threshold = 0.5
        self.inferencias = 0

    def current_mode(self):
        return "block"

    def crop_with_quality(self, frame, bbox):
        if not self._crop_valido or frame is None:
            return None, {"quality_reason": "invalid_bbox"}
        return np.full((6, 4, 3), 7, dtype=np.uint8), {"blur": 0.25, "quality_gate_passed": True}

    def _load_model(self):
        return self._model

    def infer_prepared_crop(self, crop, quality=None):
        self.inferencias += 1
        # score derivado do conteudo do recorte: entrada igual -> saida igual
        score = round(float(np.mean(crop)) / 255.0, 6)
        return CropRevalidationResult(
            enabled=True,
            applied=True,
            person_score=score,
            not_person_score=round(1.0 - score, 6),
            passed=score >= self.threshold,
            threshold=self.threshold,
            mode="block",
            inference_ms=3.5,
            model_path=self.model_path,
            reason="ok",
            block_eligible=False,
            block_reason=None,
            quality=quality or {},
            device=self.device,
        )

    def _validate_direct(self, frame, bbox):
        if not self.enabled:
            return CropRevalidationResult(enabled=False, applied=False, mode="block", reason="disabled")
        if frame is None:
            return CropRevalidationResult(enabled=True, applied=False, mode="block", reason="missing_frame")
        crop, quality = self.crop_with_quality(frame, bbox)
        if crop is None:
            return CropRevalidationResult(
                enabled=True, applied=False, mode="block", reason="invalid_bbox", quality=quality
            )
        return self.infer_prepared_crop(crop, quality)

    def validate(self, frame, bbox, **kwargs):
        return self._validate_direct(frame, bbox)


class DirectPoolTransport:
    """Transporte de teste: serializa como o socket faria, sem rede."""

    def __init__(self, pool):
        self.pool = pool
        self.enviados = 0
        self.bytes_enviados = 0

    def submit(self, request, crop, quality):
        self.enviados += 1
        array = np.ascontiguousarray(crop)
        self.bytes_enviados += array.nbytes
        # round-trip binario identico ao do socket: bytes -> reshape
        recebido = np.frombuffer(array.tobytes(), dtype=np.uint8).reshape(array.shape)
        resultado = self.pool.submit(
            recebido,
            quality=quality,
            priority=request.priority,
            deadline_monotonic_ns=request.deadline_monotonic_ns,
            payload_bytes=array.nbytes,
        )
        return IA2SocketServer._serialize_result(
            resultado,
            camera_id=int(request.camera_id),
            frame_id=int(request.frame_id) if request.frame_id is not None else -1,
            generation_id=int(request.generation_id) if request.generation_id is not None else -1,
            track_id=int(request.track_id) if isinstance(request.track_id, int) else -1,
            pool=self.pool,
        )


def _request(camera_id=37, **kwargs):
    payload = {
        "job_id": new_job_id(),
        "camera_id": camera_id,
        "model_type": MODEL_IA2,
        "metadata": {"frame": np.zeros((40, 40, 3), dtype=np.uint8), "bbox": [1.0, 1.0, 20.0, 30.0]},
    }
    payload.update(kwargs)
    return IA2Request(**payload)


@pytest.fixture
def pool_on(monkeypatch):
    monkeypatch.setattr(settings, "ia2_pool_enabled", True)
    monkeypatch.setattr(settings, "ia2_pool_worker_count", 1)
    monkeypatch.setattr(settings, "ia2_pool_max_queue_size", 8)
    monkeypatch.setattr(settings, "ia2_pool_timeout_ms", 3000)


def _pool(revalidator) -> IA2Pool:
    pool = IA2Pool(revalidator_provider=lambda: revalidator)
    pool.start()
    for _ in range(200):
        if pool.state in {STATE_READY, STATE_FAILED}:
            break
        time.sleep(0.01)
    return pool


# ------------------------------------------------------------------ equivalência


@pytest.mark.parametrize(
    "descricao, valor_pixel",
    [
        ("pessoa clara", 200),
        ("nao pessoa", 5),
        ("caso incerto", 128),
        ("crop escuro", 20),
    ],
)
def test_equivalencia_local_versus_central(pool_on, descricao, valor_pixel):
    class Fixo(ScriptedRevalidator):
        def crop_with_quality(self, frame, bbox):
            return np.full((6, 4, 3), valor_pixel, dtype=np.uint8), {"blur": 0.1}

    local_rev = Fixo()
    central_rev = Fixo()
    pool = _pool(central_rev)
    try:
        request = _request(frame_id=3, generation_id=2, track_id=11)
        local = LocalIA2InferenceClient(lambda: local_rev).infer(request)
        central = CentralIA2InferenceClient(
            DirectPoolTransport(pool), revalidator_provider=lambda: central_rev
        ).infer(request)

        assert abs((local.person_score or 0) - (central.person_score or 0)) < TOLERANCIA, descricao
        assert abs((local.not_person_score or 0) - (central.not_person_score or 0)) < TOLERANCIA
        assert local.passed == central.passed
        assert local.threshold == central.threshold
        assert local.applied == central.applied
        assert local.native.block_eligible == central.native.block_eligible
        assert local.native.quality == central.native.quality
        assert local.backend == "local" and central.backend == "central"
    finally:
        pool.stop()


def test_crop_sem_deteccao_resolve_local_sem_ocupar_pool(pool_on):
    revalidator = ScriptedRevalidator(crop_valido=False)
    pool = _pool(revalidator)
    try:
        central = CentralIA2InferenceClient(
            DirectPoolTransport(pool), revalidator_provider=lambda: revalidator
        )
        resultado = central.infer(_request())
        assert resultado.applied is False
        assert resultado.native.reason == "invalid_bbox"
        assert pool.stats.jobs_submitted_total == 0, "bbox invalido nao vai para a pool"
    finally:
        pool.stop()


def test_ia2_desabilitada_mantem_resultado_local(pool_on):
    revalidator = ScriptedRevalidator(enabled=False)
    pool = _pool(revalidator)
    try:
        central = CentralIA2InferenceClient(
            DirectPoolTransport(pool), revalidator_provider=lambda: revalidator
        )
        resultado = central.infer(_request())
        assert resultado.enabled is False
        assert resultado.applied is False
        assert pool.stats.jobs_submitted_total == 0
    finally:
        pool.stop()


def test_pool_generation_chega_ao_resultado(pool_on):
    revalidator = ScriptedRevalidator()
    pool = _pool(revalidator)
    try:
        central = CentralIA2InferenceClient(
            DirectPoolTransport(pool), revalidator_provider=lambda: revalidator
        )
        resultado = central.infer(_request())
        assert resultado.pool_generation_id == pool.generation_id
    finally:
        pool.stop()


# ------------------------------------------------------------------ identidade


def test_resposta_de_outra_camera_e_rejeitada(pool_on):
    revalidator = ScriptedRevalidator()
    pool = _pool(revalidator)

    class TrocaCamera(DirectPoolTransport):
        def submit(self, request, crop, quality):
            payload = super().submit(request, crop, quality)
            payload["camera_id"] = 999
            return payload

    try:
        local_rev = ScriptedRevalidator()
        client = FallbackIA2InferenceClient(
            CentralIA2InferenceClient(TrocaCamera(pool), revalidator_provider=lambda: revalidator),
            LocalIA2InferenceClient(lambda: local_rev),
            strict=False,
        )
        resultado = client.infer(_request(camera_id=37))
        assert resultado.camera_id == 37
        assert resultado.fallback_used is True, "resposta de outra camera cai para local"
    finally:
        pool.stop()


def test_resposta_de_track_divergente_e_rejeitada(pool_on):
    revalidator = ScriptedRevalidator()
    pool = _pool(revalidator)

    class TrocaTrack(DirectPoolTransport):
        def submit(self, request, crop, quality):
            payload = super().submit(request, crop, quality)
            payload["track_id"] = 4242
            return payload

    try:
        client = FallbackIA2InferenceClient(
            CentralIA2InferenceClient(TrocaTrack(pool), revalidator_provider=lambda: revalidator),
            LocalIA2InferenceClient(lambda: ScriptedRevalidator()),
            strict=True,
        )
        resultado = client.infer(_request(track_id=11))
        assert resultado.error_code == ERROR_POOL_UNAVAILABLE
        assert resultado.passed is None, "identidade divergente nao pode virar REJECT"
    finally:
        pool.stop()


# ------------------------------------------------------------------ política segura


def test_strict_nao_executa_modelo_local_quando_pool_cai(pool_on):
    local_rev = ScriptedRevalidator()

    class Quebrado:
        def submit(self, request, crop, quality):
            raise RuntimeError("pool fora do ar")

    central_rev = ScriptedRevalidator()
    client = FallbackIA2InferenceClient(
        CentralIA2InferenceClient(Quebrado(), revalidator_provider=lambda: central_rev),
        LocalIA2InferenceClient(lambda: local_rev),
        strict=True,
    )
    resultado = client.infer(_request())
    assert resultado.error_code == ERROR_POOL_UNAVAILABLE
    assert resultado.applied is False
    assert resultado.passed is None, "falha da pool nao pode virar REJECT"
    assert local_rev.inferencias == 0, "modo estrito nao pode rodar o modelo local"


def test_prefer_cai_para_local_quando_pool_cai(pool_on):
    local_rev = ScriptedRevalidator()

    class Quebrado:
        def submit(self, request, crop, quality):
            raise RuntimeError("pool fora do ar")

    antes = aux_metrics.snapshot(MODEL_IA2)["fallback_local_total"]
    client = FallbackIA2InferenceClient(
        CentralIA2InferenceClient(Quebrado(), revalidator_provider=lambda: ScriptedRevalidator()),
        LocalIA2InferenceClient(lambda: local_rev),
        strict=False,
    )
    resultado = client.infer(_request())
    assert resultado.ok
    assert resultado.fallback_used is True
    assert local_rev.inferencias == 1
    assert aux_metrics.snapshot(MODEL_IA2)["fallback_local_total"] == antes + 1


def test_fila_cheia_nao_vira_reject(pool_on):
    from app.runtime.ia2_pool import IA2PoolQueueFull

    class Cheia:
        def submit(self, request, crop, quality):
            raise IA2PoolQueueFull("ia2_pool_queue_full")

    client = FallbackIA2InferenceClient(
        CentralIA2InferenceClient(Cheia(), revalidator_provider=lambda: ScriptedRevalidator()),
        LocalIA2InferenceClient(lambda: ScriptedRevalidator()),
        strict=True,
    )
    resultado = client.infer(_request())
    assert resultado.passed is None
    assert resultado.applied is False


def test_factory_com_canario_usa_pool(monkeypatch, pool_on):
    monkeypatch.setattr(settings, "ia2_execution_mode", "central_prefer")
    monkeypatch.setattr(settings, "ia2_central_camera_ids", "37")
    client = build_ia2_client(37, local_provider=lambda: ScriptedRevalidator())
    assert isinstance(client, FallbackIA2InferenceClient)
    client_fora = build_ia2_client(38, local_provider=lambda: ScriptedRevalidator())
    assert isinstance(client_fora, LocalIA2InferenceClient)
