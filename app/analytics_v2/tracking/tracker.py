from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from app.core.timezone import utc_now_naive

from ..config.schema import TrackingConfig
from ..detection.types import DetectionCandidate
from ..scene.geometry import bbox_aspect_ratio, bbox_footpoint
from ..scoring.components import track_quality_score
from .association import MultiStageAssociator
from .enums import TrackState
from .types import Track, TrackHistoryPoint


@dataclass(slots=True)
class TrackerMetrics:
    candidate_tracks_created: int = 0
    confirmed_tracks_created: int = 0
    terminated_tracks: int = 0
    shadow_recovered_tracks: int = 0
    id_switch_estimate: int = 0
    state_transitions: Counter = field(default_factory=Counter)


class StatefulTracker:
    def __init__(self, config: TrackingConfig | None = None):
        self.config = config or TrackingConfig()
        self.associator = MultiStageAssociator()
        self.tracks: dict[int, Track] = {}
        self._next_track_id = 1
        self.metrics = TrackerMetrics()

    def update(self, detections: list[DetectionCandidate], timestamp: datetime | None = None) -> list[Track]:
        now = timestamp or utc_now_naive()
        detections = [det for det in detections if float(det.score) >= float(self.config.det_threshold_candidate)]

        for track in self.tracks.values():
            track.age_frames += 1
            track.metadata["last_update_timestamp"] = now.isoformat()
            track.recompute_quality(
                smoothing=self.config.track_quality_smoothing,
                max_motion_px=self.config.max_match_distance_px,
                shadow_penalty=0.0 if track.state != TrackState.SHADOW else 0.15,
            )

        active_tracks = [track for track in self.tracks.values() if track.state != TrackState.TERMINATED]
        matches = self.associator.match(
            active_tracks,
            detections,
            iou_threshold=self.config.iou_match_threshold,
            reid_threshold=self.config.reid_similarity_threshold,
            max_distance_px=self.config.max_match_distance_px,
            weak_distance_px=self.config.max_shadow_recovery_distance_px,
        )

        matched_track_ids: set[int] = set()
        matched_det_indices: set[int] = set()
        for match in matches:
            track = self.tracks.get(match.track_id)
            det = detections[match.detection_index]
            if not track or track.state == TrackState.TERMINATED:
                continue

            matched_track_ids.add(track.track_id)
            matched_det_indices.add(match.detection_index)
            self._apply_detection(track, det, now, association_score=match.score, association_method=match.method)

        created_track_ids: set[int] = set()
        for det_index, det in enumerate(detections):
            if det_index in matched_det_indices:
                continue
            created_track_ids.add(self._create_track_from_detection(det, now).track_id)

        for track in list(self.tracks.values()):
            if track.track_id in matched_track_ids or track.track_id in created_track_ids or track.state == TrackState.TERMINATED:
                continue
            self._handle_unmatched_track(track, now)

        return [track for track in self.tracks.values() if track.state != TrackState.TERMINATED]

    def _create_track_from_detection(self, det: DetectionCandidate, now: datetime) -> Track:
        track = Track(
            track_id=self._next_track_id,
            state=TrackState.NEW_CANDIDATE,
            first_seen=now,
            last_seen=now,
            dominant_class=det.class_name,
            last_detection_class=det.class_name,
            score_avg=float(det.score),
            internal_score=float(det.score),
            candidate_frames=1,
            visible_frames=1,
            age_frames=1,
            last_detection_score=float(det.score),
            embedding_latest=det.embedding,
            metadata=dict(det.metadata or {}),
        )
        footpoint = bbox_footpoint(det.bbox)
        geometry_hint = self._detection_geometry_hint(det.bbox)
        track.kalman.initialize(footpoint.x, footpoint.y)
        track.append_history(
            TrackHistoryPoint(
                timestamp=now,
                bbox=list(det.bbox),
                footpoint=(footpoint.x, footpoint.y),
                score=float(det.score),
                class_name=det.class_name,
            ),
            max_history=self.config.max_track_history,
        )
        track.add_vote(det.class_name)
        track.geometry_confidence = geometry_hint
        track.size_confidence = geometry_hint
        track.border_confidence = 0.9
        self.tracks[track.track_id] = track
        self._next_track_id += 1
        self.metrics.candidate_tracks_created += 1
        track.track_quality = track_quality_score(track, self.config)
        return track

    def _apply_detection(self, track: Track, det: DetectionCandidate, now: datetime, *, association_score: float, association_method: str) -> None:
        footpoint = bbox_footpoint(det.bbox)
        prediction = track.kalman.update(footpoint.x, footpoint.y)
        vx, vy = prediction.velocity

        track.last_seen = now
        track.visible_frames += 1
        track.lost_frames = 0
        track.candidate_frames = track.candidate_frames + 1 if track.state == TrackState.NEW_CANDIDATE else track.candidate_frames
        track.probation_frames = track.probation_frames + 1 if track.state == TrackState.PROBATION else track.probation_frames
        track.shadow_frames = 0
        track.bbox_current = list(det.bbox)
        track.footpoint_current = (footpoint.x, footpoint.y)
        track.last_association_score = float(association_score)
        track.last_detection_score = float(det.score)
        track.last_detection_class = det.class_name
        track.embedding_latest = det.embedding
        track.add_vote(det.class_name)
        track.score_avg = ((track.score_avg * (track.visible_frames - 1)) + float(det.score)) / float(track.visible_frames)
        track.internal_score = max(track.internal_score, float(association_score))
        track.velocity_estimate = (vx, vy)
        track.direction_estimate = self._estimate_direction(track)
        geometry_hint = self._detection_geometry_hint(det.bbox)
        track.geometry_confidence = max(track.geometry_confidence, geometry_hint)
        track.size_confidence = max(track.size_confidence, geometry_hint)
        track.border_confidence = max(track.border_confidence, 0.9)
        track.recompute_quality(
            smoothing=self.config.track_quality_smoothing,
            max_motion_px=self.config.max_match_distance_px,
            shadow_penalty=0.0 if track.state != TrackState.SHADOW else 0.15,
        )
        track.append_history(
            TrackHistoryPoint(
                timestamp=now,
                bbox=list(det.bbox),
                footpoint=(footpoint.x, footpoint.y),
                score=float(det.score),
                class_name=det.class_name,
            ),
            max_history=self.config.max_track_history,
        )
        recent_motion = track.recent_motion_distance(window=min(4, len(track.bbox_history)))
        track.metadata["recent_motion_distance_px"] = recent_motion

        if track.state == TrackState.NEW_CANDIDATE and track.visible_frames >= 2:
            self._transition(track, TrackState.NEW_CANDIDATE, TrackState.PROBATION)
        if (
            track.state == TrackState.PROBATION
            and track.visible_frames >= self.config.min_frames_to_confirm
            and track.score_avg >= self.config.det_threshold_confirm
            and track.track_quality >= self.config.track_quality_min_confirm
            and track.class_consistency() >= 0.5
            and (
                recent_motion >= self.config.min_motion_px_for_confirm
                or track.geometry_confidence >= 0.45
                or track.size_confidence >= 0.55
            )
        ):
            self._transition(track, TrackState.PROBATION, TrackState.CONFIRMED)
            track.confirmed_at = now
            self.metrics.confirmed_tracks_created += 1
        if track.state == TrackState.SHADOW:
            self._transition(track, TrackState.SHADOW, TrackState.CONFIRMED)
            track.confirmed_at = now if track.confirmed_at is None else track.confirmed_at
            self.metrics.shadow_recovered_tracks += 1

        track.metadata["last_association_method"] = association_method
        track.metadata["last_update_timestamp"] = now.isoformat()
        track.metadata["velocity"] = {"vx": vx, "vy": vy}

    def _detection_geometry_hint(self, bbox: list[float]) -> float:
        aspect = bbox_aspect_ratio(bbox)
        if aspect <= 0.0:
            return 0.0
        if 0.22 <= aspect <= 1.40:
            return 0.72
        if 0.15 <= aspect <= 1.8:
            return 0.50
        return 0.22
    def _handle_unmatched_track(self, track: Track, now: datetime) -> None:
        track.lost_frames += 1

        if track.state in {TrackState.NEW_CANDIDATE, TrackState.PROBATION}:
            if track.lost_frames > self.config.probation_max_lost_frames:
                self._terminate(track)
            return

        if track.state == TrackState.CONFIRMED:
            self._transition(track, TrackState.CONFIRMED, TrackState.SHADOW)
            track.shadow_started_at = now
            track.lost_frames = 1
            track.shadow_frames = 1
            track.recompute_quality(
                smoothing=self.config.track_quality_smoothing,
                max_motion_px=self.config.max_match_distance_px,
                shadow_penalty=0.15,
            )
            return

        if track.state == TrackState.SHADOW:
            track.shadow_frames += 1
            track.recompute_quality(
                smoothing=self.config.track_quality_smoothing,
                max_motion_px=self.config.max_match_distance_px,
                shadow_penalty=min(0.5, 0.05 * track.shadow_frames),
            )
        if track.state == TrackState.SHADOW and track.lost_frames > self.config.max_shadow_age_frames:
            self._terminate(track)

    def _terminate(self, track: Track) -> None:
        if track.state != TrackState.TERMINATED:
            self._transition(track, track.state, TrackState.TERMINATED)
            self.metrics.terminated_tracks += 1

    def _transition(self, track: Track, from_state: TrackState, to_state: TrackState) -> None:
        track.state = to_state
        track.state_change_count += 1
        self.metrics.state_transitions[(from_state.value, to_state.value)] += 1

    def _estimate_direction(self, track: Track) -> str | None:
        if len(track.bbox_history) < 2:
            return None
        start = track.bbox_history[0].footpoint
        end = track.bbox_history[-1].footpoint
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        if abs(dx) < 1.0 and abs(dy) < 1.0:
            return "static"
        if abs(dx) >= abs(dy):
            return "left_to_right" if dx > 0 else "right_to_left"
        return "top_to_bottom" if dy > 0 else "bottom_to_top"
