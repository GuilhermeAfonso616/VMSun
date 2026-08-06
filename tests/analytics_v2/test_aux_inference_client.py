"""Etapa 3A — modos, seleção canário, identidade de job, fallback e equivalência."""

from __future__ import annotations

import pytest

from app.analytics_v2.revalidation.aux_inference_client import (
    AuxInferenceUnavailable,
    CentralIA2InferenceClient,
    FallbackIA2InferenceClient,
    FallbackIA3InferenceClient,
    LocalIA2InferenceClient,
    LocalIA3InferenceClient,
    aux_metrics,
    build_ia2_client,
    build_ia3_client,
    camera_execution_state,
    central_selected,
    execution_mode,
)
from app.analytics_v2.revalidation.aux_inference_types import (
    ERROR_POOL_UNAVAILABLE,
    IA2Request,
    IA2Result,
    IA3Request,
    IA3Result,
    MODEL_IA2,
    MODEL_IA3,
    PRIORITY_IA2,
    PRIORITY_IA3,
    deadline_from_ms,
    monotonic_ns,
    new_job_id,
)
from app.core.config import settings


class FakeIA2Native:
    def __init__(self, person_score=0.87, applied=True):
        self.enabled = True
        self.applied = applied
        self.person_score = person_score
        self.not_person_score = 1.0 - person_score
        self.passed = person_score >= 0.5
        self.threshold = 0.5
        self.mode = "block"
        self.inference_ms = 11.0
        self.model_path = "models/revalidator/v5.pt"
        self.reason = None
        self.quality = {"blur": 0.1}
        self.device = "cuda:0"


class FakeIA3Native:
    def __init__(self, person_far_score=0.62, applied=True):
        self.enabled = True
        self.triggered = True
        self.applied = applied
        self.person_far_score = person_far_score
        self.not_person_far_score = 1.0 - person_far_score
        self.passed = person_far_score >= 0.48
        self.threshold = 0.48
        self.inference_ms = 9.0
        self.model_path = "models/revalidator_far/v1.pt"
        self.reason = None
        self.trigger_reason = "bbox_small"
        self.quality = {}
        self.device = "cuda:0"


class FakeRevalidator:
    def __init__(self, native, *, raises: Exception | None = None):
        self.native = native
        self.raises = raises
        self.calls = 0

    def validate(self, frame, bbox, **kwargs):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.native


def _fake_frame():
    import numpy as np

    return np.zeros((32, 32, 3), dtype=np.uint8)


def _ia2_request(camera_id=37, **kwargs):
    payload = {
        "job_id": new_job_id(),
        "camera_id": camera_id,
        "model_type": MODEL_IA2,
        "metadata": {"frame": _fake_frame(), "bbox": [0.0, 0.0, 10.0, 20.0]},
    }
    payload.update(kwargs)
    return IA2Request(**payload)


def _ia3_request(camera_id=37, **kwargs):
    payload = {
        "job_id": new_job_id(),
        "camera_id": camera_id,
        "model_type": MODEL_IA3,
        "metadata": {"frame": object(), "bbox": [0.0, 0.0, 10.0, 20.0]},
    }
    payload.update(kwargs)
    return IA3Request(**payload)


# ----------------------------------------------------------------- modos e canário


def test_modo_padrao_e_local(monkeypatch):
    monkeypatch.setattr(settings, "ia2_execution_mode", "local")
    monkeypatch.setattr(settings, "ia3_execution_mode", "local")
    assert execution_mode(MODEL_IA2) == "local"
    assert execution_mode(MODEL_IA3) == "local"


def test_modo_invalido_gera_erro_explicito(monkeypatch):
    monkeypatch.setattr(settings, "ia2_execution_mode", "turbo")
    with pytest.raises(ValueError, match="central_prefer"):
        execution_mode(MODEL_IA2)


