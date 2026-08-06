"""Deriva a classificacao automatica de um evento a partir do reconhecimento das IAs.

Quando IA2 e IA3 concordam sobre o evento, a conclusao delas ja responde o que o
operador responderia na tela de validacao, e o evento nao precisa entrar na fila de
revisao manual. Discordancia, dado ausente ou revalidador pulado continuam indo para
o operador — o silencio da IA nunca vira uma conclusao.

O resultado NAO vira EventFeedback: ele fica em campos proprios do evento, para nao
contaminar as metricas de operador nem o auto-tuning de parametros, que aprende com
rotulo humano. Se a IA alimentasse o proprio treino, os thresholds passariam a se
confirmar sozinhos.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Limiar em que o revalidador de recorte (IA2) passa a afirmar "pessoa".
IA2_PERSON_SCORE_MIN = 0.5

# Rotulos que a IA pode concluir sozinha. "inconclusive" nao entra aqui de proposito:
# ele e o resultado de nao haver conclusao, e nesse caso o evento vai para o operador.
AI_VALIDATION_LABELS = ("true_positive", "false_positive", "expected_event")


def detail_segments(details: Any) -> list[str]:
    return [segment.strip() for segment in str(details or "").split("|") if segment.strip()]


def find_detail_segment(details: Any, marker: str) -> str | None:
    for segment in detail_segments(details):
        if segment.startswith(marker):
            return segment
    return None


def extract_segment_value(segment: str | None, marker: str) -> str | None:
    if not segment or marker not in segment:
        return None
    raw = segment.split(marker, 1)[1].split()[0].strip()
    return raw or None


def extract_segment_float(segment: str | None, marker: str) -> float | None:
    raw = extract_segment_value(segment, marker)
    try:
        return float(raw) if raw is not None else None
    except Exception:
        return None


@dataclass(frozen=True)
class AiValidation:
    """Conclusao automatica de um evento, quando existe."""

    label: str
    reason: str
    ia2_score: float
    ia3_score: float
    ia3_threshold: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "reason": self.reason,
            "ia2_score": self.ia2_score,
            "ia3_score": self.ia3_score,
            "ia3_threshold": self.ia3_threshold,
        }


def derive_ai_validation(event: Any) -> AiValidation | None:
    """Conclui o evento quando IA2 e IA3 concordam; devolve None se precisa de operador.

    O consenso decide se ha pessoa. O que separa true_positive de expected_event e o
    alarm_eligible ja gravado no evento: pessoa em cena onde a politica preve alarme e
    verdadeiro positivo; pessoa em cena onde a politica nao preve alarme (fluxo normal,
    janela esperada) e evento esperado.
    """
    details = getattr(event, "details", None)
    details_text = str(details or "")

    # Um revalidador cancelado ou pulado nao produziu opiniao sobre este evento.
    if "revalidator_canceled=true" in details_text:
        return None
    if extract_segment_value(find_detail_segment(details, "revalidator_skipped="), "revalidator_skipped="):
        return None
    if extract_segment_value(find_detail_segment(details, "far_revalidator_skipped="), "far_revalidator_skipped="):
        return None

    ia2_segment = find_detail_segment(details, "revalidator_person=")
    ia3_segment = find_detail_segment(details, "far_revalidator_person=")
    ia2_score = extract_segment_float(ia2_segment, "revalidator_person=")
    ia3_score = extract_segment_float(ia3_segment, "far_revalidator_person=")
    if ia2_score is None or ia3_score is None:
        return None

    # IA3 e comparada ao proprio limiar: a escala dela nao é a da IA2.
    ia3_threshold = extract_segment_float(ia3_segment, "threshold=")
    if ia3_threshold is None:
        return None

    ia2_person = ia2_score >= IA2_PERSON_SCORE_MIN
    ia3_person = ia3_score >= ia3_threshold
    if ia2_person != ia3_person:
        return None

    if not ia2_person:
        return AiValidation(
            label="false_positive",
            reason="ia2_ia3_concordam_nao_pessoa",
            ia2_score=ia2_score,
            ia3_score=ia3_score,
            ia3_threshold=ia3_threshold,
        )

    alarm_eligible = getattr(event, "alarm_eligible", None)
    if alarm_eligible is False:
        return AiValidation(
            label="expected_event",
            reason="ia2_ia3_concordam_pessoa_sem_alarme_previsto",
            ia2_score=ia2_score,
            ia3_score=ia3_score,
            ia3_threshold=ia3_threshold,
        )

    return AiValidation(
        label="true_positive",
        reason="ia2_ia3_concordam_pessoa",
        ia2_score=ia2_score,
        ia3_score=ia3_score,
        ia3_threshold=ia3_threshold,
    )
