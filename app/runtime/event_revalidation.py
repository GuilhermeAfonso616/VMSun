"""Orquestracao ordenada dos revalidadores IA2, IA3 e Strategy3."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from types import SimpleNamespace
from typing import Any, Callable

from app.analytics_v2.revalidation import (
    FarPersonRevalidator,
    PersonCropRevalidator,
    build_strategy3_v2_review_payload,
    evaluate_consensus_block_candidate,
    get_far_person_revalidator,
    get_person_crop_revalidator,
    ia3_v2_protection_blocks_auto_cancel,
)
from app.analytics_v2.revalidation.aux_inference_client import (
    build_ia2_client,
    build_ia3_client,
)
from app.analytics_v2.revalidation.aux_inference_types import (
    IA2Request,
    IA3Request,
    deadline_from_ms,
    new_job_id,
)
from app.core.config import settings
from app.db.models import Event, EventFeedback
from app.services.revalidator_region_memory_service import build_region_memory


_DEFAULT = object()


@dataclass(slots=True)
class EventRevalidationResult:
    revalidator: Any
    revalidation: Any
    far_revalidation: Any
    consensus_revalidation: dict
    strategy3_v2_review: dict
    ia3_v2_block_veto: bool
    frame_width: int | float | None
    frame_height: int | float | None


class EventRevalidationCoordinator:
    def __init__(
        self,
        logger,
        debug_logger,
        *,
        person_revalidator_provider: Callable[[], Any] | None = None,
        far_revalidator_provider: Callable[[], Any] | None = None,
        shadow_revalidators: list[tuple[str, Any]] | None = None,
        protection_revalidator: Any = _DEFAULT,
    ):
        self.logger = logger
        self.debug_logger = debug_logger
        self.person_revalidator_provider = (
            person_revalidator_provider or get_person_crop_revalidator
        )
        self.far_revalidator_provider = far_revalidator_provider or get_far_person_revalidator
        self.shadow_revalidators = (
            self._build_ia2_shadow_revalidators()
            if shadow_revalidators is None
            else list(shadow_revalidators)
        )
        self.protection_revalidator = (
            self._build_ia3_v2_protection_revalidator()
            if protection_revalidator is _DEFAULT
            else protection_revalidator
        )
        self._timings_by_camera: dict[int, dict[str, float | int]] = {}

    def reset_timings(self, camera_id: int) -> None:
        self._timings_by_camera[int(camera_id)] = {
            "ia2_ms": 0.0,
            "ia3_ms": 0.0,
            "ia2_calls": 0,
            "ia3_calls": 0,
            "ia2_errors": 0,
            "ia3_errors": 0,
            "revalidation_events": 0,
        }

    def timing_snapshot(self, camera_id: int) -> dict:
        return dict(self._timings_by_camera.get(int(camera_id)) or {})

    @staticmethod
    def _build_ia2_shadow_revalidators() -> list[tuple[str, PersonCropRevalidator]]:
        revalidators: list[tuple[str, PersonCropRevalidator]] = []
        if bool(settings.ia2_v8b_shadow_enabled):
            revalidators.append(
                (
                    "ia2_v8b_shadow",
                    PersonCropRevalidator(
                        model_path=settings.ia2_v8b_shadow_model_path,
                        threshold=settings.ia2_v8b_shadow_threshold,
                        mode="audit",
                        enabled=True,
                        policy_controlled=False,
                    ),
                )
            )
        if bool(settings.ia2_v8c_shadow_enabled):
            revalidators.append(
                (
                    "ia2_v8c_shadow",
                    PersonCropRevalidator(
                        model_path=settings.ia2_v8c_shadow_model_path,
                        threshold=settings.ia2_v8c_shadow_threshold,
                        mode="audit",
                        enabled=True,
                        policy_controlled=False,
                    ),
                )
            )
        return revalidators

    @staticmethod
    def _build_ia3_v2_protection_revalidator() -> FarPersonRevalidator | None:
        if not bool(settings.ia3_v2_protection_enabled):
            return None
        return FarPersonRevalidator(
            model_path=settings.ia3_v2_protection_model_path,
            threshold=settings.ia3_v2_protection_threshold,
            enabled=True,
        )

    def _evaluate_shadows(self, frame, bbox, baseline_result) -> dict[str, dict]:
        shadow_results: dict[str, dict] = {}
        baseline_passed = bool(getattr(baseline_result, "passed", False))
        for name, revalidator in self.shadow_revalidators:
            result = revalidator.validate(frame, bbox)
            metadata = result.to_metadata()
            metadata["shadow_only"] = True
            metadata["baseline_passed"] = baseline_passed
            metadata["disagrees_with_baseline"] = bool(
                result.applied and bool(result.passed) != baseline_passed
            )
            metadata["operational_decision"] = (
                "shadow_accept" if result.passed else "shadow_reject"
            )
            shadow_results[name] = metadata
        return shadow_results

    def _evaluate_protection(
        self,
        frame,
        bbox,
        *,
        base_quality: dict | None,
        ia2_result,
        ia3_v1_result,
    ) -> dict | None:
        if self.protection_revalidator is None:
            return None
        result = self.protection_revalidator.validate(
            frame,
            bbox,
            base_quality=base_quality,
            ia2_result=ia2_result,
        )
        metadata = result.to_metadata()
        metadata["shadow_only"] = True
        metadata["protection_only"] = True
        metadata["accepts_automatically"] = False
        metadata["protects_when_primary_rejects"] = bool(
            result.applied
            and result.passed
            and not bool(getattr(ia2_result, "passed", False))
            and not bool(getattr(ia3_v1_result, "passed", False))
        )
        metadata["recommended_action"] = (
            "UNCERTAIN_AUDIT"
            if metadata["protects_when_primary_rejects"]
            else "NO_RUNTIME_CHANGE"
        )
        return metadata

    def _runtime_region_memory(
        self,
        db,
        *,
        camera_id: int,
        bbox: list[float] | None,
        frame_width: int | float | None,
        frame_height: int | float | None,
    ) -> dict | None:
        if not bool(settings.region_memory_enabled) or db is None:
            return None
        try:
            current_event = SimpleNamespace(
                id=None,
                camera_id=camera_id,
                bbox_json=json.dumps(bbox) if bbox else None,
            )
            training_limit = max(1, int(settings.region_memory_runtime_training_limit or 25))
            history_rows = (
                db.query(EventFeedback, Event)
                .join(Event, Event.id == EventFeedback.event_id)
                .filter(EventFeedback.camera_id == camera_id)
                .order_by(EventFeedback.reviewed_at.desc(), EventFeedback.id.desc())
                .limit(training_limit * 3)
                .all()
            )
            latest_history_rows = []
            seen_event_ids: set[int] = set()
            for history_feedback, history_event in history_rows:
                event_id = int(getattr(history_event, "id", 0) or 0)
                if event_id and event_id in seen_event_ids:
                    continue
                if event_id:
                    seen_event_ids.add(event_id)
                latest_history_rows.append((history_feedback, history_event))
                if len(latest_history_rows) >= training_limit:
                    break
            region_memory = build_region_memory(
                event=current_event,
                feedback=None,
                history_rows=latest_history_rows,
                frame_width=frame_width,
                frame_height=frame_height,
            )
            region_memory["runtime_training_limit"] = training_limit
            region_memory["runtime_history_rows_loaded"] = len(history_rows)
            region_memory["runtime_history_unique_events"] = len(latest_history_rows)
            return region_memory
        except Exception:
            self.logger.exception(
                "Runtime region memory failed camera_id=%s",
                camera_id,
                extra={
                    "camera_id": camera_id,
                    "action": "runtime_region_memory_failed",
                    "status": "degraded",
                    "reason": "region_memory_exception",
                },
            )
            return None

    def _record_consensus_candidate(self, event, camera_id: int, consensus: dict) -> None:
        if consensus.get("block_candidate"):
            event.explanation = (
                f"{event.explanation} | consensus_block_candidate=true "
                f"reason={consensus.get('reason')}"
            )
            self.debug_logger.info(
                "analytics_event_consensus_block_candidate camera_id=%s event_type=%s track_id=%s",
                camera_id,
                event.event_type,
                event.track_id,
                extra={
                    "camera_id": camera_id,
                    "event_id": event.event_id,
                    "action": "analytics_event_consensus_block_candidate",
                    "status": "running",
                    "reason": consensus.get("reason") or "ia2_ia3_consensus_not_person",
                },
            )
            return

        dynamic_candidates = (
            "ia3_confirmed_dynamic_candidate",
            "ia2_dominant_ia3_non_person_candidate",
            "balanced_block_candidate",
        )
        candidate_name = next((name for name in dynamic_candidates if consensus.get(name)), None)
        if candidate_name is not None:
            reason = consensus.get("reason") or candidate_name
            event.explanation = (
                f"{event.explanation} | {candidate_name}=true reason={reason}"
            )
            self.debug_logger.info(
                "analytics_event_%s camera_id=%s event_type=%s track_id=%s",
                candidate_name,
                camera_id,
                event.event_type,
                event.track_id,
                extra={
                    "camera_id": camera_id,
                    "event_id": event.event_id,
                    "action": f"analytics_event_{candidate_name}",
                    "status": "running",
                    "reason": reason,
                },
            )
            return

        quality_candidates = (
            ("small_bbox_consensus_candidate", "quality_gate_blocked"),
            ("border_consensus_candidate", "border_blocked"),
        )
        for candidate_name, fallback_reason in quality_candidates:
            if not consensus.get(candidate_name):
                continue
            quality_reason = consensus.get("quality_reason") or "unknown"
            event.explanation = (
                f"{event.explanation} | {candidate_name}=true quality_reason={quality_reason}"
            )
            self.debug_logger.info(
                f"analytics_event_{candidate_name} camera_id=%s event_type=%s track_id=%s quality_reason=%s",
                camera_id,
                event.event_type,
                event.track_id,
                consensus.get("quality_reason"),
                extra={
                    "camera_id": camera_id,
                    "event_id": event.event_id,
                    "action": f"analytics_event_{candidate_name}",
                    "status": "running",
                    "reason": consensus.get("quality_reason") or fallback_reason,
                },
            )
            return

        simple_candidates = (
            ("ia2_strong_not_person_without_ia3", "ia3_not_triggered"),
            ("ia2_only_balanced_candidate", "ia2_only_balanced_not_person"),
        )
        for candidate_name, fallback_reason in simple_candidates:
            if not consensus.get(candidate_name):
                continue
            reason = consensus.get("reason") or fallback_reason
            event.explanation = (
                f"{event.explanation} | {candidate_name}=true reason={reason}"
            )
            self.debug_logger.info(
                f"analytics_event_{candidate_name} camera_id=%s event_type=%s track_id=%s",
                camera_id,
                event.event_type,
                event.track_id,
                extra={
                    "camera_id": camera_id,
                    "event_id": event.event_id,
                    "action": f"analytics_event_{candidate_name}",
                    "status": "running",
                    "reason": reason,
                },
            )
            return

    @staticmethod
    def _job_identity(event, source_track) -> dict:
        """Identidade minima do job auxiliar, usada para rejeitar resposta stale."""
        metadata = getattr(source_track, "metadata", None)
        generation_id = None
        if isinstance(metadata, dict):
            try:
                raw = metadata.get("generation_id")
                generation_id = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                generation_id = None
        frame_id = None
        if isinstance(metadata, dict):
            try:
                raw = metadata.get("frame_id")
                frame_id = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                frame_id = None
        return {
            "frame_id": frame_id,
            "generation_id": generation_id,
            "track_id": getattr(event, "track_id", None),
            "event_candidate_id": getattr(event, "event_id", None),
        }

    def _run_ia2(self, event, *, camera_id: int, source_track, frame, bbox):
        """IA2 atraves da interface de execucao (local ou central)."""
        request = IA2Request(
            job_id=new_job_id(),
            camera_id=int(camera_id),
            model_type="ia2",
            deadline_monotonic_ns=deadline_from_ms(settings.ia2_pool_timeout_ms),
            metadata={"frame": frame, "bbox": bbox},
            **self._job_identity(event, source_track),
        )
        client = build_ia2_client(
            camera_id,
            local_provider=self.person_revalidator_provider,
        )
        return client.infer(request).native

    def _run_ia3(self, event, *, camera_id: int, source_track, frame, bbox, ia2_result):
        """IA3 atraves da interface de execucao (local ou central)."""
        request = IA3Request(
            job_id=new_job_id(),
            camera_id=int(camera_id),
            model_type="ia3",
            deadline_monotonic_ns=deadline_from_ms(settings.ia3_pool_timeout_ms),
            base_quality=getattr(ia2_result, "quality", None),
            ia2_person_score=getattr(ia2_result, "person_score", None),
            ia2_not_person_score=getattr(ia2_result, "not_person_score", None),
            ia2_applied=bool(getattr(ia2_result, "applied", False)),
            metadata={"frame": frame, "bbox": bbox, "ia2_result": ia2_result},
            **self._job_identity(event, source_track),
        )
        client = build_ia3_client(
            camera_id,
            local_provider=self.far_revalidator_provider,
        )
        return client.infer(request).native

    def evaluate(
        self,
        event,
        *,
        camera_id: int,
        source_track,
        db,
        snapshot_source,
        frozen_evidence_bbox: list[float] | None,
        anti_fp_patterns: list | dict,
    ) -> EventRevalidationResult:
        revalidator = self.person_revalidator_provider()
        timings = self._timings_by_camera.setdefault(
            int(camera_id),
            {
                "ia2_ms": 0.0,
                "ia3_ms": 0.0,
                "ia2_calls": 0,
                "ia3_calls": 0,
                "ia2_errors": 0,
                "ia3_errors": 0,
                "revalidation_events": 0,
            },
        )
        ia2_started = time.perf_counter()
        timings["ia2_started_at_ns"] = time.time_ns()
        timings["ia2_calls"] = int(timings.get("ia2_calls") or 0) + 1
        try:
            revalidation = self._run_ia2(
                event,
                camera_id=camera_id,
                source_track=source_track,
                frame=snapshot_source,
                bbox=frozen_evidence_bbox,
            )
        except Exception:
            timings["ia2_errors"] = int(timings.get("ia2_errors") or 0) + 1
            raise
        finally:
            timings["ia2_completed_at_ns"] = time.time_ns()
            timings["ia2_ms"] = float(timings.get("ia2_ms") or 0.0) + (
                time.perf_counter() - ia2_started
            ) * 1000.0
        event.metadata["person_revalidator"] = revalidation.to_metadata()
        if revalidation.applied:
            person_score = (
                f"{revalidation.person_score:.3f}"
                if revalidation.person_score is not None
                else "-"
            )
            threshold = (
                f"{revalidation.threshold:.2f}"
                if revalidation.threshold is not None
                else "-"
            )
            event.explanation = (
                f"{event.explanation} | revalidator_person={person_score} "
                f"threshold={threshold} mode={revalidation.mode}"
            )
        else:
            event.explanation = (
                f"{event.explanation} | revalidator_skipped={revalidation.reason or 'unknown'} "
                f"mode={revalidation.mode}"
            )

        far_revalidator = self.far_revalidator_provider()
        ia3_started = time.perf_counter()
        timings["ia3_started_at_ns"] = time.time_ns()
        timings["ia3_calls"] = int(timings.get("ia3_calls") or 0) + 1
        try:
            far_revalidation = self._run_ia3(
                event,
                camera_id=camera_id,
                source_track=source_track,
                frame=snapshot_source,
                bbox=frozen_evidence_bbox,
                ia2_result=revalidation,
            )
        except Exception:
            timings["ia3_errors"] = int(timings.get("ia3_errors") or 0) + 1
            raise
        finally:
            timings["ia3_completed_at_ns"] = time.time_ns()
            timings["ia3_ms"] = float(timings.get("ia3_ms") or 0.0) + (
                time.perf_counter() - ia3_started
            ) * 1000.0
        timings["revalidation_events"] = int(
            timings.get("revalidation_events") or 0
        ) + 1
        event.metadata["far_person_revalidator"] = far_revalidation.to_metadata()
        if far_revalidation.applied:
            far_score = (
                f"{far_revalidation.person_far_score:.3f}"
                if far_revalidation.person_far_score is not None
                else "-"
            )
            event.explanation = (
                f"{event.explanation} | far_revalidator_person={far_score} "
                f"threshold={far_revalidation.threshold:.3f} mode=audit"
            )
            self.debug_logger.info(
                "analytics_event_far_revalidator_audit camera_id=%s event_type=%s track_id=%s person_far_score=%s threshold=%s",
                camera_id,
                event.event_type,
                event.track_id,
                far_revalidation.person_far_score,
                far_revalidation.threshold,
                extra={
                    "camera_id": camera_id,
                    "event_id": event.event_id,
                    "action": "analytics_event_far_revalidator_audit",
                    "status": "running",
                    "reason": far_revalidation.trigger_reason or "far_candidate",
                },
            )
        elif far_revalidation.triggered:
            event.explanation = (
                f"{event.explanation} | far_revalidator_skipped={far_revalidation.reason or 'unknown'} "
                "mode=audit"
            )

        shadow_results = self._evaluate_shadows(
            snapshot_source,
            frozen_evidence_bbox,
            revalidation,
        )
        if shadow_results:
            event.metadata["person_revalidator_shadow"] = shadow_results
            discordant = [
                name
                for name, result in shadow_results.items()
                if bool(result.get("disagrees_with_baseline"))
            ]
            if discordant:
                event.metadata["person_revalidator_shadow_discordance"] = discordant
                event.explanation = (
                    f"{event.explanation} | ia2_shadow_discordance={','.join(discordant)}"
                )

        protection = self._evaluate_protection(
            snapshot_source,
            frozen_evidence_bbox,
            base_quality=revalidation.quality,
            ia2_result=revalidation,
            ia3_v1_result=far_revalidation,
        )
        if protection is not None:
            event.metadata["far_person_revalidator_v2_protection"] = protection
            if bool(protection.get("protects_when_primary_rejects")):
                event.metadata["ia3_v2_protection_recommendation"] = "UNCERTAIN_AUDIT"
                event.explanation = (
                    f"{event.explanation} | ia3_v2_protection=UNCERTAIN_AUDIT"
                )
        block_veto = ia3_v2_protection_blocks_auto_cancel(protection)
        if block_veto:
            enforce = str(settings.ia3_v2_protection_mode or "audit").strip().lower() not in {
                "",
                "audit",
                "shadow",
            }
            event.metadata["ia3_v2_block_veto_candidate"] = True
            event.metadata["ia3_v2_block_veto"] = enforce
            event.explanation = (
                f"{event.explanation} | ia3_v2_block_veto={'true' if enforce else 'audit'}"
            )
            block_veto = enforce

        consensus = evaluate_consensus_block_candidate(revalidation, far_revalidation)
        event.metadata["consensus_revalidator"] = consensus
        self._record_consensus_candidate(event, camera_id, consensus)

        quality = dict(getattr(revalidation, "quality", None) or {})
        frame_width = quality.get("frame_width")
        frame_height = quality.get("frame_height")
        region_memory = self._runtime_region_memory(
            db,
            camera_id=camera_id,
            bbox=frozen_evidence_bbox,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        detector_score = (
            getattr(source_track, "last_detection_score", None)
            if source_track is not None
            and getattr(source_track, "last_detection_score", None) is not None
            else getattr(source_track, "score", None)
            if source_track is not None and getattr(source_track, "score", None) is not None
            else event.event_score
        )
        strategy = build_strategy3_v2_review_payload(
            ia2_result=revalidation,
            ia3_result=far_revalidation,
            detector_score=detector_score,
            bbox=frozen_evidence_bbox,
            frame_width=frame_width,
            frame_height=frame_height,
            camera_id=camera_id,
            track=source_track,
            timestamp=event.timestamp_end,
            event=event,
            region_memory=region_memory,
            anti_fp_patterns=anti_fp_patterns,
        )
        event.metadata["strategy3_v2"] = strategy
        event.metadata["anti_fp_post_filter"] = strategy.get("anti_fp_post_filter")
        return EventRevalidationResult(
            revalidator=revalidator,
            revalidation=revalidation,
            far_revalidation=far_revalidation,
            consensus_revalidation=consensus,
            strategy3_v2_review=strategy,
            ia3_v2_block_veto=bool(block_veto),
            frame_width=frame_width,
            frame_height=frame_height,
        )