def test_selecao_canario_por_lista(monkeypatch):
    monkeypatch.setattr(settings, "ia2_execution_mode", "central_prefer")
    monkeypatch.setattr(settings, "ia2_central_camera_ids", "36, 37")
    assert central_selected(MODEL_IA2, 36)
    assert central_selected(MODEL_IA2, 37)
    assert not central_selected(MODEL_IA2, 38)


def test_selecao_canario_wildcard(monkeypatch):
    monkeypatch.setattr(settings, "ia2_execution_mode", "central_strict")
    monkeypatch.setattr(settings, "ia2_central_camera_ids", "*")
    assert central_selected(MODEL_IA2, 999)


def test_modo_local_ignora_lista(monkeypatch):
    monkeypatch.setattr(settings, "ia2_execution_mode", "local")
    monkeypatch.setattr(settings, "ia2_central_camera_ids", "*")
    assert not central_selected(MODEL_IA2, 37)


def test_ia2_e_ia3_tem_canarios_independentes(monkeypatch):
    monkeypatch.setattr(settings, "ia2_execution_mode", "central_prefer")
    monkeypatch.setattr(settings, "ia2_central_camera_ids", "36,37")
    monkeypatch.setattr(settings, "ia3_execution_mode", "central_prefer")
    monkeypatch.setattr(settings, "ia3_central_camera_ids", "36")
    assert central_selected(MODEL_IA2, 37)
    assert not central_selected(MODEL_IA3, 37)


def test_factory_devolve_local_fora_do_canario(monkeypatch):
    monkeypatch.setattr(settings, "ia2_execution_mode", "central_prefer")
    monkeypatch.setattr(settings, "ia2_central_camera_ids", "36")
    client = build_ia2_client(99, local_provider=lambda: FakeRevalidator(FakeIA2Native()))
    assert isinstance(client, LocalIA2InferenceClient)


def test_factory_devolve_fallback_dentro_do_canario(monkeypatch):
    monkeypatch.setattr(settings, "ia2_execution_mode", "central_prefer")
    monkeypatch.setattr(settings, "ia2_central_camera_ids", "36")
    client = build_ia2_client(36, local_provider=lambda: FakeRevalidator(FakeIA2Native()))
    assert isinstance(client, FallbackIA2InferenceClient)
    assert client._strict is False


def test_factory_marca_strict(monkeypatch):
    monkeypatch.setattr(settings, "ia3_execution_mode", "central_strict")
    monkeypatch.setattr(settings, "ia3_central_camera_ids", "*")
    client = build_ia3_client(1, local_provider=lambda: FakeRevalidator(FakeIA3Native()))
    assert isinstance(client, FallbackIA3InferenceClient)
    assert client._strict is True


# ----------------------------------------------------------------- identidade de job


def test_resultado_valida_identidade_completa():
    request = _ia2_request(frame_id=10, generation_id=3, track_id=7)
    result = IA2Result.from_native(request, FakeIA2Native())
    assert result.matches(request)


@pytest.mark.parametrize(
    "campo, valor",
    [
        ("camera_id", 99),
        ("frame_id", 11),
        ("generation_id", 4),
        ("track_id", 8),
        ("job_id", "outro"),
    ],
)
def test_resultado_divergente_e_rejeitado(campo, valor):
    request = _ia2_request(frame_id=10, generation_id=3, track_id=7)
    result = IA2Result.from_native(request, FakeIA2Native())
    setattr(result, campo, valor)
    assert not result.matches(request)


def test_deadline_expirado():
    request = _ia2_request(deadline_monotonic_ns=monotonic_ns() - 1)
    assert request.expired()
    assert request.remaining_seconds() == 0.0


def test_deadline_futuro_nao_expira():
    request = _ia2_request(deadline_monotonic_ns=deadline_from_ms(5000))
    assert not request.expired()
    assert request.remaining_seconds() > 0.0


def test_sem_deadline_nunca_expira():
    assert not _ia2_request(deadline_monotonic_ns=0).expired()


