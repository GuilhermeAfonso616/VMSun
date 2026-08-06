from __future__ import annotations

from dataclasses import dataclass, field

from .models import AlarmEvent


@dataclass(slots=True)
class EventOutputBatch:
    events: list[AlarmEvent] = field(default_factory=list)
    suppressed: list[dict] = field(default_factory=list)
