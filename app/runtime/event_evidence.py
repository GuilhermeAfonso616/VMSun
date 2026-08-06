"""Preparacao imutavel das evidencias usadas por revalidacao e persistencia."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.analytics.visual_quality import analyze_frame_quality
from app.core.config import settings
from app.runtime.event_clip_buffer import EventClipPersistenceBuffer


@dataclass(slots=True)
class PreparedEventEvidence:
    visual_quality: Any
    event_captured_at: datetime
    clip_before_source: Any
    clip_before_captured_at: datetime
    clip_after_source: Any
    clip_after_captured_at: datetime
    frozen_evidence_bbox: list[float] | None


class EventEvidencePreparer:
    def __init__(self, clip_buffer: EventClipPersistenceBuffer):
        self.clip_buffer = clip_buffer

    @staticmethod
    def freeze_bbox(bbox) -> list[float] | None:
        if not bbox or len(bbox) != 4:
            return None
        try:
            return [float(value) for value in bbox]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def frame_dimensions(frame) -> dict[str, int | None]:
        shape = getattr(frame, "shape", None)
        if not shape or len(shape) < 2:
            return {"width": None, "height": None}
        return {"width": int(shape[1]), "height": int(shape[0])}

    def prepare(
        self,
        event,
        *,
        camera_id: int,
        snapshot_source,
        raw_frame_used: bool,
        annotated_frame,
        captured_at: datetime,
        camera_profile,
        policy_preview: dict,
        rule_plan: list,
    ) -> PreparedEventEvidence:
        profile_snapshot = camera_profile.to_dict() if camera_profile is not None else {}
        event.metadata.setdefault("profile_snapshot", profile_snapshot)
        event.metadata.setdefault(
            "threshold_snapshot",
            dict(policy_preview.get("thresholds", {}) or {}),
        )
        event.metadata.setdefault(
            "nuisance_profile_snapshot",
            list(policy_preview.get("nuisance_flags", []) or []),
        )
        event.metadata.setdefault(
            "scene_profile",
            getattr(camera_profile, "scene_profile", None),
        )
        event.metadata.setdefault(
            "camera_family",
            getattr(camera_profile, "camera_family", None),
        )
        event.metadata.setdefault("rule_plan", list(rule_plan or []))
        event.metadata.setdefault(
            "scene_counts",
            dict(policy_preview.get("scene_counts", {}) or {}),
        )

        visual_quality = analyze_frame_quality(snapshot_source)
        event.metadata["visual_quality"] = visual_quality.as_dict()
        if visual_quality.invalid_reason or visual_quality.artifact_reason:
            event.explanation = (
                f"{event.explanation} | visual_quality_invalid={visual_quality.invalid_reason or '-'} "
                f"visual_quality_artifact={visual_quality.artifact_reason or '-'}"
            )

        event_captured_at = captured_at
        clip_before_source, clip_before_captured_at = self.clip_buffer.select_before_frame(
            camera_id,
            event_at=event_captured_at,
            fallback_frame=snapshot_source,
        )
        clip_after_source = annotated_frame if annotated_frame is not None else snapshot_source
        clip_after_captured_at = event_captured_at
        frozen_evidence_bbox = self.freeze_bbox(event.evidence.bbox)
        event.evidence.bbox = (
            list(frozen_evidence_bbox) if frozen_evidence_bbox is not None else None
        )
        event.metadata["revalidation_evidence"] = {
            "version": 1,
            "source": "frozen_event_evidence",
            "frame_source": "raw_frame" if raw_frame_used else "processed_frame",
            "bbox_source": "event_evidence_bbox",
            "bbox": (
                list(frozen_evidence_bbox)
                if frozen_evidence_bbox is not None
                else None
            ),
            "persisted_as_event_snapshot": True,
            "persisted_as_event_bbox": True,
            **self.frame_dimensions(snapshot_source),
        }
        event.metadata["clip_context"] = {
            "version": 1,
            "before_seconds": float(settings.event_clip_before_seconds or 0.0),
            "after_seconds": float(settings.event_clip_after_seconds or 0.0),
            "before_captured_at": clip_before_captured_at.isoformat(),
            "event_captured_at": event_captured_at.isoformat(),
            "after_captured_at": clip_after_captured_at.isoformat(),
            "before_offset_seconds": round(
                (clip_before_captured_at - event_captured_at).total_seconds(),
                3,
            ),
            "after_offset_seconds": round(
                (clip_after_captured_at - event_captured_at).total_seconds(),
                3,
            ),
        }

        return PreparedEventEvidence(
            visual_quality=visual_quality,
            event_captured_at=event_captured_at,
            clip_before_source=clip_before_source,
            clip_before_captured_at=clip_before_captured_at,
            clip_after_source=clip_after_source,
            clip_after_captured_at=clip_after_captured_at,
            frozen_evidence_bbox=frozen_evidence_bbox,
        )
