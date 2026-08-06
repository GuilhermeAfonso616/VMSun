"""Constantes de apresentação compartilhadas pelas páginas web."""

from app.services.camera_configuration_service import (
    DEFAULT_HUMAN_LOITERING_SECONDS,
    HUMAN_DETECTION_SENSITIVITY_CHOICES,
    HUMAN_EVENT_MODE_CHOICES,
)

MOTION_DEFAULTS = {
    "motion_idle_interval": 1.0,
    "motion_active_interval": 0.12,
    "motion_hold_seconds": 2.0,
    "motion_detection_hold_seconds": 3.5,
    "motion_min_motion_frames": 2,
    "motion_downscale_width": 384,
    "motion_min_contour_area": 700,
    "motion_ratio_threshold": 0.0025,
    "motion_global_change_ratio_limit": 0.40,
    "motion_background_alpha": 0.025,
    "motion_warmup_frames": 20,
}

EVENT_STATUS_CHOICES = ["new", "acknowledged", "closed"]
PRIORITY_CHOICES = ["low", "medium", "high", "critical"]
HUMAN_EVENT_MODE_LABELS = {
    "person_entered": "Pessoa entrou na cena",
    "person_left": "Pessoa saiu da cena",
    "person_entered_roi": "Pessoa entrou na ROI",
    "person_left_roi": "Pessoa saiu da ROI",
    "person_loitering": "Permanência prolongada",
    "line_crossing": "Linha cruzada",
}
MONITOR_POPUP_NOTICE_EVENT_TYPES = set(HUMAN_EVENT_MODE_CHOICES) | {
    "confirmed_intrusion",
    "intrusion_default",
    "line_crossed",
    "zone_presence",
}
HUMAN_EVENT_MODE_GROUPS = {
    "person_entered": "presença",
    "person_left": "presença",
    "person_entered_roi": "ROI",
    "person_left_roi": "ROI",
    "person_loitering": "permanência longa",
    "line_crossing": "linha",
}
HUMAN_DETECTION_SENSITIVITY_LABELS = {
    "very_low": "Muito sensível",
    "low": "Sensível",
    "medium": "Padrão",
    "high": "Conservador",
}
HUMAN_DETECTION_SENSITIVITY_THRESHOLDS = {
    "very_low": 0.20,
    "low": 0.30,
    "medium": 0.45,
    "high": 0.60,
}
EVENT_TYPE_LABELS = {
    "confirmed_intrusion": "Intrusao confirmada",
    "intrusion_default": "Intrusao",
    "person_entered": "Pessoa detectada",
    "person_left": "Pessoa saiu",
    "person_entered_roi": "Pessoa entrou na ROI",
    "person_left_roi": "Pessoa saiu da ROI",
    "person_loitering": "Permanencia prolongada",
    "line_crossing": "Cruzamento de linha",
    "line_crossed": "Cruzamento de linha",
    "zone_presence": "Presenca em zona",
}
STATUS_LABELS = {
    "new": "Novo",
    "acknowledged": "Reconhecido",
    "closed": "Fechado",
    "canceled": "Cancelado",
    "persisted": "Registrado",
    "open": "Aberto",
    "log_only": "Registro",
    "pending": "Pendente",
    "applied": "Aplicada",
    "rejected": "Rejeitada",
    "error": "Erro",
    "sent": "Enviado",
    "alarm": "Alarme",
    "low_priority": "Baixa prioridade",
    "low_confidence": "Baixa confianca",
    "audit": "Auditoria",
    "suppressed": "Suprimido",
}
SEVERITY_LABELS = {
    "low": "Baixa",
    "medium": "Media",
    "high": "Alta",
    "critical": "Critica",
}
