from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.timezone import utc_now_naive

from ..config.schema import AnalyticsConfig, RuleConfig, DirectionalLine, SceneZone
from ..detection.types import DetectionCandidate
from ..events.models import AlarmEvent, EventEvidence
from ..rules.pipeline import IntrusionRuleEngine
from ..rules.base import make_event_signature
from ..scene.geometry import bbox_footpoint
from ..scene.model import AnalyticsScene
from ..scene.lines import LineCrossing
from ..scene.zones import ZoneHit
from ..tracking.tracker import StatefulTracker
from ..tracking.enums import TrackState


@dataclass(slots=True)
class AnalyticsBatchResult:
    tracks: list
    events: list[AlarmEvent]
    suppressed: list[dict]


def _full_frame_zone(width: int, height: int) -> list[tuple[float, float]]:
    return [(0.0, 0.0), (float(width), 0.0), (float(width), float(height)), (0.0, float(height))]


class AnalyticsEventPipeline:
    def __init__(self, config: AnalyticsConfig | None = None):
        self.config = config or AnalyticsConfig(
            rules={
                "intrusion_default": RuleConfig(rule_id="intrusion_default", rule_type="intrusion_zone"),
            }
        )
        self.tracker = StatefulTracker(self.config.tracking)
        self.engine = IntrusionRuleEngine(self.config)
        self.scene = AnalyticsScene(
            border_margin_ratio=self.config.scene.border_margin_ratio,
            min_bbox_aspect_ratio=self.config.scene.min_bbox_aspect_ratio,
            max_bbox_aspect_ratio=self.config.scene.max_bbox_aspect_ratio,
        )
        self.active_modes: tuple[str, ...] = ()
        self.geometry_signature: tuple = ()
        self.last_result = AnalyticsBatchResult(tracks=[], events=[], suppressed=[])
        self.previous_track_ids: set[int] = set()  # Tracks seen in previous frame

    def update_geometry(
        self,
        roi_polygon,
        line_pixels,
        line_direction: str,
        human_event_modes: list[str] | None = None,
        human_loitering_seconds: float | None = None,
        human_detection_sensitivity: str | None = None,
    ):
        width = self.scene.width or 1
        height = self.scene.height or 1
        restricted_zones: list[SceneZone] = []
        if roi_polygon and len(roi_polygon) >= 3:
            restricted_zones.append(
                SceneZone(
                    zone_id="roi_1",
                    name="ROI 1",
                    polygon=[(float(x), float(y)) for x, y in roi_polygon],
                    zone_type="roi",
                    enabled=True,
                )
            )
        else:
            restricted_zones.append(
                SceneZone(
                    zone_id="scene_full",
                    name="Cena inteira",
                    polygon=_full_frame_zone(width, height),
                    zone_type="scene",
                    enabled=True,
                )
            )

        directional_lines: list[DirectionalLine] = []
        if line_pixels:
            (x1, y1), (x2, y2) = line_pixels
            directional_lines.append(
                DirectionalLine(
                    line_id="line_1",
                    name="Linha 1",
                    start=(float(x1), float(y1)),
                    end=(float(x2), float(y2)),
                    direction=line_direction or "any",
                    enabled=True,
                )
            )

        self.scene = AnalyticsScene(
            width=width,
            height=height,
            restricted_zones=restricted_zones,
            exclusion_zones=[],
            buffer_zones=[],
            directional_lines=directional_lines,
            perspective_profile=[],
            border_margin_ratio=self.config.scene.border_margin_ratio,
            min_bbox_aspect_ratio=self.config.scene.min_bbox_aspect_ratio,
            max_bbox_aspect_ratio=self.config.scene.max_bbox_aspect_ratio,
        )

        requested_modes = tuple(sorted(human_event_modes or []))
        roi_signature = tuple((float(x), float(y)) for x, y in roi_polygon) if roi_polygon else ()
        line_signature = (
            tuple(float(v) for point in line_pixels for v in point)
            if line_pixels
            else ()
        )
        current_signature = (
            roi_signature,
            line_signature,
            str(line_direction or "any"),
            requested_modes,
            float(human_loitering_seconds or 0.0) if human_loitering_seconds is not None else None,
            str(human_detection_sensitivity or "medium"),
        )
        if requested_modes != self.active_modes or current_signature != self.geometry_signature:
            self.geometry_signature = current_signature
            self.active_modes = requested_modes
            rules: dict[str, RuleConfig] = {}
            if "person_entered" in requested_modes or "person_entered_roi" in requested_modes or not requested_modes:
                rules["intrusion_default"] = RuleConfig(
                    rule_id="intrusion_default",
                    rule_type="intrusion_zone",
                    enabled=True,
                    target_class="person",
                    require_confirmed_track=True,
                    min_track_age_frames=4,
                    min_visible_frames=4,
                    min_dwell_ms=300,
                    min_event_score=0.60,
                    cooldown_seconds=5.0,  # Reduced from 10s to 5s to allow more responsive events
                    min_motion_plausibility=0.20,
                    min_motion_distance_px=2.0,
                )
            if "person_loitering" in requested_modes:
                rules["loitering_default"] = RuleConfig(
                    rule_id="loitering_default",
                    rule_type="loitering",
                    enabled=True,
                    target_class="person",
                    require_confirmed_track=True,
                    min_track_age_frames=4,
                    min_visible_frames=4,
                    min_dwell_ms=int((human_loitering_seconds or 10.0) * 1000.0),
                    min_event_score=0.60,
                    cooldown_seconds=5.0,  # Reduced from 10s to 5s
                )
            if "line_crossing" in requested_modes and line_pixels:
                rules["line_crossing_default"] = RuleConfig(
                    rule_id="line_crossing_default",
                    rule_type="line_crossing",
                    enabled=True,
                    target_class="person",
                    require_confirmed_track=True,
                    min_track_age_frames=3,
                    min_visible_frames=3,
                    min_dwell_ms=0,
                    min_event_score=0.58,
                    cooldown_seconds=3.0,  # Reduced from 8s to 3s for faster line crossing detection
                )
            if not rules:
                rules["intrusion_default"] = RuleConfig(rule_id="intrusion_default", rule_type="intrusion_zone")
            self.config = AnalyticsConfig(tracking=self.config.tracking, scene=self.scene, rules=rules, scoring=self.config.scoring)
            self.engine = IntrusionRuleEngine(self.config)

    def process(self, camera_id: int, tracks: list[dict], db, frame, motion_gate=None) -> AnalyticsBatchResult:
        now = utc_now_naive()
        if frame is not None and hasattr(frame, "shape"):
            try:
                self.scene.width = int(frame.shape[1])
                self.scene.height = int(frame.shape[0])
            except Exception:
                pass

        if self.scene.width and self.scene.height and self.scene.restricted_zones:
            first_zone = self.scene.restricted_zones[0]
            if getattr(first_zone, "zone_type", "") == "scene" and len(getattr(first_zone, "polygon", []) or []) >= 3:
                first_zone.polygon = _full_frame_zone(self.scene.width, self.scene.height)

        detections: list[DetectionCandidate] = []
        for track in tracks or []:
            bbox = track.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            detections.append(
                DetectionCandidate(
                    bbox=[float(v) for v in bbox],
                    score=float(track.get("confidence") or 0.0),
                    class_name=str(track.get("class_name") or "person"),
                    detection_id=str(track.get("track_id")) if track.get("track_id") is not None else None,
                    embedding=track.get("embedding"),
                    timestamp=now,
                    source="worker_track",
                    metadata=track,
                )
            )

        updated_tracks = self.tracker.update(detections, timestamp=now)
        self.scene.observe_tracks(updated_tracks)

        # Update track metadata with local motion features
        for track in updated_tracks:
            if not isinstance(track.metadata, dict):
                track.metadata = {}
            if "motion_history" not in track.metadata:
                track.metadata["motion_history"] = []

            if motion_gate is not None and track.bbox_current is not None:
                local_features = motion_gate.get_local_motion_features(track.bbox_current)
            else:
                local_features = {
                    "motion_area_pct": 0.0,
                    "motion_blobs": 0,
                    "has_motion_mask": False,
                }

            history_size = 20
            try:
                from app.core.config import settings
                if settings is not None:
                    history_size = getattr(settings, "motion_confirm_history_size", 20)
            except Exception:
                pass

            track.metadata["motion_history"].append(local_features)
            if len(track.metadata["motion_history"]) > history_size:
                track.metadata["motion_history"] = track.metadata["motion_history"][-history_size:]
        
        # Generate person_left events for tracks that have been terminated
        exit_events = self._generate_exit_events(camera_id, updated_tracks, now)
        
        events, suppressed = self.engine.evaluate(
            camera_id=camera_id,
            tracks=updated_tracks,
            scene=self.scene,
            tracker_metrics=self.tracker.metrics,
            now=now,
        )
        
        # Combine entry events from rules with exit events
        all_events = events + exit_events
        
        # Update previous track IDs for next frame
        self.previous_track_ids = {track.track_id for track in updated_tracks if track.state != TrackState.TERMINATED}
        
        self.last_result = AnalyticsBatchResult(tracks=updated_tracks, events=all_events, suppressed=suppressed)
        return self.last_result
    
    def _generate_exit_events(self, camera_id: int, current_tracks: list, now: datetime) -> list[AlarmEvent]:
        """Generate person_left/person_left_roi events for tracks that have been terminated."""
        exit_events: list[AlarmEvent] = []
        
        # Check if person_left events are requested
        if "person_left" not in self.active_modes and "person_left_roi" not in self.active_modes:
            return exit_events
        
        # Find current active track IDs
        current_track_ids = {track.track_id for track in current_tracks if track.state != TrackState.TERMINATED}
        
        # Find terminated tracks (were visible, now terminated)
        for track in current_tracks:
            if track.track_id not in self.previous_track_ids:
                continue  # Track wasn't seen before
            if track.state != TrackState.TERMINATED:
                continue  # Track is still active
            if not getattr(track, "event_emitted", False):
                continue  # No entry event was emitted, so don't emit exit event
            
            # Determine zone type for person_left vs person_left_roi
            if self.scene.restricted_zones and len(self.scene.restricted_zones) > 0:
                first_zone = self.scene.restricted_zones[0]
                zone_type = getattr(first_zone, "zone_type", "scene")
                event_type = "person_left_roi" if zone_type == "roi" else "person_left"
                zone_id = first_zone.zone_id if zone_type == "roi" else None
                zone_name = first_zone.name if zone_type == "roi" else None
            else:
                event_type = "person_left"
                zone_id = None
                zone_name = None
            
            # Create signature for deduplication
            signature = make_event_signature(
                camera_id=camera_id,
                rule_id="exit_rule_default",
                track_id=track.track_id,
                event_type=event_type,
                zone_id=zone_id,
            )
            
            # Create person_left event
            exit_event = AlarmEvent(
                event_id=signature,
                camera_id=camera_id,
                rule_id="exit_rule_default",
                track_id=track.track_id,
                timestamp_start=track.first_seen or now,
                timestamp_end=now,
                event_score=0.9,  # High confidence for tracked exits
                priority="medium",
                event_type=event_type,
                evidence=EventEvidence(
                    bbox=list(track.bbox_current or []),
                    trajectory=[point.footpoint for point in track.bbox_history[-10:]],
                    zone_id=zone_id,
                    footpoint=track.footpoint_current,
                    reason="track_terminated_exit",
                ),
                metadata={
                    "zone_id": zone_id,
                    "zone_name": zone_name,
                    "track_lifetime_seconds": (track.last_seen - track.first_seen).total_seconds() if track.last_seen and track.first_seen else 0,
                    "visible_frames": track.visible_frames,
                },
            )
            exit_events.append(exit_event)
        
        return exit_events