def test_prioridade_padrao_por_modelo():
    assert _ia2_request().priority == PRIORITY_IA2
    assert _ia3_request().priority == PRIORITY_IA3


# ----------------------------------------------------------------- execução local


def test_local_ia2_preserva_resultado_nativo():
    native = FakeIA2Native(person_score=0.91)
    revalidator = FakeRevalidator(native)
    client = LocalIA2InferenceClient(lambda: revalidator)
    result = client.infer(_ia2_request())
    assert result.native is native
    assert result.person_score == 0.91
    assert result.backend == "local"
    assert result.ok
    assert revalidator.calls == 1


def test_local_ia3_repassa_base_quality_e_ia2():
    native = FakeIA3Native()
    captured = {}

    class Capturing(FakeRevalidator):
        def validate(self, frame, bbox, **kwargs):
            captured.update(kwargs)
            return super().validate(frame, bbox, **kwargs)

    client = LocalIA3InferenceClient(lambda: Capturing(native))
    request = _ia3_request(base_quality={"blur": 0.2})
    request.metadata["ia2_result"] = "ia2-obj"
    result = client.infer(request)
    assert result.person_far_score == native.person_far_score
    assert captured["base_quality"] == {"blur": 0.2}
    assert captured["ia2_result"] == "ia2-obj"


def test_falha_local_nao_vira_reject():
    client = LocalIA2InferenceClient(lambda: FakeRevalidator(None, raises=RuntimeError("boom")))
    result = client.infer(_ia2_request())
    assert result.error_code is not None
    assert result.applied is False
    assert result.passed is None, "falha nao pode ser interpretada como ausencia de pessoa"


# ----------------------------------------------------------------- fallback


def test_central_prefer_cai_para_local_com_metrica(monkeypatch):
    monkeypatch.setattr(settings, "ia2_pool_enabled", False)
    revalidator = FakeRevalidator(FakeIA2Native())
    antes = aux_metrics.snapshot(MODEL_IA2)["fallback_local_total"]
    client = FallbackIA2InferenceClient(
        CentralIA2InferenceClient(None),
        LocalIA2InferenceClient(lambda: revalidator),
        strict=False,
    )
    result = client.infer(_ia2_request())
    assert result.ok
    assert result.fallback_used is True
    assert revalidator.calls == 1
    assert aux_metrics.snapshot(MODEL_IA2)["fallback_local_total"] == antes + 1


def test_central_strict_bloqueia_fallback(monkeypatch):
    monkeypatch.setattr(settings, "ia2_pool_enabled", False)
    revalidator = FakeRevalidator(FakeIA2Native())
    client = FallbackIA2InferenceClient(
        CentralIA2InferenceClient(None),
        LocalIA2InferenceClient(lambda: revalidator),
        strict=True,
    )
    result = client.infer(_ia2_request())
    assert result.error_code == ERROR_POOL_UNAVAILABLE
    assert revalidator.calls == 0, "modo estrito nao pode executar o modelo local"
    assert result.applied is False
    assert result.passed is None, "pool ausente nao pode virar REJECT"


class CroppingRevalidator(FakeRevalidator):
    """Revalidador com o contrato usado pelo cliente central (Etapa 3B)."""

    enabled = True

    def crop_with_quality(self, frame, bbox):
        import numpy as np

        return np.zeros((4, 4, 3), dtype=np.uint8), {"blur": 0.1}

    def _validate_direct(self, frame, bbox):
        return self.native


def _payload_from(request, *, person_score=0.77, **overrides):
    payload = {
        "camera_id": int(request.camera_id),
        "frame_id": request.frame_id,
        "generation_id": request.generation_id,
        "track_id": request.track_id,
        "pool_generation_id": 1,
        "queue_wait_ms": 1.0,
        "enabled": True,
        "applied": True,
        "person_score": person_score,
        "not_person_score": 1.0 - person_score,
        "passed": person_score >= 0.5,
        "threshold": 0.5,
        "mode": "block",
        "inference_ms": 8.0,
        "model_path": "models/ia2.pt",
        "reason": "ok",
        "block_eligible": False,
        "block_reason": None,
        "quality": {},
        "device": "cpu",
    }
    payload.update(overrides)
    return payload


