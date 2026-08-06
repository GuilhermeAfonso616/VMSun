from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from app.core.config import settings
from app.runtime import event_evidence
from app.runtime.event_evidence import EventEvidencePreparer
from app.runtime.events import EventPipeline


def test_evidence_preparer_freezes_bbox_and_builds_temporal_metadata(monkeypatch):
    captured_at = datetime(2026, 1, 1, 12, 0, 0)
    before_at = captured_at - timedelta(seconds=2)
    before_frame = object()
    annotated_frame = object()
    clip_buffer = Mock()
    clip_buffer.select_before_frame.return_value = (before_frame, before_at)
    quality = SimpleNamespace(
        invalid_reason="blur",
        artifact_reason="compression",
        as_dict=lambda: {"invalid_reason": "blur", "artifact_reason": "compression"},
    )
    monkeypatch.setattr(event_evidence, "analyze_frame_quality", lambda _frame: quality)
    monkeypatch.setattr(settings, "event_clip_before_seconds", 2.0)
    monkeypatch.setattr(settings, "event_clip_after_seconds", 3.0)
    profile = SimpleNamespace(
        scene_profile="perimeter_outdoor",
        camera_family="bullet",
        to_dict=lambda: {"preset_name": "perimeter_bullet"},
    )
    event = SimpleNamespace(
        metadata={},
        explanation="base",
        evidence=SimpleNamespace(bbox=[1, "2", 30.5, 40]),
    )
    snapshot = np.zeros((48, 64, 3), dtype=np.uint8)

    prepared = EventEvidencePreparer(clip_buffer).prepare(
        event,
        camera_id=7,
        snapshot_source=snapshot,
        raw_frame_used=True,
        annotated_frame=annotated_frame,
        captured_at=captured_at,
        camera_profile=profile,
        policy_preview={
            "thresholds": {"person_confidence_min": 0.5},
            "nuisance_flags": ["vegetation_wind"],
            "scene_counts": {"restricted_zones": 1},
        },
        rule_plan=["intrusion_default"],
    )

    assert prepared.frozen_evidence_bbox == [1.0, 2.0, 30.5, 40.0]
    assert event.evidence.bbox == prepared.frozen_evidence_bbox
    assert prepared.clip_before_source is before_frame
    assert prepared.clip_after_source is annotated_frame
    assert event.metadata["profile_snapshot"] == {"preset_name": "perimeter_bullet"}
    assert event.metadata["revalidation_evidence"] == {
        "version": 1,
        "source": "frozen_event_evidence",
        "frame_source": "raw_frame",
        "bbox_source": "event_evidence_bbox",
        "bbox": [1.0, 2.0, 30.5, 40.0],
        "persisted_as_event_snapshot": True,
        "persisted_as_event_bbox": True,
        "width": 64,
        "height": 48,
    }
    assert event.metadata["clip_context"]["before_offset_seconds"] == -2.0
    assert event.metadata["clip_context"]["after_offset_seconds"] == 0.0
    assert "visual_quality_invalid=blur" in event.explanation
    assert "visual_quality_artifact=compression" in event.explanation


def test_evidence_preparer_rejects_malformed_bbox_without_raising():
    assert EventEvidencePreparer.freeze_bbox(None) is None
    assert EventEvidencePreparer.freeze_bbox([1, 2, 3]) is None
    assert EventEvidencePreparer.freeze_bbox([1, "invalid", 3, 4]) is None


def test_event_pipeline_uses_evidence_preparer_with_shared_clip_buffer():
    pipeline = EventPipeline()

    assert isinstance(pipeline._evidence_preparer, EventEvidencePreparer)
    assert pipeline._evidence_preparer.clip_buffer is pipeline._clip_persistence


def test_visual_revalidation_tracking_uses_shared_bbox_contract(monkeypatch):
    monkeypatch.setattr(settings, "visual_revalidation_gate_enabled", True)
    monkeypatch.setattr(settings, "visual_revalidation_gate_decisions", "NOTIFY")
    monkeypatch.setattr(settings, "visual_revalidation_gate_min_person_score", 0.0)
    monkeypatch.setattr(settings, "visual_revalidation_gate_ttl_seconds", 3.0)
    pipeline = EventPipeline()
    event = SimpleNamespace(
        evidence=SimpleNamespace(bbox=[1, "2", 30, 40]),
        event_score=0.8,
        timestamp_end=datetime(2026, 1, 1, 12, 0, 0),
        track_id=9,
        camera_id=7,
        rule_id="intrusion",
        event_type="intrusion",
    )

    pipeline._record_revalidated_visual_track(
        event,
        {"notification_decision": "NOTIFY", "decision": "NOTIFY"},
    )

    tracks = pipeline.visual_tracks(now=event.timestamp_end)
    assert tracks[0]["bbox"] == [1.0, 2.0, 30.0, 40.0]
