from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config.schema import RuleConfig
from ..events.debug import event_rule_debug
from ..events.models import EventEvidence
from ..scene.geometry import normalize_footpoint
from ..scoring.components import motion_plausibility
from ..tracking.enums import TrackState
from ..tracking.types import Track
from .base import RuleContext, RuleResult


def _safe_value(value: Any) -> Any:
    try:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
    except Exception:
        return None


@dataclass(slots=True)
class IntrusionZoneRule:
    config: RuleConfig

    def _emit_rejected(
        self,
        track: Track,
        context: RuleContext,
        reason: str,
        *,
        score: float | None = None,
        observation: Any = None,
        zone_hit: Any = None,
        full_frame_mode: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload = self._debug_payload(
            track,
            context,
            observation=observation,
            zone_hit=zone_hit,
            full_frame_mode=full_frame_mode,
        )

        if score is not None:
            payload["score"] = score

        if extra:
            payload.update(extra)

        event_rule_debug.emit(
            "rule_rejected",
            camera_id=getattr(context, "camera_id", None),
            track_id=getattr(track, "track_id", None),
            reason=reason,
            rate_key=f"{self.config.rule_id}:{reason}",
            **payload,
        )

    def _emit_candidate(
        self,
        track: Track,
        context: RuleContext,
        *,
        event_type: str,
        score: float,
        observation: Any,
        zone_hit: Any,
        full_frame_mode: bool,
        dwell_ms: float,
        effective_quality: float,
        zone_streak: int,
    ) -> None:
        payload = self._debug_payload(
            track,
            context,
            observation=observation,
            zone_hit=zone_hit,
            full_frame_mode=full_frame_mode,
        )
        payload.update(
            {
                "event_type": event_type,
                "score": score,
                "min_event_score": self.config.min_event_score,
                "dwell_ms": dwell_ms,
                "min_dwell_ms": self.config.min_dwell_ms,
                "track_quality": effective_quality,
                "zone_streak": zone_streak,
                "min_zone_persistence_frames": self.config.min_zone_persistence_frames,
            }
        )

        event_rule_debug.emit(
            "event_candidate_created",
            camera_id=getattr(context, "camera_id", None),
            track_id=getattr(track, "track_id", None),
            reason="confirmed_intrusion",
            rate_key=f"{self.config.rule_id}:event_candidate_created",
            **payload,
        )

    def _debug_payload(
        self,
        track: Track,
        context: RuleContext,
        *,
        observation: Any = None,
        zone_hit: Any = None,
        full_frame_mode: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rule_id": self.config.rule_id,
            "rule_type": "intrusion_zone",
            "target_class": getattr(self.config, "target_class", None),
            "require_confirmed_track": getattr(self.config, "require_confirmed_track", None),
            "full_frame_mode": bool(full_frame_mode),
            "track_state": _safe_value(getattr(track, "state", None)),
            "track_age_frames": getattr(track, "age_frames", None),
            "visible_frames": getattr(track, "visible_frames", None),
            "min_track_age_frames": getattr(self.config, "min_track_age_frames", None),
            "min_visible_frames": getattr(self.config, "min_visible_frames", None),
            "min_track_quality": getattr(self.config, "min_track_quality", None),
            "min_class_consistency": getattr(self.config, "min_class_consistency", None),
            "min_geometry_confidence": getattr(self.config, "min_geometry_confidence", None),
            "min_event_score": getattr(self.config, "min_event_score", None),
            "min_dwell_ms": getattr(self.config, "min_dwell_ms", None),
            "min_zone_persistence_frames": getattr(self.config, "min_zone_persistence_frames", None),
            "bbox_current": list(getattr(track, "bbox_current", []) or []),
            "geometry_confidence": getattr(track, "geometry_confidence", None),
            "class_consistency": None,
            "track_quality": None,
        }

        try:
            payload["track_quality"] = track.effective_quality()
        except Exception:
            payload["track_quality"] = None

        try:
            payload["class_consistency"] = track.class_consistency()
        except Exception:
            payload["class_consistency"] = None

        if observation is not None:
            restricted_zone = getattr(observation, "restricted_zone", None)
            exclusion_zone = getattr(observation, "exclusion_zone", None)
            buffer_zone = getattr(observation, "buffer_zone", None)

            payload.update(
                {
                    "footpoint": _safe_value(getattr(observation, "footpoint", None)),
                    "near_border": getattr(observation, "near_border", None),
                    "border_score": getattr(observation, "border_score", None),
                    "aspect_ratio": getattr(observation, "aspect_ratio", None),
                    "restricted_zone_id": getattr(restricted_zone, "zone_id", None),
                    "restricted_zone_type": getattr(restricted_zone, "zone_type", None),
                    "exclusion_zone_id": getattr(exclusion_zone, "zone_id", None),
                    "buffer_zone_id": getattr(buffer_zone, "zone_id", None),
                }
            )

        if zone_hit is not None:
            payload.update(
                {
                    "zone_hit": True,
                    "zone_id": getattr(zone_hit, "zone_id", None),
                    "zone_name": getattr(zone_hit, "name", None),
                    "zone_type": getattr(zone_hit, "zone_type", None),
                    "zone_confidence": getattr(zone_hit, "confidence", None),
                }
            )
        else:
            payload["zone_hit"] = False

        return payload

    def evaluate(self, track: Track, context: RuleContext) -> RuleResult:
        if not self.config.enabled:
            self._emit_rejected(track, context, "rejected_rule_disabled")
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "disabled")

        if self.config.require_confirmed_track and track.state != TrackState.CONFIRMED:
            self._emit_rejected(track, context, "rejected_unconfirmed_track")
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "track_not_confirmed")

        if track.age_frames < self.config.min_track_age_frames:
            self._emit_rejected(
                track,
                context,
                "rejected_track_age",
                extra={
                    "track_age_frames": track.age_frames,
                    "min_track_age_frames": self.config.min_track_age_frames,
                },
            )
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "track_too_young")

        if track.visible_frames < self.config.min_visible_frames:
            self._emit_rejected(
                track,
                context,
                "rejected_visible_frames",
                extra={
                    "visible_frames": track.visible_frames,
                    "min_visible_frames": self.config.min_visible_frames,
                },
            )
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "not_enough_visibility")

        if not track.bbox_current:
            self._emit_rejected(track, context, "rejected_missing_bbox")
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "missing_bbox")

        effective_quality = track.effective_quality()
        if effective_quality < self.config.min_track_quality:
            self._emit_rejected(
                track,
                context,
                "rejected_track_quality",
                score=effective_quality,
                extra={
                    "track_quality": effective_quality,
                    "min_track_quality": self.config.min_track_quality,
                },
            )
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "track_quality_too_low", score=effective_quality)

        class_consistency = track.class_consistency()
        if class_consistency < self.config.min_class_consistency:
            self._emit_rejected(
                track,
                context,
                "rejected_class_consistency",
                extra={
                    "class_consistency": class_consistency,
                    "min_class_consistency": self.config.min_class_consistency,
                },
            )
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "class_consistency_too_low")

        observation = self._scene_observation(track, context)
        zone_hit = getattr(observation, "restricted_zone", None)
        has_restricted_zone_config = self._scene_has_restricted_zone_config(getattr(context, "scene", None))
        full_frame_mode = not has_restricted_zone_config

        # A camera may prefer ROI/ignore zones, but blocking every full-frame
        # event made legitimate alarms disappear on cameras not yet zoned.
        # Keep these flags as policy metadata and let border/motion/revalidator
        # checks handle the risky cases.
        if getattr(observation, "exclusion_zone", None) is not None and (
            not self.config.exclusion_zones or observation.exclusion_zone.zone_id in self.config.exclusion_zones
        ):
            self._emit_rejected(
                track,
                context,
                "rejected_inside_exclusion_zone",
                observation=observation,
                zone_hit=zone_hit,
                full_frame_mode=full_frame_mode,
                extra={
                    "exclusion_zone_id": getattr(observation.exclusion_zone, "zone_id", None),
                    "exclusion_zone_name": getattr(observation.exclusion_zone, "name", None),
                },
            )
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "inside_exclusion_zone")

        if observation.aspect_ratio > 0.0 and (
            observation.aspect_ratio < self.config.min_aspect_ratio
            or observation.aspect_ratio > self.config.max_aspect_ratio
        ):
            self._emit_rejected(
                track,
                context,
                "rejected_aspect_ratio",
                observation=observation,
                zone_hit=zone_hit,
                full_frame_mode=full_frame_mode,
                extra={
                    "aspect_ratio": observation.aspect_ratio,
                    "min_aspect_ratio": self.config.min_aspect_ratio,
                    "max_aspect_ratio": self.config.max_aspect_ratio,
                },
            )
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "aspect_ratio_out_of_range")

        # Se existe ROI/zona configurada e o track não está dentro dela,
        # a rejeição correta é zona, não borda.
        if has_restricted_zone_config and zone_hit is None:
            self._emit_rejected(
                track,
                context,
                "rejected_not_inside_roi",
                observation=observation,
                zone_hit=zone_hit,
                full_frame_mode=full_frame_mode,
            )
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "outside_restricted_zone")

        # Full-frame still blocks side/top border artifacts, but keeps the
        # bottom-cut exception for a person entering close to the camera.
        bottom_cut_full_frame = full_frame_mode and self._is_bottom_cut(track, context, observation)

        if self.config.block_near_border and observation.near_border and zone_hit is None and not bottom_cut_full_frame:
            recent_motion = track.recent_motion_distance(window=min(4, len(track.bbox_history)))
            if recent_motion < self.config.min_motion_distance_px or observation.border_score < (
                1.0 - self.config.max_border_penalty
            ):
                self._emit_rejected(
                    track,
                    context,
                    "rejected_border",
                    observation=observation,
                    zone_hit=zone_hit,
                    full_frame_mode=full_frame_mode,
                    extra={
                        "recent_motion": recent_motion,
                        "min_motion_distance_px": self.config.min_motion_distance_px,
                        "border_score": observation.border_score,
                        "max_border_penalty": self.config.max_border_penalty,
                    },
                )
                return RuleResult(False, self.config.rule_id, "intrusion_zone", "near_border_low_motion")

        # Low geometry is acceptable only for that bottom-cut full-frame case.
        if track.geometry_confidence < self.config.min_geometry_confidence and not bottom_cut_full_frame:
            self._emit_rejected(
                track,
                context,
                "rejected_geometry",
                observation=observation,
                zone_hit=zone_hit,
                full_frame_mode=full_frame_mode,
                score=track.geometry_confidence,
                extra={
                    "geometry_confidence": track.geometry_confidence,
                    "min_geometry_confidence": self.config.min_geometry_confidence,
                },
            )
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "geometry_confidence_too_low", score=track.geometry_confidence)

        recent_motion = track.recent_motion_distance(window=min(4, len(track.bbox_history)))
        motion_score = motion_plausibility(track)
        motion_required = observation.near_border or track.geometry_confidence < (
            self.config.min_geometry_confidence + 0.10
        )
        if motion_required and (
            recent_motion < self.config.min_motion_distance_px
            or motion_score < self.config.min_motion_plausibility
        ):
            self._emit_rejected(
                track,
                context,
                "rejected_motion",
                observation=observation,
                zone_hit=zone_hit,
                full_frame_mode=full_frame_mode,
                score=motion_score,
                extra={
                    "recent_motion": recent_motion,
                    "min_motion_distance_px": self.config.min_motion_distance_px,
                    "motion_plausibility": motion_score,
                    "min_motion_plausibility": self.config.min_motion_plausibility,
                },
            )
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "motion_too_low", score=motion_score)

        dwell_reference = track.confirmed_at or track.first_seen
        dwell_ms = 0.0
        if dwell_reference and track.last_seen:
            dwell_ms = (track.last_seen - dwell_reference).total_seconds() * 1000.0

        if dwell_ms < self.config.min_dwell_ms:
            self._emit_rejected(
                track,
                context,
                "rejected_dwell",
                observation=observation,
                zone_hit=zone_hit,
                full_frame_mode=full_frame_mode,
                extra={
                    "dwell_ms": dwell_ms,
                    "min_dwell_ms": self.config.min_dwell_ms,
                },
            )
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "dwell_too_short")

        if full_frame_mode:
            zone_streak = int(getattr(track, "visible_frames", 0) or 0)
        else:
            zone_streak = track.recent_zone_streak(zone_hit.zone_id)

        if zone_streak < self.config.min_zone_persistence_frames:
            self._emit_rejected(
                track,
                context,
                "rejected_zone_persistence",
                observation=observation,
                zone_hit=zone_hit,
                full_frame_mode=full_frame_mode,
                extra={
                    "zone_streak": zone_streak,
                    "min_zone_persistence_frames": self.config.min_zone_persistence_frames,
                },
            )
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "zone_persistence_too_short")

        score = self._compose_score(
            track=track,
            context=context,
            observation=observation,
            zone_hit=zone_hit,
            full_frame_mode=full_frame_mode,
            effective_quality=effective_quality,
        )

        if score < self.config.min_event_score:
            self._emit_rejected(
                track,
                context,
                "rejected_score",
                observation=observation,
                zone_hit=zone_hit,
                full_frame_mode=full_frame_mode,
                score=score,
                extra={
                    "event_score": score,
                    "min_event_score": self.config.min_event_score,
                },
            )
            return RuleResult(False, self.config.rule_id, "intrusion_zone", "event_score_below_threshold", score=score)

        if full_frame_mode:
            event_type = "person_entered"
            zone_id = None
            zone_name = None
            zone_type = "full_frame"
            evidence_reason = "confirmed_track_in_full_frame_intrusion"
        else:
            event_type = "person_entered_roi" if zone_hit.zone_type == "roi" else "person_entered"
            zone_id = zone_hit.zone_id
            zone_name = zone_hit.name
            zone_type = zone_hit.zone_type
            evidence_reason = "confirmed_track_inside_restricted_zone"

        self._emit_candidate(
            track,
            context,
            event_type=event_type,
            score=score,
            observation=observation,
            zone_hit=zone_hit,
            full_frame_mode=full_frame_mode,
            dwell_ms=dwell_ms,
            effective_quality=effective_quality,
            zone_streak=zone_streak,
        )

        return RuleResult(
            True,
            self.config.rule_id,
            event_type,
            "confirmed_intrusion",
            score=score,
            priority=self._priority(score),
            evidence=EventEvidence(
                bbox=list(track.bbox_current),
                trajectory=[point.footpoint for point in track.bbox_history[-10:]],
                zone_id=zone_id,
                footpoint=observation.footpoint,
                reason=evidence_reason,
            ),
            metadata={
                "zone_id": zone_id,
                "zone_name": zone_name,
                "zone_type": zone_type,
                "full_frame_mode": full_frame_mode,
                "dwell_ms": dwell_ms,
                "track_quality": effective_quality,
                "zone_streak": zone_streak,
                "geometry_confidence": getattr(track, "geometry_confidence", None),
                "near_border": getattr(observation, "near_border", None),
                "border_score": getattr(observation, "border_score", None),
            },
        )

    def _compose_score(
        self,
        *,
        track: Track,
        context: RuleContext,
        observation: Any,
        zone_hit: Any,
        full_frame_mode: bool,
        effective_quality: float,
    ) -> float:
        if not full_frame_mode:
            return context.scoring.compose(track=track, context=context, zone=zone_hit, line=None, observation=observation)

        try:
            score = context.scoring.compose(track=track, context=context, zone=None, line=None, observation=observation)
            if isinstance(score, (int, float)):
                return float(score)
        except Exception:
            pass

        # Fallback seguro para modo full-frame:
        # usa qualidade da track, mas nunca abaixo do mínimo configurado.
        return max(float(self.config.min_event_score), min(1.0, float(effective_quality)))

    def _scene_has_restricted_zone_config(self, scene: Any) -> bool:
        if scene is None:
            return False

        try:
            first_zone = scene.first_restricted_zone()
            if first_zone is not None:
                return True
        except Exception:
            pass

        for attr_name in ("restricted_zones", "zones"):
            try:
                value = getattr(scene, attr_name, None)
            except Exception:
                value = None

            if isinstance(value, dict):
                iterable = value.values()
            elif isinstance(value, (list, tuple, set)):
                iterable = value
            else:
                continue

            for item in iterable:
                if item is None:
                    continue
                zone_type = getattr(item, "zone_type", None)
                enabled = getattr(item, "enabled", True)
                if enabled and zone_type in {"roi", "restricted", "restricted_zone", None}:
                    return True

        return False

    def _is_bottom_cut(self, track: Track, context: RuleContext, observation: Any) -> bool:
        bbox = list(getattr(track, "bbox_current", []) or [])
        scene = getattr(context, "scene", None)
        height = float(getattr(scene, "height", 0.0) or 0.0)
        width = float(getattr(scene, "width", 0.0) or 0.0)
        if len(bbox) < 4 or height <= 0.0 or width <= 0.0:
            return False

        x1, _, x2, y2 = [float(value) for value in bbox[:4]]
        footpoint = normalize_footpoint(getattr(observation, "footpoint", None))
        near_bottom = (height - max(y2, footpoint[1])) <= max(2.0, height * 0.035)
        away_from_sides = x1 > width * 0.035 and x2 < width * 0.965
        return bool(near_bottom and away_from_sides)

    def _scene_observation(self, track: Track, context: RuleContext):
        observation = track.metadata.get("scene_observation")
        if observation is not None:
            return observation
        return context.scene.observe_track(track)

    def _priority(self, score: float) -> str:
        if score >= 0.85:
            return "critical"
        if score >= 0.70:
            return "high"
        if score >= 0.55:
            return "medium"
        return "low"
