"""Contratos HTTP de eventos, feedback e sugestoes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EventUpdate(BaseModel):
    status: str | None = None
    operator_note: str | None = None
    resolution_code: str | None = None


class IncidentAssign(BaseModel):
    assignee_user_id: int | None = None


class IncidentClose(BaseModel):
    resolution_code: str | None = "other"
    comment: str | None = None


class IncidentComment(BaseModel):
    comment: str


class IncidentCreate(BaseModel):
    camera_id: int
    title: str
    description: str | None = None
    priority: str = "medium"
    team: str | None = None
    assignee_user_id: int | None = None


class IncidentDetailsUpdate(BaseModel):
    priority: str | None = None
    team: str | None = None


class IncidentChecklistUpdate(BaseModel):
    completed: bool


class IncidentCorrelate(BaseModel):
    event_ids: list[int]


class EventFeedbackCreate(BaseModel):
    label: str
    probable_cause: str | None = None
    operator_note: str | None = None
    reviewed_by: str | None = None
    auto_suggest: bool = True


class SuggestionCreateFromEvent(BaseModel):
    scope_type: str = "camera"
    scope_id: str | None = None
    suggestion_type: str = "policy_tuning"
    parameter_name: str = "alarm_confirmation_seconds"
    old_value: Any | None = None
    suggested_value: Any | None = None
    reason_summary: str | None = None
    evidence_count: int = 1
    confidence_score: float = 0.5
