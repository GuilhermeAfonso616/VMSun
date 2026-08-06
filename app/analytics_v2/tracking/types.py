from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .enums import TrackState
from .kalman import ConstantVelocityKalman


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(slots=True)
class TrackObservation:
    bbox: list[float]
    footpoint: tuple[float, float]
    score: float
    class_name: str
    timestamp: datetime
    embedding: list[float] | None = None
    source_detection_id: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class TrackHistoryPoint:
    timestamp: datetime
    bbox: list[float]
    footpoint: tuple[float, float]
    score: float
    class_name: str
    zone_ids: list[str] = field(default_factory=list)
    line_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Track:
    track_id: int
    state: TrackState
    bbox_current: list[float] | None = None
    bbox_history: list[TrackHistoryPoint] = field(default_factory=list)
    footpoint_current: tuple[float, float] | None = None
    score_avg: float = 0.0
    internal_score: float = 0.0
    dominant_class: str = "person"
    class_votes: dict[str, int] = field(default_factory=dict)
    age_frames: int = 0
    visible_frames: int = 0
    lost_frames: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    velocity_estimate: tuple[float, float] = (0.0, 0.0)
    direction_estimate: str | None = None
    embedding_latest: list[float] | None = None
    embedding_mean: list[float] | None = None
    zone_history: list[str] = field(default_factory=list)
    line_crossing_history: list[dict] = field(default_factory=list)
    event_emitted: bool = False
    metadata: dict = field(default_factory=dict)
    confirmed_at: datetime | None = None
    shadow_started_at: datetime | None = None
    candidate_frames: int = 0
    probation_frames: int = 0
    shadow_frames: int = 0
    state_change_count: int = 0
    last_association_score: float = 0.0
    last_detection_score: float = 0.0
    last_detection_class: str = "person"
    track_quality: float = 0.0
    motion_confidence: float = 0.0
    size_confidence: float = 0.0
    border_confidence: float = 1.0
    geometry_confidence: float = 0.0
    kalman: ConstantVelocityKalman = field(default_factory=ConstantVelocityKalman)

    def add_vote(self, class_name: str) -> None:
        self.class_votes[class_name] = self.class_votes.get(class_name, 0) + 1
        self.dominant_class = max(self.class_votes.items(), key=lambda item: item[1])[0]

    def class_consistency(self) -> float:
        total = sum(self.class_votes.values())
        if total <= 0:
            return 0.0
        dominant = max(self.class_votes.values())
        return _clamp01(dominant / float(total))

    def append_history(self, point: TrackHistoryPoint, max_history: int = 60) -> None:
        self.bbox_current = point.bbox
        self.footpoint_current = point.footpoint
        self.bbox_history.append(point)
        if len(self.bbox_history) > max_history:
            self.bbox_history = self.bbox_history[-max_history:]

    def current_motion_step(self) -> float:
        if len(self.bbox_history) < 2:
            return 0.0
        prev = self.bbox_history[-2].footpoint
        curr = self.bbox_history[-1].footpoint
        dx = curr[0] - prev[0]
        dy = curr[1] - prev[1]
        return (dx * dx + dy * dy) ** 0.5

    def recent_motion_distance(self, window: int = 3) -> float:
        if len(self.bbox_history) < 2:
            return 0.0
        points = [point.footpoint for point in self.bbox_history[-max(2, window):]]
        distance = 0.0
        for idx in range(1, len(points)):
            prev = points[idx - 1]
            curr = points[idx]
            dx = curr[0] - prev[0]
            dy = curr[1] - prev[1]
            distance += (dx * dx + dy * dy) ** 0.5
        return distance

    def recent_zone_streak(self, zone_id: str) -> int:
        streak = 0
        for zone in reversed(self.zone_history):
            if zone != zone_id:
                break
            streak += 1
        return streak

    def recent_line_crossings(self, line_id: str | None = None) -> int:
        if line_id is None:
            return len(self.line_crossing_history)
        return sum(1 for item in self.line_crossing_history if item.get("line_id") == line_id)

    def recompute_quality(self, *, smoothing: float = 0.70, shadow_penalty: float = 0.0, max_motion_px: float = 120.0) -> float:
        class_score = self.class_consistency()
        visibility = _clamp01(self.visible_frames / float(max(1, self.age_frames)))
        confidence = _clamp01(self.score_avg)
        association = _clamp01(self.last_association_score)
        motion_step = self.current_motion_step()
        motion_conf = 1.0 - _clamp01(motion_step / float(max(1.0, max_motion_px)))
        motion_conf = _clamp01(motion_conf)
        self.motion_confidence = motion_conf
        self.size_confidence = _clamp01(self.size_confidence)
        self.border_confidence = _clamp01(self.border_confidence)
        self.geometry_confidence = _clamp01(
            0.60 * self.size_confidence + 0.40 * self.border_confidence
        )
        base_quality = (
            0.28 * class_score
            + 0.24 * visibility
            + 0.20 * confidence
            + 0.18 * association
            + 0.10 * motion_conf
            + 0.08 * self.geometry_confidence
        )
        base_quality = _clamp01(base_quality * (1.0 - shadow_penalty))
        if self.track_quality <= 0.0:
            self.track_quality = base_quality
        else:
            self.track_quality = _clamp01((smoothing * self.track_quality) + ((1.0 - smoothing) * base_quality))
        return self.track_quality

    def effective_quality(self) -> float:
        if self.track_quality > 0.0:
            return self.track_quality
        fallback = (
            0.38 * self.class_consistency()
            + 0.24 * _clamp01(self.age_visible_ratio)
            + 0.20 * _clamp01(self.score_avg)
            + 0.18 * _clamp01(self.geometry_confidence)
        )
        return _clamp01(fallback)

    @property
    def age_visible_ratio(self) -> float:
        if self.age_frames <= 0:
            return 0.0
        return self.visible_frames / float(self.age_frames)
