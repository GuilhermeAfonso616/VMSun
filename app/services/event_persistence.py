"""Persistencia assíncrona de eventos analíticos.

O worker principal enfileira eventos leves aqui e segue o loop de vídeo.
A thread dedicada faz snapshot, clip, commit e integracao externa sem
bloquear o caminho quente da câmera.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from queue import Empty, Full, Queue
from threading import Event as ThreadEvent
from threading import Lock, Thread
import json

from app.core.timezone import utc_now_naive
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models import Event
from app.core.logging import get_camera_logger
from app.services.event_ai_validation import derive_ai_validation
from app.services.event_snapshot_store import event_snapshot_store
from app.services.event_retention_service import maybe_prune_expired_events
from app.services.local_clip_retention_service import prune_local_review_clips
from app.services.lockdown_ingest_service import send_event_if_needed
from app.services.notification_service import enqueue_event_notifications
from app.services.incident_service import initialize_incident
from app.services.onedrive_client import onedrive_client
from app.services.event_broadcaster import event_broadcaster
from app.web.event_listing_presenter import serialize_event_for_table


@dataclass(slots=True)
class PendingEventWrite:
    """Payload mínimo e seguro para persistência fora do loop principal."""

    camera_id: int
    event: Any
    snapshot_frame: Any | None
    clip_before_frame: Any | None
    clip_after_frame: Any | None
    clip_video_frames: list[Any] = field(default_factory=list)
    # Instante real de cada frame do clipe, em segundos a partir do primeiro.
    clip_video_frame_offsets: list[float] = field(default_factory=list)
    evidence_bbox: list[float] | None = None
    snapshot_captured_at: datetime | None = None
    clip_before_captured_at: datetime | None = None
    clip_after_captured_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now_naive)

    @staticmethod
    def _copy_frame(frame):
        if frame is None or isinstance(frame, bytes):
            return frame
        if isinstance(frame, (bytearray, memoryview)):
            return bytes(frame)
        return frame.copy() if hasattr(frame, "copy") else frame

    def copy(self) -> "PendingEventWrite":
        return PendingEventWrite(
            camera_id=int(self.camera_id),
            event=self.event,
            snapshot_frame=self._copy_frame(self.snapshot_frame),
            clip_before_frame=self._copy_frame(self.clip_before_frame),
            clip_after_frame=self._copy_frame(self.clip_after_frame),
            clip_video_frames=[self._copy_frame(frame) for frame in self.clip_video_frames],
            clip_video_frame_offsets=list(self.clip_video_frame_offsets),
            evidence_bbox=list(self.evidence_bbox) if self.evidence_bbox is not None else None,
            snapshot_captured_at=self.snapshot_captured_at,
            clip_before_captured_at=self.clip_before_captured_at,
            clip_after_captured_at=self.clip_after_captured_at,
            created_at=self.created_at,
        )


@dataclass(slots=True)
class EventPersistenceStats:
    queue_size: int = 0
    events_queued: int = 0
    events_persisted: int = 0
    events_failed: int = 0
    dropped_or_rejected_jobs: int = 0
    persist_latency_ms: float = 0.0
    last_persist_latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue_size": self.queue_size,
            "events_queued": self.events_queued,
            "events_persisted": self.events_persisted,
            "events_failed": self.events_failed,
            "dropped_or_rejected_jobs": self.dropped_or_rejected_jobs,
            "persist_latency_ms": round(self.persist_latency_ms, 2),
            "last_persist_latency_ms": round(self.last_persist_latency_ms, 2),
        }


class EventPersistenceQueue:
    """Fila curta com writer dedicado para persistencia de eventos."""

    def __init__(self, camera_id: int, *, worker_mode: str = "normal", maxsize: int = 8):
        self.camera_id = int(camera_id)
        self.worker_mode = worker_mode
        self.maxsize = max(1, int(maxsize))
        self.logger = get_camera_logger(
            "app.event_persistence",
            camera_id=self.camera_id,
            worker_mode=self.worker_mode,
        )
        self._queue: Queue[PendingEventWrite] = Queue(maxsize=self.maxsize)
        self._stop_event = ThreadEvent()
        self._thread: Thread | None = None
        self._stats_lock = Lock()
        self._stats = EventPersistenceStats()
        self._latency_samples: list[float] = []

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = Thread(
            target=self._run,
            name=f"event-persistence-{self.camera_id}",
            daemon=True,
        )
        self._thread.start()
        self.logger.info(
            "Event persistence worker started",
            extra={
                "action": "event_persistence_start",
                "status": "running",
                "reason": "worker_started",
            },
        )

    def submit(self, payload: PendingEventWrite) -> bool:
        if self._stop_event.is_set():
            with self._stats_lock:
                self._stats.dropped_or_rejected_jobs += 1
            self.logger.warning(
                "Event persistence rejected because shutdown was requested",
                extra={
                    "action": "event_persistence_reject",
                    "status": "stopped",
                    "reason": "shutdown_requested",
                },
            )
            return False

        try:
            self._queue.put_nowait(payload.copy())
        except Full:
            with self._stats_lock:
                self._stats.dropped_or_rejected_jobs += 1
                self._stats.queue_size = self._queue.qsize()
            self.logger.warning(
                "Event persistence queue full camera_id=%s queue_size=%s maxsize=%s",
                self.camera_id,
                self._queue.qsize(),
                self.maxsize,
                extra={
                    "action": "event_persistence_queue_full",
                    "status": "degraded",
                    "reason": "queue_full",
                },
            )
            return False

        with self._stats_lock:
            self._stats.events_queued += 1
            self._stats.queue_size = self._queue.qsize()
        self.logger.debug(
            "Event persistence queued event_type=%s queue_size=%s",
            getattr(payload.event, "event_type", None),
            self._queue.qsize(),
            extra={
                "action": "event_persistence_enqueue",
                "status": "running",
                "reason": "queued",
            },
        )
        return True

    def persist_inline(self, payload: PendingEventWrite) -> bool:
        return self._persist_payload(payload.copy(), inline_fallback=True)

    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is None:
            return

        if drain:
            deadline = time.perf_counter() + max(0.0, float(timeout))
            while time.perf_counter() < deadline:
                if self._queue.empty():
                    break
                time.sleep(0.05)

        self._thread.join(timeout=max(0.0, float(timeout)))
        if self._thread.is_alive():
            self.logger.warning(
                "Event persistence worker did not stop before timeout",
                extra={
                    "action": "event_persistence_stop_timeout",
                    "status": "degraded",
                    "reason": "join_timeout",
                },
            )
        else:
            self.logger.info(
                "Event persistence worker stopped",
                extra={
                    "action": "event_persistence_stop",
                    "status": "stopped",
                    "reason": "shutdown",
                },
            )

    def stats(self) -> dict[str, Any]:
        with self._stats_lock:
            self._stats.queue_size = self._queue.qsize()
            return self._stats.to_dict()

    def _run(self) -> None:
        while True:
            if self._stop_event.is_set() and self._queue.empty():
                break

            try:
                payload = self._queue.get(timeout=0.2)
            except Empty:
                continue

            try:
                self._persist_payload(payload)
            finally:
                self._queue.task_done()

    def _record_latency(self, latency_ms: float) -> None:
        with self._stats_lock:
            self._stats.last_persist_latency_ms = float(latency_ms)
            self._latency_samples.append(float(latency_ms))
            if len(self._latency_samples) > 100:
                self._latency_samples.pop(0)
            self._stats.persist_latency_ms = sum(self._latency_samples) / max(1, len(self._latency_samples))

    def _mark_failed(self, *, reason: str, event_id: Any | None = None) -> None:
        with self._stats_lock:
            self._stats.events_failed += 1
        self.logger.exception(
            "Event persistence failed event_id=%s reason=%s",
            event_id,
            reason,
            extra={
                "action": "event_persistence_failed",
                "status": "error",
                "reason": reason,
            },
        )

    def _build_event_row(self, payload: PendingEventWrite) -> Event:
        event = payload.event
        metadata = getattr(event, "metadata", {}) or {}
        profile_snapshot = metadata.get("profile_snapshot", {})
        threshold_snapshot = metadata.get("threshold_snapshot", {})
        nuisance_snapshot = metadata.get("nuisance_profile_snapshot", {})
        bbox = payload.evidence_bbox if payload.evidence_bbox is not None else getattr(event.evidence, "bbox", None)
        status = str(metadata.get("status") or "processing")
        lifecycle_action = str(metadata.get("lifecycle_action") or "open")
        alarm_eligible = bool(metadata.get("alarm_eligible", True))
        is_alarm_active = bool(metadata.get("is_alarm_active", True))
        alarm_category = metadata.get("alarm_category", event.rule_id)

        row = Event(
            camera_id=event.camera_id,
            event_type=event.event_type,
            started_at=getattr(event, "timestamp_start", None),
            ended_at=getattr(event, "timestamp_end", None),
            track_id=getattr(event, "track_id", None),
            detector_score=float(metadata.get("track_quality", event.event_score) or event.event_score),
            confidence=event.event_score,
            event_score=event.event_score,
            details=event.explanation,
            snapshot_path=None,
            clip_path=None,
            bbox_json=event_snapshot_store.bbox_to_json(bbox),
            active_profile_snapshot=json.dumps(profile_snapshot, ensure_ascii=False, default=str),
            threshold_snapshot=json.dumps(threshold_snapshot, ensure_ascii=False, default=str),
            scene_profile=metadata.get("scene_profile"),
            camera_family=metadata.get("camera_family"),
            nuisance_profile_snapshot=json.dumps(nuisance_snapshot, ensure_ascii=False, default=str),
            roi_id=event.evidence.zone_id if event.evidence.zone_id and str(event.event_type).endswith("roi") else None,
            zone_id=event.evidence.zone_id,
            rule_id=event.rule_id,
            severity=event.priority,
            status=status,
            lifecycle_action=lifecycle_action,
            alarm_eligible=alarm_eligible,
            is_alarm_active=is_alarm_active,
            alarm_category=alarm_category,
            correlation_key=event.event_id,
        )

        # Se IA2 e IA3 ja concordaram, o evento nasce classificado e nao entra na
        # fila de validacao manual. Sem consenso, segue para o operador.
        ai_validation = derive_ai_validation(row)
        if ai_validation is not None:
            row.ai_validation_label = ai_validation.label
            row.ai_validation_reason = ai_validation.reason
            row.ai_validation_at = utc_now_naive()

        return row

    @staticmethod
    def _json_or_none(value: str | None) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except Exception:
            return value

    def _build_onedrive_event_payload(self, event_obj: Event, payload: PendingEventWrite) -> dict[str, Any]:
        event = payload.event
        metadata = getattr(event, "metadata", {}) or {}
        return {
            "schema_version": 1,
            "event": {
                "id": event_obj.id,
                "camera_id": event_obj.camera_id,
                "event_type": event_obj.event_type,
                "rule_id": event_obj.rule_id,
                "track_id": event_obj.track_id,
                "severity": event_obj.severity,
                "status": event_obj.status,
                "lifecycle_action": event_obj.lifecycle_action,
                "alarm_category": event_obj.alarm_category,
                "alarm_eligible": event_obj.alarm_eligible,
                "is_alarm_active": event_obj.is_alarm_active,
                "started_at": event_obj.started_at,
                "ended_at": event_obj.ended_at,
                "created_at": event_obj.created_at,
                "confidence": event_obj.confidence,
                "event_score": event_obj.event_score,
                "detector_score": event_obj.detector_score,
                "details": event_obj.details,
                "correlation_key": event_obj.correlation_key,
            },
            "evidence": {
                "bbox": self._json_or_none(event_obj.bbox_json),
                "zone_id": event_obj.zone_id,
                "roi_id": event_obj.roi_id,
                "scene_profile": event_obj.scene_profile,
                "camera_family": event_obj.camera_family,
            },
            "artifacts": {
                "snapshot_path": event_obj.snapshot_path,
                "clip_path": event_obj.clip_path,
                "clip_remote_web_url": event_obj.clip_remote_web_url,
                "snapshot_remote_web_url": event_obj.snapshot_remote_web_url,
            },
            "profile_snapshot": self._json_or_none(event_obj.active_profile_snapshot),
            "threshold_snapshot": self._json_or_none(event_obj.threshold_snapshot),
            "nuisance_profile_snapshot": self._json_or_none(event_obj.nuisance_profile_snapshot),
            "raw_metadata": metadata,
        }

    def _upload_onedrive_artifacts(
        self,
        *,
        event_obj: Event,
        payload: PendingEventWrite,
        snapshot_path: str | None,
        clip_path: str | None,
    ) -> None:
        if not onedrive_client.enabled():
            return

        event_id = int(event_obj.id)
        if snapshot_path:
            try:
                remote = onedrive_client.upload_audit_snapshot(
                    event_id=event_id,
                    snapshot_file=Path(snapshot_path),
                )
                event_obj.snapshot_remote_item_id = remote.get("item_id")
                event_obj.snapshot_remote_web_url = remote.get("web_url")
                event_obj.snapshot_remote_status = "uploaded"
                event_obj.snapshot_remote_uploaded_at = utc_now_naive()
            except Exception:
                event_obj.snapshot_remote_status = "failed"
                self.logger.exception(
                    "Event snapshot OneDrive upload failed event_id=%s",
                    getattr(event_obj, "id", "-"),
                    extra={
                        "camera_id": payload.camera_id,
                        "event_id": getattr(event_obj, "id", "-"),
                        "action": "event_snapshot_onedrive_upload_failed",
                        "reason": "onedrive_upload_failed",
                        "status": "degraded",
                    },
                )

        if clip_path:
            video_file = Path(clip_path) / "clip.mp4"
            if video_file.exists():
                try:
                    remote = onedrive_client.upload_audit_clip(event_id=event_id, clip_file=video_file)
                    event_obj.clip_remote_item_id = remote.get("item_id")
                    event_obj.clip_remote_web_url = remote.get("web_url")
                    event_obj.clip_remote_status = "uploaded"
                    event_obj.clip_remote_uploaded_at = utc_now_naive()
                except Exception:
                    event_obj.clip_remote_status = "failed"
                    self.logger.exception(
                        "Event clip OneDrive upload failed event_id=%s",
                        getattr(event_obj, "id", "-"),
                        extra={
                            "camera_id": payload.camera_id,
                            "event_id": getattr(event_obj, "id", "-"),
                            "action": "event_clip_onedrive_upload_failed",
                            "reason": "onedrive_upload_failed",
                            "status": "degraded",
                        },
                    )
                else:
                    try:
                        video_file.unlink(missing_ok=True)
                        event_obj.clip_local_deleted_at = utc_now_naive()
                    except Exception:
                        self.logger.exception(
                            "Event local clip cleanup failed event_id=%s",
                            getattr(event_obj, "id", "-"),
                            extra={
                                "camera_id": payload.camera_id,
                                "event_id": getattr(event_obj, "id", "-"),
                                "action": "event_clip_local_cleanup_failed",
                                "reason": "local_delete_failed",
                                "status": "degraded",
                            },
                        )

        try:
            remote = onedrive_client.upload_audit_event(
                event_id=event_id,
                event_payload=self._build_onedrive_event_payload(event_obj, payload),
            )
            event_obj.event_remote_item_id = remote.get("item_id")
            event_obj.event_remote_web_url = remote.get("web_url")
            event_obj.event_remote_status = "uploaded"
            event_obj.event_remote_uploaded_at = utc_now_naive()
        except Exception:
            event_obj.event_remote_status = "failed"
            self.logger.exception(
                "Event JSON OneDrive upload failed event_id=%s",
                getattr(event_obj, "id", "-"),
                extra={
                    "camera_id": payload.camera_id,
                    "event_id": getattr(event_obj, "id", "-"),
                    "action": "event_json_onedrive_upload_failed",
                    "reason": "onedrive_upload_failed",
                    "status": "degraded",
                },
            )

    def _persist_payload(self, payload: PendingEventWrite, *, inline_fallback: bool = False) -> bool:
        started = time.perf_counter()
        db: Session = SessionLocal()
        event_obj: Event | None = None
        try:
            event_obj = self._build_event_row(payload)
            db.add(event_obj)
            db.commit()
            db.refresh(event_obj)
            initialize_incident(event_obj, db)

            raw_frame = payload.clip_before_frame if payload.clip_before_frame is not None else payload.snapshot_frame
            annotated_frame = payload.clip_after_frame if payload.clip_after_frame is not None else payload.snapshot_frame

            snapshot_path = None
            clip_path = None

            if payload.snapshot_frame is not None:
                try:
                    snapshot_path = event_snapshot_store.save(
                        camera_id=payload.camera_id,
                        frame=payload.snapshot_frame,
                        event_type=payload.event.event_type,
                        track_id=payload.event.track_id,
                        bbox=payload.evidence_bbox if payload.evidence_bbox is not None else payload.event.evidence.bbox,
                    )
                except Exception:
                    self.logger.exception(
                        "Event snapshot failed event_type=%s",
                        payload.event.event_type,
                        extra={
                            "camera_id": payload.camera_id,
                            "event_id": getattr(event_obj, "id", "-"),
                            "action": "event_snapshot_failed",
                            "reason": "snapshot_write_failed",
                            "status": "degraded",
                        },
                    )

            try:
                clip_path = event_snapshot_store.save_clip_pair(
                    camera_id=payload.camera_id,
                    frame_before=raw_frame,
                    frame_after=annotated_frame,
                    event_type=payload.event.event_type,
                    track_id=payload.event.track_id,
                    bbox=payload.evidence_bbox if payload.evidence_bbox is not None else payload.event.evidence.bbox,
                    captured_at_before=payload.clip_before_captured_at,
                    captured_at_event=payload.snapshot_captured_at,
                    captured_at_after=payload.clip_after_captured_at,
                    video_frames=payload.clip_video_frames,
                    video_frame_offsets=payload.clip_video_frame_offsets,
                )
            except Exception:
                self.logger.exception(
                    "Event clip failed event_type=%s",
                    payload.event.event_type,
                    extra={
                        "camera_id": payload.camera_id,
                        "event_id": getattr(event_obj, "id", "-"),
                        "action": "event_clip_failed",
                        "reason": "clip_write_failed",
                        "status": "degraded",
                    },
                )

            event_obj.snapshot_path = snapshot_path
            event_obj.clip_path = clip_path
            final_status = str((getattr(payload.event, "metadata", {}) or {}).get("final_status") or "")
            event_obj.status = final_status or ("failed" if not snapshot_path and not clip_path else "persisted")
            self._upload_onedrive_artifacts(
                event_obj=event_obj,
                payload=payload,
                snapshot_path=snapshot_path,
                clip_path=clip_path,
            )
            db.commit()
            db.refresh(event_obj)
            if clip_path and not onedrive_client.enabled():
                prune_local_review_clips(db)
            db.commit()
            db.refresh(event_obj)
            maybe_prune_expired_events(db)

            if event_obj.status == "persisted" and bool(getattr(event_obj, "is_alarm_active", True)):
                try:
                    enqueue_event_notifications(event_obj, db)
                except Exception:
                    db.rollback()
                    self.logger.exception(
                        "Notification enqueue hook failed event_type=%s",
                        payload.event.event_type,
                        extra={
                            "camera_id": payload.camera_id,
                            "event_id": getattr(event_obj, "id", "-"),
                            "action": "notification_enqueue_failed",
                            "reason": "unexpected_service_failure",
                            "status": "degraded",
                        },
                    )
                try:
                    send_event_if_needed(event_obj, db)
                except Exception:
                    self.logger.exception(
                        "Lockdown ingest hook failed event_type=%s",
                        payload.event.event_type,
                        extra={
                            "camera_id": payload.camera_id,
                            "event_id": getattr(event_obj, "id", "-"),
                            "action": "lockdown_ingest_hook_failed",
                            "reason": "unexpected_service_failure",
                            "status": "degraded",
                        },
                    )

                try:
                    event_payload = serialize_event_for_table(event_obj)
                    event_broadcaster.broadcast_event_sync(event_payload)
                except Exception:
                    self.logger.exception("Falha no broadcast SSE do evento id=%s", getattr(event_obj, "id", "-"))

            with self._stats_lock:
                if event_obj.status == "failed":
                    self._stats.events_failed += 1
                else:
                    self._stats.events_persisted += 1
                self._stats.queue_size = self._queue.qsize()
            self.logger.info(
                "Event persistence completed event_id=%s status=%s snapshot=%s clip=%s inline_fallback=%s",
                getattr(event_obj, "id", None),
                event_obj.status,
                bool(snapshot_path),
                bool(clip_path),
                inline_fallback,
                extra={
                    "action": "event_persistence_completed",
                    "status": event_obj.status,
                    "reason": "persisted",
                },
            )
            return event_obj.status in {"persisted", "canceled"}
        except Exception:
            db.rollback()
            if event_obj is not None and getattr(event_obj, "id", None) is not None:
                try:
                    db.query(Event).filter(Event.id == event_obj.id).update({"status": "failed"})
                    db.commit()
                except Exception:
                    db.rollback()
            self._mark_failed(reason="persist_job_failed", event_id=getattr(event_obj, "id", None))
            return False
        finally:
            self._record_latency((time.perf_counter() - started) * 1000.0)
            db.close()


__all__ = [
    "EventPersistenceQueue",
    "EventPersistenceStats",
    "PendingEventWrite",
]
