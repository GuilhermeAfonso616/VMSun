"""Ponte entre o pipeline stateful de analytics e a persistencia de eventos.

Este modulo mantem a assinatura usada pelo worker atual, mas por baixo ja
consome a nova arquitetura com tracker stateful, regras configuraveis,
score composto, histerese e deduplicacao.
"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import json

from app.analytics.camera_profile_models import CameraAnalyticProfile
from app.analytics.camera_policy_builder import build_analytics_config_from_profile
from app.analytics_v2 import AnalyticsEventPipeline
from app.analytics_v2.events.alarm_session import AlarmSessionPolicy
from app.analytics_v2.revalidation import load_anti_fp_patterns
from app.core.config import settings
from app.core.logging import get_event_rules_debug_logger, get_logger
from app.core.timezone import utc_now_naive
from app.runtime.event_alarm_policy import (
    apply_consensus_block_policy,
    apply_visual_quality_alarm_gate,
    assess_event_alarm,
)
from app.runtime.event_clip_buffer import EventClipPersistenceBuffer
from app.runtime.event_evidence import EventEvidencePreparer
from app.runtime.event_revalidation import EventRevalidationCoordinator
from app.services.event_persistence import EventPersistenceQueue, PendingEventWrite


@dataclass(slots=True)
class EventBatchResult:
    generated_events: list[dict]
    persisted_count: int


class EventPipeline:
    def __init__(self, persistence_queue: EventPersistenceQueue | None = None):
        self.pipeline = AnalyticsEventPipeline()
        self.logger = get_logger("app.runtime.events")
        self.debug_logger = get_event_rules_debug_logger()
        self._clip_persistence = EventClipPersistenceBuffer(persistence_queue, self.logger)
        self._evidence_preparer = EventEvidencePreparer(self._clip_persistence)
        self._revalidation_coordinator = EventRevalidationCoordinator(
            self.logger,
            self.debug_logger,
        )
        self._policy_signature = None
        self._visual_revalidated_tracks: dict[str, dict] = {}
        self.alarm_sessions = AlarmSessionPolicy(
            enabled=settings.alarm_session_enabled,
            same_scope_cooldown_seconds=settings.alarm_session_same_scope_cooldown_seconds,
            active_extend_seconds=settings.alarm_session_active_extend_seconds,
            rearm_clear_seconds=settings.alarm_session_rearm_clear_seconds,
            new_track_renotify_enabled=settings.alarm_session_new_track_renotify_enabled,
            new_track_cooldown_seconds=settings.alarm_session_new_track_cooldown_seconds,
            reminder_interval_seconds=settings.alarm_session_reminder_interval_seconds,
            escalation_score_delta=settings.alarm_session_escalation_score_delta,
        )

    def update_geometry(
        self,
        roi_polygon,
        line_pixels,
        line_direction: str,
        human_event_modes: list[str] | None = None,
        human_loitering_seconds: float | None = None,
        human_detection_sensitivity: str | None = None,
        *,
        camera_profile: CameraAnalyticProfile | None = None,
        frame_width: int | None = None,
        frame_height: int | None = None,
    ):
        profile = camera_profile
        if profile is None:
            from app.analytics.camera_profile_models import build_camera_analytic_profile

            profile = build_camera_analytic_profile(
                preset_name="legacy_default",
                camera_family="dome",
                scene_profile="indoor_discreet",
                analytic_goal="intrusion",
                threshold_profile={
                    "person_confidence_min": 0.45,
                    "track_persistence_frames": 3,
                    "alarm_confirmation_seconds": float(human_loitering_seconds or 10.0),
                    "dwell_seconds": float(human_loitering_seconds or 10.0),
                    "roi_required": bool(roi_polygon and len(roi_polygon) >= 3),
                    "full_frame_forbidden": False,
                },
                roi_polygon=[{"x": float(x), "y": float(y)} for x, y in (roi_polygon or [])] if roi_polygon else [],
                directional_lines=[
                    {
                        "line_id": "line_1",
                        "name": "Linha 1",
                        "start": [float(line_pixels[0][0]), float(line_pixels[0][1])] if line_pixels else [0.0, 0.0],
                        "end": [float(line_pixels[1][0]), float(line_pixels[1][1])] if line_pixels else [1.0, 1.0],
                        "direction": line_direction or "any",
                        "enabled": bool(line_pixels),
                    }
                ] if line_pixels else [],
                manual_overrides={
                    "human_event_modes": human_event_modes or [],
                    "human_loitering_seconds": human_loitering_seconds,
                    "human_detection_sensitivity": human_detection_sensitivity,
                },
            )

        width = int(frame_width or 1920)
        height = int(frame_height or 1080)
        config, derived_policy = build_analytics_config_from_profile(profile, frame_width=width, frame_height=height)
        signature = (
            profile.preset_name,
            profile.camera_family,
            profile.scene_profile,
            profile.analytic_goal,
            profile.risk_profile,
            tuple(sorted(derived_policy.preview.get("nuisance_flags", []))),
            json.dumps(derived_policy.preview.get("thresholds", {}), sort_keys=True, ensure_ascii=False),
            json.dumps(derived_policy.preview.get("scene_counts", {}), sort_keys=True, ensure_ascii=False),
            json.dumps(derived_policy.rule_plan, sort_keys=True, ensure_ascii=False),
            width,
            height,
            tuple(tuple(point) for point in (roi_polygon or [])),
            tuple(tuple(point) for point in (line_pixels or [])) if line_pixels else (),
            line_direction,
            tuple(sorted(human_event_modes or [])),
            human_loitering_seconds,
            human_detection_sensitivity,
        )

        if signature != self._policy_signature:
            self._policy_signature = signature
            self._camera_profile = profile
            self._policy_preview = derived_policy.preview
            self._policy_rule_plan = derived_policy.rule_plan
            self.pipeline = AnalyticsEventPipeline(config=config)

    @staticmethod
    def _mark_revalidator_canceled(event, *, source: str, reason: str) -> None:
        event.metadata["revalidator_canceled"] = True
        event.metadata["revalidator_cancel_source"] = source
        event.metadata["revalidator_cancel_reason"] = reason
        event.metadata["status"] = "canceled"
        event.metadata["final_status"] = "canceled"
        event.metadata["lifecycle_action"] = "canceled"
        event.metadata["alarm_eligible"] = False
        event.metadata["is_alarm_active"] = False
        event.metadata["alarm_category"] = event.rule_id

    @staticmethod
    def _alarm_decision_applies(alarm_decision: dict) -> bool:
        if bool(alarm_decision.get("applied")):
            return True
        if bool(alarm_decision.get("current_behavior_preserved", True)):
            return False
        mode = str(alarm_decision.get("mode") or "audit").strip().lower()
        return mode not in {"", "audit", "shadow"}

    def _record_alarm_decision(self, event, alarm_decision: dict) -> None:
        event.metadata["alarm_decision"] = alarm_decision
        event.metadata["alarm_recommendation"] = {
            "action": alarm_decision.get("action"),
            "suggested_status": alarm_decision.get("suggested_status"),
            "suggested_alarm_eligible": alarm_decision.get("suggested_alarm_eligible"),
            "suggested_is_alarm_active": alarm_decision.get("suggested_is_alarm_active"),
            "reason": alarm_decision.get("reason"),
            "mode": alarm_decision.get("mode"),
            "applied": self._alarm_decision_applies(alarm_decision),
        }
        if not self._alarm_decision_applies(alarm_decision):
            return

        event.metadata["alarm_eligible"] = bool(alarm_decision.get("suggested_alarm_eligible", True))
        event.metadata["is_alarm_active"] = bool(alarm_decision.get("suggested_is_alarm_active", False))
        event.metadata["status"] = str(alarm_decision.get("suggested_status", "audit"))
        event.metadata["final_status"] = str(alarm_decision.get("suggested_status", "audit"))
        event.metadata["lifecycle_action"] = str(alarm_decision.get("action", "AUDIT")).lower()

    def _apply_alarm_session_policy(self, event) -> dict:
        current_active = bool(event.metadata.get("is_alarm_active", True))
        lifecycle = self.alarm_sessions.evaluate(event, now=event.timestamp_end)
        event.metadata["alarm_session"] = lifecycle
        event.metadata["alarm_session_id"] = lifecycle.get("session_id")
        event.metadata["alarm_session_key"] = lifecycle.get("key")
        event.metadata["alarm_session_decision"] = lifecycle.get("decision")

        if not current_active:
            lifecycle["applied_to_alarm"] = False
            return lifecycle

        if lifecycle.get("decision") == "UPDATE":
            event.metadata["is_alarm_active"] = False
            event.metadata["status"] = str(lifecycle.get("status") or "correlated")
            event.metadata["final_status"] = str(lifecycle.get("status") or "correlated")
            event.metadata["lifecycle_action"] = str(lifecycle.get("lifecycle_action") or "update")
            event.metadata["alarm_eligible"] = True
            event.explanation = (
                f"{event.explanation} | alarm_session_update=true "
                f"alarm_session_id={lifecycle.get('session_id')} "
                f"alarm_session_reason={lifecycle.get('reason')}"
            )
        else:
            event.metadata.setdefault("is_alarm_active", bool(lifecycle.get("is_alarm_active", True)))
            event.metadata.setdefault("lifecycle_action", str(lifecycle.get("lifecycle_action") or "open"))

        lifecycle["applied_to_alarm"] = True
        return lifecycle

    @staticmethod
    def _visual_gate_allowed_decisions() -> set[str]:
        raw = str(settings.visual_revalidation_gate_decisions or "NOTIFY,LOW_PRIORITY,AUDIT")
        return {token.strip().upper() for token in raw.split(",") if token.strip()}

    @staticmethod
    def _visual_person_score(event, strategy_result: dict) -> float:
        scores: list[float] = []
        for key in ("ia2_person_score", "ia3_person_score", "ia3_person_far_score"):
            try:
                value = strategy_result.get(key)
                if value is not None:
                    scores.append(float(value))
            except Exception:
                continue
        if scores:
            return max(scores)
        try:
            return float(getattr(event, "event_score", 0.0) or 0.0)
        except Exception:
            return 0.0

    def _record_revalidated_visual_track(self, event, strategy_result: dict) -> None:
        if not bool(settings.visual_revalidation_gate_enabled):
            return
        bbox = self._evidence_preparer.freeze_bbox(
            getattr(getattr(event, "evidence", None), "bbox", None)
        )
        if bbox is None:
            return

        notification_decision = str(strategy_result.get("notification_decision") or "SUPPRESS").upper()
        strategy_decision = str(strategy_result.get("decision") or "SUPPRESS").upper()
        if notification_decision not in self._visual_gate_allowed_decisions() or strategy_decision == "SUPPRESS":
            return

        visual_person_score = self._visual_person_score(event, strategy_result)
        min_person_score = float(settings.visual_revalidation_gate_min_person_score or 0.0)
        if visual_person_score < min_person_score:
            return

        timestamp = getattr(event, "timestamp_end", None) or utc_now_naive()
        ttl_seconds = max(0.1, float(settings.visual_revalidation_gate_ttl_seconds or 3.0))
        expires_at = timestamp.timestamp() + ttl_seconds
        track_id = getattr(event, "track_id", None)
        key = f"{getattr(event, 'camera_id', '')}:{getattr(event, 'rule_id', '')}:{track_id}:{getattr(event, 'event_type', '')}"
        self._visual_revalidated_tracks[key] = {
            "bbox": bbox,
            "track_id": int(track_id) if track_id is not None else None,
            "confidence": round(float(visual_person_score), 4),
            "visual_person_score": round(float(visual_person_score), 4),
            "label": "person",
            "visual_status": "revalidated",
            "strategy3_v2_decision": strategy_decision,
            "notification_decision": notification_decision,
            "expires_at": expires_at,
        }

    def visual_tracks(self, *, now: datetime | None = None) -> list[dict]:
        if not bool(settings.visual_revalidation_gate_enabled):
            return []
        now_ts = (now or utc_now_naive()).timestamp()
        expired = [key for key, item in self._visual_revalidated_tracks.items() if float(item.get("expires_at", 0.0) or 0.0) < now_ts]
        for key in expired:
            self._visual_revalidated_tracks.pop(key, None)
        return [
            {key: value for key, value in item.items() if key != "expires_at"}
            for item in self._visual_revalidated_tracks.values()
        ]

    def latency_snapshot(self, camera_id: int) -> dict:
        return self._revalidation_coordinator.timing_snapshot(camera_id)

    @property
    def persistence_queue(self) -> EventPersistenceQueue | None:
        return self._clip_persistence.persistence_queue

    @persistence_queue.setter
    def persistence_queue(self, value: EventPersistenceQueue | None) -> None:
        self._clip_persistence.persistence_queue = value

    @property
    def _clip_history(self):
        return self._clip_persistence.history

    @_clip_history.setter
    def _clip_history(self, value) -> None:
        self._clip_persistence.history = value

    @property
    def _delayed_event_writes(self):
        return self._clip_persistence.delayed_writes

    @_delayed_event_writes.setter
    def _delayed_event_writes(self, value) -> None:
        self._clip_persistence.delayed_writes = value

    def _record_clip_frame(self, camera_id: int, frame, *, captured_at: datetime) -> None:
        self._clip_persistence.record_frame(camera_id, frame, captured_at=captured_at)

    def _select_clip_before_frame(self, camera_id: int, *, event_at: datetime, fallback_frame):
        return self._clip_persistence.select_before_frame(
            camera_id,
            event_at=event_at,
            fallback_frame=fallback_frame,
        )

    def _select_clip_video_frames(self, camera_id: int, *, event_at: datetime) -> list[bytes]:
        return self._clip_persistence.select_video_frames(camera_id, event_at=event_at)

    def _submit_persistence_payload(self, payload: PendingEventWrite) -> bool:
        return self._clip_persistence.submit(payload)

    def _flush_due_event_writes(self, camera_id: int, frame, *, captured_at: datetime) -> int:
        return self._clip_persistence.flush_due_writes(
            camera_id,
            frame,
            captured_at=captured_at,
        )

    def process(
        self,
        camera_id: int,
        tracks: list[dict],
        db,
        frame,
        raw_frame=None,
        annotated_frame=None,
        *,
        detections_fresh: bool = True,
        motion_gate=None,
    ) -> EventBatchResult:
        self._revalidation_coordinator.reset_timings(camera_id)
        captured_at = utc_now_naive()
        snapshot_source = raw_frame if raw_frame is not None else frame
        self._record_clip_frame(camera_id, snapshot_source, captured_at=captured_at)
        persisted_count = self._flush_due_event_writes(camera_id, snapshot_source, captured_at=captured_at)

        # Frames capturados entre duas inferencias ainda alimentam o ring de clip
        # e finalizam evidencias atrasadas, mas nao representam uma nova
        # observacao analitica. Reenviar last_tracks aqui inflaria idade,
        # persistencia e dwell dos tracks quando o motion gate pula a IA1.
        if not detections_fresh:
            return EventBatchResult(generated_events=[], persisted_count=persisted_count)

        batch = self.pipeline.process(camera_id=camera_id, tracks=tracks, db=db, frame=frame, motion_gate=motion_gate)

        anti_fp_patterns = load_anti_fp_patterns()

        if not batch.events:
            if batch.suppressed:
                self.debug_logger.info(
                    "analytics_events_suppressed camera_id=%s suppressed=%s",
                    camera_id,
                    len(batch.suppressed),
                    extra={
                        "camera_id": camera_id,
                        "action": "analytics_events_suppressed",
                        "status": "running",
                        "reason": "dedupe_or_hysteresis",
                    },
                )
            return EventBatchResult(generated_events=[], persisted_count=persisted_count)

        self.debug_logger.info(
            "analytics_events_batch camera_id=%s track_count=%s generated=%s",
            camera_id,
            len(tracks or []),
            [event.event_type for event in batch.events],
            extra={
                "camera_id": camera_id,
                "action": "analytics_events_batch",
                "status": "running",
                "reason": "stateful_rules_processed",
            },
        )

        for event in batch.events:
            source_track = next((track for track in batch.tracks if getattr(track, "track_id", None) == event.track_id), None)
            prepared_evidence = self._evidence_preparer.prepare(
                event,
                camera_id=camera_id,
                snapshot_source=snapshot_source,
                raw_frame_used=raw_frame is not None,
                annotated_frame=annotated_frame,
                captured_at=captured_at,
                camera_profile=getattr(self, "_camera_profile", None),
                policy_preview=dict(getattr(self, "_policy_preview", {}) or {}),
                rule_plan=list(getattr(self, "_policy_rule_plan", []) or []),
            )
            visual_quality = prepared_evidence.visual_quality
            event_captured_at = prepared_evidence.event_captured_at
            clip_before_source = prepared_evidence.clip_before_source
            clip_before_captured_at = prepared_evidence.clip_before_captured_at
            clip_after_source = prepared_evidence.clip_after_source
            clip_after_captured_at = prepared_evidence.clip_after_captured_at
            frozen_evidence_bbox = prepared_evidence.frozen_evidence_bbox

            revalidation_result = self._revalidation_coordinator.evaluate(
                event,
                camera_id=camera_id,
                source_track=source_track,
                db=db,
                snapshot_source=snapshot_source,
                frozen_evidence_bbox=frozen_evidence_bbox,
                anti_fp_patterns=anti_fp_patterns,
            )
            revalidator = revalidation_result.revalidator
            revalidation = revalidation_result.revalidation
            far_revalidation = revalidation_result.far_revalidation
            consensus_revalidation = revalidation_result.consensus_revalidation
            strategy3_v2_review = revalidation_result.strategy3_v2_review
            ia3_v2_block_veto = revalidation_result.ia3_v2_block_veto
            frame_width = revalidation_result.frame_width
            frame_height = revalidation_result.frame_height
            self._record_revalidated_visual_track(event, strategy3_v2_review)
            assessment = assess_event_alarm(
                event,
                source_track=source_track,
                revalidation=revalidation,
                far_revalidation=far_revalidation,
                consensus_revalidation=consensus_revalidation,
                strategy3_v2_review=strategy3_v2_review,
                frame_width=frame_width,
                frame_height=frame_height,
                camera_family=(
                    getattr(self._camera_profile, "camera_family", None)
                    if getattr(self, "_camera_profile", None)
                    else None
                ),
            )
            maturity_safety = assessment.maturity_safety
            alarm_decision = assessment.alarm_decision
            self._record_alarm_decision(event, alarm_decision)
            consensus_block = apply_consensus_block_policy(
                event,
                revalidator_mode=revalidator.current_mode(),
                consensus_revalidation=consensus_revalidation,
                maturity_safety=maturity_safety,
                ia3_v2_block_veto=ia3_v2_block_veto,
                alarm_decision=alarm_decision,
            )
            if consensus_block.applied:
                self._mark_revalidator_canceled(
                    event,
                    source=str(consensus_block.source),
                    reason=str(consensus_block.reason),
                )
                event.explanation = (
                    f"{event.explanation} | consensus_revalidator_canceled=true "
                    f"block_reason={consensus_block.reason}"
                )
                self.debug_logger.info(
                    "analytics_event_blocked_by_consensus_revalidator camera_id=%s event_type=%s track_id=%s ia2_person_score=%s ia3_person_far_score=%s",
                    camera_id,
                    event.event_type,
                    event.track_id,
                    revalidation.person_score,
                    far_revalidation.person_far_score,
                    extra={
                        "camera_id": camera_id,
                        "event_id": event.event_id,
                        "action": "analytics_event_blocked_by_consensus_revalidator",
                        "status": "suppressed",
                        "reason": consensus_block.reason,
                    },
                )
            if (
                not consensus_block.applied
                and not ia3_v2_block_veto
                and revalidator.should_block(revalidation)
            ):
                block_reason = revalidation.block_reason or "unknown"
                self._mark_revalidator_canceled(
                    event,
                    source="ia2_v5",
                    reason=block_reason,
                )
                event.explanation = (
                    f"{event.explanation} | revalidator_canceled=true "
                    f"block_reason={block_reason}"
                )
                self.debug_logger.info(
                    "analytics_event_blocked_by_revalidator camera_id=%s event_type=%s track_id=%s person_score=%s threshold=%s",
                    camera_id,
                    event.event_type,
                    event.track_id,
                    revalidation.person_score,
                    revalidation.threshold,
                    extra={
                        "camera_id": camera_id,
                        "event_id": event.event_id,
                        "action": "analytics_event_blocked_by_revalidator",
                        "status": "suppressed",
                        "reason": block_reason or "person_crop_revalidator_conservative_block",
                    },
                )

            artifact_reason = apply_visual_quality_alarm_gate(
                event,
                alarm_decision,
                visual_quality,
            )
            if artifact_reason is not None:
                self.debug_logger.info(
                    "analytics_event_visual_quality_log_only camera_id=%s event_type=%s track_id=%s reason=%s",
                    camera_id,
                    event.event_type,
                    event.track_id,
                    artifact_reason,
                    extra={
                        "camera_id": camera_id,
                        "event_id": event.event_id,
                        "action": "analytics_event_visual_quality_log_only",
                        "status": "suppressed",
                        "reason": artifact_reason,
                    },
                )
            self._record_alarm_decision(event, alarm_decision)
            alarm_session = self._apply_alarm_session_policy(event)
            event.metadata["final_notification_level"] = str(strategy3_v2_review.get("final_notification_level", "suppressed"))
            final_status_label = event.metadata.get("final_status") or "preserved"
            final_alarm_label = event.metadata.get("is_alarm_active")
            final_alarm_label = str(bool(final_alarm_label)).lower() if final_alarm_label is not None else "preserved"
            event.explanation = (
                f"{event.explanation} | final_notification_level={strategy3_v2_review.get('final_notification_level', 'suppressed')} "
                f"alarm_session_decision={alarm_session.get('decision', 'UNKNOWN')} "
                f"final_status={final_status_label} "
                f"final_is_alarm_active={final_alarm_label}"
            )
            # A copia pesada do frame fica centralizada na fila assíncrona para
            # reduzir o custo do caminho quente do worker.
            snapshot_frame = snapshot_source
            clip_before_frame = clip_before_source
            clip_after_frame = clip_after_source

            clip_video_frames, clip_video_frame_offsets = self._clip_persistence.select_video_clip(
                camera_id,
                event_at=event_captured_at,
            )

            payload = PendingEventWrite(
                camera_id=camera_id,
                event=event,
                snapshot_frame=snapshot_frame,
                clip_before_frame=clip_before_frame,
                clip_after_frame=clip_after_frame,
                clip_video_frames=clip_video_frames,
                clip_video_frame_offsets=clip_video_frame_offsets,
                evidence_bbox=frozen_evidence_bbox,
                snapshot_captured_at=event_captured_at,
                clip_before_captured_at=clip_before_captured_at,
                clip_after_captured_at=clip_after_captured_at,
            )

            after_seconds = max(0.0, float(settings.event_clip_after_seconds or 0.0))
            if after_seconds > 0 and annotated_frame is None:
                payload.clip_after_frame = None
                payload.clip_after_captured_at = None
                event.metadata["clip_context"]["after_captured_at"] = None
                event.metadata["clip_context"]["after_offset_seconds"] = None
                self._clip_persistence.defer(
                    payload,
                    due_at=event_captured_at + timedelta(seconds=after_seconds),
                )
                continue

            if self._submit_persistence_payload(payload):
                persisted_count += 1

        self.debug_logger.info(
            "analytics_events_enqueued camera_id=%s accepted_count=%s generated_count=%s",
            camera_id,
            persisted_count,
            len(batch.events),
            extra={
                "camera_id": camera_id,
                "action": "analytics_events_enqueued",
                "status": "running",
                "reason": "queued_for_async_persistence",
            },
        )
        return EventBatchResult(generated_events=[asdict(event) for event in batch.events], persisted_count=persisted_count)
