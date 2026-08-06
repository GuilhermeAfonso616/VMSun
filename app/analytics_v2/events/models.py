from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class EventEvidence:
    snapshot_path: str | None = None
    clip_path: str | None = None
    bbox: list[float] | None = None
    trajectory: list[tuple[float, float]] = field(default_factory=list)
    line_id: str | None = None
    zone_id: str | None = None
    footpoint: tuple[float, float] | None = None
    reason: str | None = None


@dataclass(slots=True)
class AlarmEvent:
    event_id: str
    camera_id: int
    rule_id: str
    track_id: int
    timestamp_start: datetime
    timestamp_end: datetime
    event_score: float
    priority: str
    event_type: str
    evidence: EventEvidence
    explanation: str
    metadata: dict = field(default_factory=dict)