def test_resposta_stale_da_pool_nao_e_aplicada(monkeypatch):
    monkeypatch.setattr(settings, "ia2_pool_enabled", True)

    class StaleTransport:
        def submit(self, request, crop, quality):
            return _payload_from(request, camera_id=1)

    revalidator = CroppingRevalidator(FakeIA2Native())
    client = FallbackIA2InferenceClient(
        CentralIA2InferenceClient(StaleTransport(), revalidator_provider=lambda: revalidator),
        LocalIA2InferenceClient(lambda: revalidator),
        strict=False,
    )
    result = client.infer(_ia2_request(camera_id=37))
    assert result.fallback_used is True
    assert result.camera_id == 37


def test_pool_saudavel_nao_usa_local(monkeypatch):
    monkeypatch.setattr(settings, "ia2_pool_enabled", True)

    class GoodTransport:
        def submit(self, request, crop, quality):
            return _payload_from(request)

    central_rev = CroppingRevalidator(FakeIA2Native())
    local_rev = FakeRevalidator(FakeIA2Native())
    client = FallbackIA2InferenceClient(
        CentralIA2InferenceClient(GoodTransport(), revalidator_provider=lambda: central_rev),
        LocalIA2InferenceClient(lambda: local_rev),
        strict=True,
    )
    result = client.infer(_ia2_request())
    assert result.backend == "central"
    assert result.person_score == 0.77
    assert result.pool_generation_id == 1
    assert local_rev.calls == 0


# ----------------------------------------------------------------- equivalência


def test_equivalencia_local_versus_central(monkeypatch):
    """Mesmo resultado por dois caminhos deve produzir o mesmo tipo de saida.

    A equivalencia de execucao real (pool + preprocessamento) e coberta em
    tests/runtime/test_ia2_central_equivalence.py.
    """
    monkeypatch.setattr(settings, "ia2_pool_enabled", True)
    native = FakeIA2Native(person_score=0.6421)
    request = _ia2_request(frame_id=4, generation_id=2, track_id=9)

    local = LocalIA2InferenceClient(lambda: FakeRevalidator(native)).infer(request)

    class Transport:
        def submit(self, req, crop, quality):
            return _payload_from(req, person_score=0.6421)

    remote = CentralIA2InferenceClient(
        Transport(), revalidator_provider=lambda: CroppingRevalidator(native)
    ).infer(request)

    tolerancia = 1e-6
    assert abs((local.person_score or 0) - (remote.person_score or 0)) < tolerancia
    assert local.passed == remote.passed
    assert local.threshold == remote.threshold
    assert local.applied == remote.applied
    assert local.backend != remote.backend


def test_equivalencia_ia3_local_versus_central():
    native = FakeIA3Native(person_far_score=0.3311)
    request = _ia3_request(frame_id=1, generation_id=1, track_id=2)
    local = LocalIA3InferenceClient(lambda: FakeRevalidator(native)).infer(request)
    remote = IA3Result.from_native(request, native, backend="central")
    assert abs((local.person_far_score or 0) - (remote.person_far_score or 0)) < 1e-6
    assert local.passed == remote.passed
    assert local.triggered == remote.triggered


# ----------------------------------------------------------------- observabilidade


def test_estado_por_camera_nao_expoe_payload(monkeypatch):
    monkeypatch.setattr(settings, "ia2_execution_mode", "central_prefer")
    monkeypatch.setattr(settings, "ia2_central_camera_ids", "37")
    monkeypatch.setattr(settings, "ia3_execution_mode", "local")
    state = camera_execution_state(37)
    assert state["ia2_execution_mode"] == "central"
    assert state["ia3_execution_mode"] == "local"
    serializado = str(state)
    assert "frame" not in serializado and "crop" not in serializado
