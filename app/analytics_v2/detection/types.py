from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class DetectionCandidate:
    bbox: list[float]
    score: float
    class_name: str = "person"
    detection_id: str | None = None
    embedding: list[float] | None = None
    timestamp: datetime | None = None
    source: str = "detector"
    metadata: dict = field(default_factory=dict)

    @property
    def x1(self) -> float:
        return float(self.bbox[0])

    @property
    def y1(self) -> float:
        return float(self.bbox[1])

    @property
    def x2(self) -> float:
        return float(self.bbox[2])

    @property
    def y2(self) -> float:
        return float(self.bbox[3])
