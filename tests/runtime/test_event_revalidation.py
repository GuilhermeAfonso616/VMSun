from types import SimpleNamespace
from unittest.mock import Mock

from app.core.config import settings
from app.runtime import event_revalidation
from app.runtime.event_revalidation import EventRevalidationCoordinator
from app.runtime.events import EventPipeline


class _Result:
    def __init__(self, *, kind: str, passed: bool, applied: bool = True):
        self.kind = kind
        self.passed = passed
        self.applied = applied
        self.person_score = 0.8 if kind == "ia2" else None
        self.person_far_score = 0.7 if kind == "ia3" else None
        self.threshold = 0.5
        self.mode = "audit"
        self.reason = None
        self.triggered = kind == "ia3"
        self.trigger_reason = "far_candidate"
        self.block_reason = None
        self.quality = {"frame_width": 640, "frame_height": 360}

    def to_metadata(self):
        return {"kind": self.kind, "passed": self.passed, "applied": self.applied}


class _Revalidator:
    def __init__(self, name: str, result: _Result, calls: list[str]):
        self.name = name
        self.result = result
        self.calls = calls

    def validate(self, *_args, **_kwargs):
        self.calls.append(self.name)
        return self.result


def _event():
    return SimpleNamespace(
        metadata={},
        explanation="base",
        event_type="intrusion",
        track_id=9,
        event_id="event-9",
        event_score=0.75,
        timestamp_end=SimpleNamespace(),
    )


def test_revalidation_coordinator_preserves_model_and_strategy_order(monkeypatch):
    monkeypatch.setattr(settings, "region_memory_enabled", False)
    calls: list[str] = []
    ia2 = _Result(kind="ia2", passed=True)
    ia3 = _Result(kind="ia3", passed=True)
    shadow = _Result(kind="shadow", passed=False)
    coordinator = EventRevalidationCoordinator(
        Mock(),
        Mock(),
        person_revalidator_provider=lambda: _Revalidator("ia2", ia2, calls),
        far_revalidator_provider=lambda: _Revalidator("ia3", ia3, calls),
        shadow_revalidators=[("candidate_shadow", _Revalidator("shadow", shadow, calls))],
        protection_revalidator=None,
    )

    def consensus(_ia2, _ia3):
        calls.append("consensus")
        return {"block_candidate": False}

    def strategy(**kwargs):
        calls.append("strategy3")
        assert kwargs["ia2_result"] is ia2
        assert kwargs["ia3_result"] is ia3
        return {"decision": "PERSON", "anti_fp_post_filter": {"blocked": False}}

    monkeypatch.setattr(event_revalidation, "evaluate_consensus_block_candidate", consensus)
    monkeypatch.setattr(event_revalidation, "build_strategy3_v2_review_payload", strategy)
    event = _event()

    result = coordinator.evaluate(
        event,
        camera_id=7,
        source_track=SimpleNamespace(last_detection_score=0.9, score=0.8),
        db=None,
        snapshot_source=object(),
        frozen_evidence_bbox=[1.0, 2.0, 30.0, 40.0],
        anti_fp_patterns=[],
    )

    assert calls == ["ia2", "ia3", "shadow", "consensus", "strategy3"]
    assert result.revalidation is ia2
    assert result.far_revalidation is ia3
    assert event.metadata["person_revalidator_shadow_discordance"] == [
        "candidate_shadow"
    ]
    assert event.metadata["strategy3_v2"]["decision"] == "PERSON"


def test_ia3_v2_protection_is_audit_only_until_mode_is_enforced(monkeypatch):
    monkeypatch.setattr(settings, "region_memory_enabled", False)
    monkeypatch.setattr(
        event_revalidation,
        "evaluate_consensus_block_candidate",
        lambda *_args: {"block_candidate": False},
    )
    monkeypatch.setattr(
        event_revalidation,
        "build_strategy3_v2_review_payload",
        lambda **_kwargs: {"decision": "AUDIT", "anti_fp_post_filter": None},
    )
    monkeypatch.setattr(
        event_revalidation,
        "ia3_v2_protection_blocks_auto_cancel",
        lambda metadata: bool(metadata and metadata.get("protects_when_primary_rejects")),
    )
    calls: list[str] = []
    ia2 = _Result(kind="ia2", passed=False)
    ia3 = _Result(kind="ia3", passed=False)
    protection = _Result(kind="protection", passed=True)
    coordinator = EventRevalidationCoordinator(
        Mock(),
        Mock(),
        person_revalidator_provider=lambda: _Revalidator("ia2", ia2, calls),
        far_revalidator_provider=lambda: _Revalidator("ia3", ia3, calls),
        shadow_revalidators=[],
        protection_revalidator=_Revalidator("protection", protection, calls),
    )

    monkeypatch.setattr(settings, "ia3_v2_protection_mode", "audit")
    audit_event = _event()
    audit = coordinator.evaluate(
        audit_event,
        camera_id=7,
        source_track=None,
        db=None,
        snapshot_source=object(),
        frozen_evidence_bbox=[1.0, 2.0, 30.0, 40.0],
        anti_fp_patterns=[],
    )
    monkeypatch.setattr(settings, "ia3_v2_protection_mode", "enforce")
    enforce_event = _event()
    enforced = coordinator.evaluate(
        enforce_event,
        camera_id=7,
        source_track=None,
        db=None,
        snapshot_source=object(),
        frozen_evidence_bbox=[1.0, 2.0, 30.0, 40.0],
        anti_fp_patterns=[],
    )

    assert audit.ia3_v2_block_veto is False
    assert audit_event.metadata["ia3_v2_block_veto_candidate"] is True
    assert audit_event.metadata["ia3_v2_block_veto"] is False
    assert enforced.ia3_v2_block_veto is True
    assert enforce_event.metadata["ia3_v2_block_veto"] is True


def test_runtime_region_memory_deduplicates_latest_feedback_per_event(monkeypatch):
    monkeypatch.setattr(settings, "region_memory_enabled", True)
    monkeypatch.setattr(settings, "region_memory_runtime_training_limit", 2)
    events = [SimpleNamespace(id=value) for value in (1, 1, 2, 3)]
    rows = [(SimpleNamespace(), event) for event in events]

    class Query:
        def join(self, *_args):
            return self

        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def all(self):
            return rows

    db = SimpleNamespace(query=lambda *_args: Query())
    builder = Mock(return_value={})
    monkeypatch.setattr(event_revalidation, "build_region_memory", builder)
    coordinator = EventRevalidationCoordinator(
        Mock(),
        Mock(),
        shadow_revalidators=[],
        protection_revalidator=None,
    )

    memory = coordinator._runtime_region_memory(
        db,
        camera_id=7,
        bbox=[1, 2, 3, 4],
        frame_width=640,
        frame_height=360,
    )

    assert memory["runtime_history_rows_loaded"] == 4
    assert memory["runtime_history_unique_events"] == 2
    passed_rows = builder.call_args.kwargs["history_rows"]
    assert [event.id for _feedback, event in passed_rows] == [1, 2]


def test_event_pipeline_composes_revalidation_coordinator_with_shared_loggers():
    pipeline = EventPipeline()

    assert isinstance(
        pipeline._revalidation_coordinator,
        EventRevalidationCoordinator,
    )
    assert pipeline._revalidation_coordinator.logger is pipeline.logger
    assert pipeline._revalidation_coordinator.debug_logger is pipeline.debug_logger
