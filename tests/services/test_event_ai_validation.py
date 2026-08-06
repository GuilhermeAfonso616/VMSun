"""Regras de conclusao automatica de evento por consenso IA2+IA3."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.event_ai_validation import derive_ai_validation


def evento(details: str, *, alarm_eligible: bool | None = True):
    return SimpleNamespace(details=details, alarm_eligible=alarm_eligible)


def detalhes(*, ia2: float | None = None, ia3: float | None = None, ia3_threshold: float = 0.10, extra: str = ""):
    partes = ["rule=intrusion_zone"]
    if ia2 is not None:
        partes.append(f"revalidator_person={ia2} threshold=0.35")
    if ia3 is not None:
        partes.append(f"far_revalidator_person={ia3} threshold={ia3_threshold}")
    if extra:
        partes.append(extra)
    return " | ".join(partes)


class TestConsensoConclui:
    def test_ambas_confirmam_pessoa_com_alarme_previsto_e_verdadeiro_positivo(self):
        resultado = derive_ai_validation(evento(detalhes(ia2=0.91, ia3=0.44), alarm_eligible=True))

        assert resultado is not None
        assert resultado.label == "true_positive"
        assert resultado.reason == "ia2_ia3_concordam_pessoa"

    def test_ambas_confirmam_pessoa_sem_alarme_previsto_e_evento_esperado(self):
        # Pessoa real, mas a politica nao preve alarme ali (fluxo normal/janela esperada).
        resultado = derive_ai_validation(evento(detalhes(ia2=0.88, ia3=0.30), alarm_eligible=False))

        assert resultado is not None
        assert resultado.label == "expected_event"

    def test_ambas_negam_pessoa_e_falso_positivo(self):
        resultado = derive_ai_validation(evento(detalhes(ia2=0.12, ia3=0.02)))

        assert resultado is not None
        assert resultado.label == "false_positive"
        assert resultado.reason == "ia2_ia3_concordam_nao_pessoa"

    def test_ia3_usa_o_proprio_limiar_e_nao_a_escala_da_ia2(self):
        # 0.20 e "pessoa" para um limiar de 0.10 e "nao pessoa" para 0.50.
        confirma = derive_ai_validation(evento(detalhes(ia2=0.80, ia3=0.20, ia3_threshold=0.10)))
        assert confirma is not None and confirma.label == "true_positive"

        discorda = derive_ai_validation(evento(detalhes(ia2=0.80, ia3=0.20, ia3_threshold=0.50)))
        assert discorda is None


class TestVaiParaOOperador:
    def test_discordancia_entre_ia2_e_ia3_nao_conclui(self):
        assert derive_ai_validation(evento(detalhes(ia2=0.95, ia3=0.01))) is None
        assert derive_ai_validation(evento(detalhes(ia2=0.05, ia3=0.90))) is None

    def test_ia3_nao_solicitada_nao_conclui(self):
        assert derive_ai_validation(evento(detalhes(ia2=0.95))) is None

    def test_ia2_ausente_nao_conclui(self):
        assert derive_ai_validation(evento(detalhes(ia3=0.40))) is None

    def test_revalidador_pulado_nao_conclui(self):
        detalhe = detalhes(ia2=0.95, ia3=0.40, extra="revalidator_skipped=crop_too_small")
        assert derive_ai_validation(evento(detalhe)) is None

    def test_ia3_pulada_nao_conclui(self):
        detalhe = detalhes(ia2=0.95, ia3=0.40, extra="far_revalidator_skipped=no_frame")
        assert derive_ai_validation(evento(detalhe)) is None

    def test_revalidador_cancelado_nao_conclui(self):
        detalhe = detalhes(ia2=0.95, ia3=0.40, extra="revalidator_canceled=true")
        assert derive_ai_validation(evento(detalhe)) is None

    def test_ia3_sem_limiar_registrado_nao_conclui(self):
        # Sem o limiar nao da para saber o que o score da IA3 significa.
        detalhe = "rule=intrusion_zone | revalidator_person=0.95 threshold=0.35 | far_revalidator_person=0.40"
        assert derive_ai_validation(evento(detalhe)) is None

    def test_evento_sem_detalhes_nao_conclui(self):
        assert derive_ai_validation(evento("")) is None
        assert derive_ai_validation(evento(None)) is None


class TestFormatoDeProducao:
    """Reproduz as f-strings de app/runtime/event_revalidation.py.

    Se o formato de `details` mudar la, estes testes quebram — e devem quebrar,
    porque a derivacao le exatamente esse texto.
    """

    @staticmethod
    def detalhes_reais(*, ia2_score=None, ia2_threshold=0.35, ia3_score=None, ia3_threshold=0.100,
                       ia2_skip=None, ia3_skip=None):
        texto = "Pessoa entrou na ROI. point=(10, 20)"
        if ia2_skip:
            texto = f"{texto} | revalidator_skipped={ia2_skip} mode=block"
        elif ia2_score is not None:
            texto = f"{texto} | revalidator_person={ia2_score:.3f} threshold={ia2_threshold:.2f} mode=block"
        if ia3_skip:
            texto = f"{texto} | far_revalidator_skipped={ia3_skip} mode=audit"
        elif ia3_score is not None:
            texto = f"{texto} | far_revalidator_person={ia3_score:.3f} threshold={ia3_threshold:.3f} mode=audit"
        return texto

    def test_consenso_pessoa_no_formato_real(self):
        detalhe = self.detalhes_reais(ia2_score=0.912, ia3_score=0.443)
        resultado = derive_ai_validation(evento(detalhe, alarm_eligible=True))

        assert resultado is not None
        assert resultado.label == "true_positive"

    def test_consenso_nao_pessoa_no_formato_real(self):
        detalhe = self.detalhes_reais(ia2_score=0.104, ia3_score=0.022)
        resultado = derive_ai_validation(evento(detalhe))

        assert resultado is not None
        assert resultado.label == "false_positive"

    def test_score_ausente_vira_traco_e_nao_conclui(self):
        # O runtime grava "-" quando nao ha score; isso nao pode virar 0.0.
        detalhe = "Pessoa entrou na ROI | revalidator_person=- threshold=0.35 mode=block | far_revalidator_person=0.400 threshold=0.100 mode=audit"
        assert derive_ai_validation(evento(detalhe)) is None

    def test_skip_no_formato_real_nao_conclui(self):
        detalhe = self.detalhes_reais(ia2_score=0.900, ia3_skip="no_candidate")
        assert derive_ai_validation(evento(detalhe)) is None


class TestFronteiras:
    @pytest.mark.parametrize(
        "ia2, esperado_pessoa",
        [(0.49, False), (0.50, True), (0.51, True)],
    )
    def test_limiar_da_ia2_e_inclusivo(self, ia2, esperado_pessoa):
        resultado = derive_ai_validation(evento(detalhes(ia2=ia2, ia3=0.90 if esperado_pessoa else 0.01)))

        assert resultado is not None
        assert (resultado.label == "true_positive") is esperado_pessoa

    def test_score_da_ia3_exatamente_no_limiar_conta_como_pessoa(self):
        resultado = derive_ai_validation(evento(detalhes(ia2=0.80, ia3=0.10, ia3_threshold=0.10)))

        assert resultado is not None
        assert resultado.label == "true_positive"

    def test_alarm_eligible_nulo_nao_vira_evento_esperado(self):
        # None significa "nao informado", nao "sem alarme previsto".
        resultado = derive_ai_validation(evento(detalhes(ia2=0.90, ia3=0.50), alarm_eligible=None))

        assert resultado is not None
        assert resultado.label == "true_positive"

    def test_scores_expostos_para_auditoria(self):
        resultado = derive_ai_validation(evento(detalhes(ia2=0.77, ia3=0.33, ia3_threshold=0.10)))

        assert resultado is not None
        assert resultado.ia2_score == pytest.approx(0.77)
        assert resultado.ia3_score == pytest.approx(0.33)
        assert resultado.ia3_threshold == pytest.approx(0.10)
        assert set(resultado.as_dict()) == {"label", "reason", "ia2_score", "ia3_score", "ia3_threshold"}
