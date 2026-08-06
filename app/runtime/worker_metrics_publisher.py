"""Serializacao e persistencia do snapshot de metricas do worker."""

from __future__ import annotations

import time
from typing import Optional

import psutil

from app.core.config import settings
from app.services.metrics_store import metrics_store


class WorkerMetricsPublisher:
    def __init__(self):
        self.process = psutil.Process()
        self.last_metrics_ts = 0.0

    @staticmethod
    def _aux_inference_state(camera_id: int) -> dict:
        """Estado de execucao de IA2/IA3 por camera (Etapa 3).

        Nunca expoe crops ou payloads; falha de leitura nao pode derrubar a
        publicacao de metricas do worker.
        """
        try:
            from app.analytics_v2.revalidation.aux_inference_client import (
                camera_execution_state,
            )

            return camera_execution_state(camera_id)
        except Exception:
            return {
                "ia2_execution_mode": "local",
                "ia3_execution_mode": "local",
            }

    def _serialize_tracks(
        self,
        tracks: list[dict] | None,
        limit: int = 20,
    ) -> list[dict]:
        serialized: list[dict] = []
        for track in (tracks or [])[:limit]:
            bbox = track.get("bbox") if isinstance(track, dict) else None
            if not bbox or len(bbox) != 4:
                continue
            try:
                cleaned_bbox = [round(float(value), 2) for value in bbox]
            except Exception:
                continue
            item = {"bbox": cleaned_bbox}
            try:
                track_id = track.get("track_id")
                if track_id is not None:
                    item["track_id"] = int(track_id)
            except Exception:
                pass
            try:
                confidence = track.get("confidence")
                if confidence is not None:
                    item["confidence"] = round(float(confidence), 4)
            except Exception:
                pass
            if track.get("label"):
                item["label"] = str(track.get("label"))
            if track.get("visual_status"):
                item["visual_status"] = str(track.get("visual_status"))
            if track.get("visual_person_score") is not None:
                try:
                    item["visual_person_score"] = round(
                        float(track.get("visual_person_score")),
                        4,
                    )
                except Exception:
                    pass
            if track.get("notification_decision"):
                item["notification_decision"] = str(
                    track.get("notification_decision")
                )
            if track.get("strategy3_v2_decision"):
                item["strategy3_v2_decision"] = str(
                    track.get("strategy3_v2_decision")
                )
            serialized.append(item)
        return serialized

    def publish(
        self,
        camera_id: int,
        *,
        read_ms: float,
        infer_ms: float,
        plot_ms: float,
        jpeg_ms: float,
        loop_ms: float,
        current_fps: float,
        raw_fps: float | None = None,
        processed_fps: float | None = None,
        frame,
        infer_input_width: int,
        infer_input_height: int,
        tracks_count: int,
        tracks: list[dict] | None = None,
        reconnect_count: int,
        dropped_frames_count: int,
        last_successful_inference_at,
        last_frame_at=None,
        last_processed_frame_at=None,
        last_metrics_at=None,
        health_status: str | None = None,
        consecutive_stall_checks: int | None = None,
        roi_polygon=None,
        roi_name: Optional[str] = None,
        roi_crop_active: bool = False,
        roi_crop_meta=None,
        line_pixels=None,
        line_direction: str = "any",
        worker_mode: str = "normal",
        worker_generation: str | None = None,
        capture_source: str | None = None,
        gateway_fallback_active: bool | None = None,
        gateway_recovery_count: int | None = None,
        gateway_recovery_last_success_at=None,
        gateway_fallback_started_at=None,
        motion_info: dict | None = None,
        frame_width: int | None = None,
        frame_height: int | None = None,
        source_frame_width: int | None = None,
        source_frame_height: int | None = None,
        display_frame_width: int | None = None,
        display_frame_height: int | None = None,
        capture_queue_dropped_frames: int | None = None,
        event_persistence_queue_size: int | None = None,
        event_persistence_events_queued: int | None = None,
        event_persistence_events_persisted: int | None = None,
        event_persistence_events_failed: int | None = None,
        event_persistence_dropped_or_rejected_jobs: int | None = None,
        event_persistence_latency_ms: float | None = None,
        event_persistence_last_latency_ms: float | None = None,
        raw_frames_published: int | None = None,
        processed_frames_published: int | None = None,
        raw_frames_skipped_by_throttle: int | None = None,
        processed_frames_skipped_by_throttle: int | None = None,
        jpeg_encode_count: int | None = None,
        jpeg_encode_time_ms: float | None = None,
        overlay_render_count: int | None = None,
        overlay_render_time_ms: float | None = None,
        effective_raw_publish_fps: float | None = None,
        effective_processed_publish_fps: float | None = None,
        visual_queue_size: int | None = None,
        visual_jobs_dropped: int | None = None,
        inference_result_age_ms: float | None = None,
        visual_tracks_stale: bool | None = None,
        inference_runtime: dict | None = None,
        frame_transport_metrics: dict | None = None,
    ):
        now = time.perf_counter()
        if now - self.last_metrics_ts < 1.0:
            return
        mem = self.process.memory_info()
        measured_frame_width = int(frame.shape[1])
        measured_frame_height = int(frame.shape[0])
        effective_frame_width = (
            int(frame_width) if frame_width is not None else measured_frame_width
        )
        effective_frame_height = (
            int(frame_height) if frame_height is not None else measured_frame_height
        )
        effective_source_frame_width = (
            int(source_frame_width)
            if source_frame_width is not None
            else effective_frame_width
        )
        effective_source_frame_height = (
            int(source_frame_height)
            if source_frame_height is not None
            else effective_frame_height
        )
        effective_display_frame_width = (
            int(display_frame_width)
            if display_frame_width is not None
            else effective_frame_width
        )
        effective_display_frame_height = (
            int(display_frame_height)
            if display_frame_height is not None
            else effective_frame_height
        )
        safe_roi_polygon = roi_polygon or []
        runtime = inference_runtime or {}
        transport = frame_transport_metrics or {}

        data = {
            "fps": round(current_fps, 2),
            "raw_fps": round(float(raw_fps), 2) if raw_fps is not None else round(current_fps, 2),
            "processed_fps": round(float(processed_fps), 2) if processed_fps is not None else round(current_fps, 2),
            "read_ms": round(read_ms, 2),
            "infer_ms": round(infer_ms, 2),
            "plot_ms": round(plot_ms, 2),
            "jpeg_ms": round(jpeg_ms, 2),
            "loop_ms": round(loop_ms, 2),
            "process_cpu_percent": self.process.cpu_percent(interval=None),
            "system_cpu_percent": psutil.cpu_percent(interval=None),
            "process_rss_mb": round(mem.rss / (1024 * 1024), 2),
            "system_ram_percent": psutil.virtual_memory().percent,
            "frame_width": effective_frame_width,
            "frame_height": effective_frame_height,
            "source_frame_width": effective_source_frame_width,
            "source_frame_height": effective_source_frame_height,
            "display_frame_width": effective_display_frame_width,
            "display_frame_height": effective_display_frame_height,
            "capture_queue_dropped_frames": int(capture_queue_dropped_frames or 0),
            "event_persistence_queue_size": int(event_persistence_queue_size or 0),
            "event_persistence_events_queued": int(event_persistence_events_queued or 0),
            "event_persistence_events_persisted": int(event_persistence_events_persisted or 0),
            "event_persistence_events_failed": int(event_persistence_events_failed or 0),
            "event_persistence_dropped_or_rejected_jobs": int(event_persistence_dropped_or_rejected_jobs or 0),
            "event_persistence_latency_ms": round(float(event_persistence_latency_ms or 0.0), 2),
            "event_persistence_last_latency_ms": round(float(event_persistence_last_latency_ms or 0.0), 2),
            "raw_frames_published": int(raw_frames_published or 0),
            "processed_frames_published": int(processed_frames_published or 0),
            "raw_frames_skipped_by_throttle": int(raw_frames_skipped_by_throttle or 0),
            "processed_frames_skipped_by_throttle": int(processed_frames_skipped_by_throttle or 0),
            "jpeg_encode_count": int(jpeg_encode_count or 0),
            "jpeg_encode_time_ms": round(float(jpeg_encode_time_ms or 0.0), 2),
            "overlay_render_count": int(overlay_render_count or 0),
            "overlay_render_time_ms": round(float(overlay_render_time_ms or 0.0), 2),
            "effective_raw_publish_fps": round(float(effective_raw_publish_fps or 0.0), 2),
            "effective_processed_publish_fps": round(float(effective_processed_publish_fps or 0.0), 2),
            "visual_queue_size": int(visual_queue_size or 0),
            "visual_jobs_dropped": int(visual_jobs_dropped or 0),
            "inference_result_age_ms": round(float(inference_result_age_ms), 2) if inference_result_age_ms is not None else None,
            "visual_tracks_stale": bool(visual_tracks_stale),
            "detector_fp16_enabled": bool(settings.detector_fp16_enabled),
            "inference_pool_enabled": bool(settings.inference_pool_enabled),
            "inference_pool_mode": str(runtime.get("mode") or ("pool" if settings.inference_pool_enabled else "direct")),
            "inference_pool_backend": str(runtime.get("backend") or settings.inference_pool_backend),
            "inference_pool_id": runtime.get("pool_id"),
            "inference_pool_count": int(runtime.get("pool_count") or settings.inference_pool_count),
            "inference_pool_assigned_cameras": int(runtime.get("assigned_cameras") or 0),
            "inference_pool_total_assigned_cameras": int(runtime.get("total_assigned_cameras") or 0),
            "inference_pool_max_cameras_per_pool": int(runtime.get("max_cameras_per_pool") or settings.inference_pool_max_cameras_per_pool),
            "inference_pool_queue_size": int(runtime.get("queue_size") or 0),
            "inference_pool_active_camera_id": runtime.get("active_camera_id"),
            "inference_pool_submitted": int(runtime.get("submitted") or 0),
            "inference_pool_completed": int(runtime.get("completed") or 0),
            "inference_pool_failed": int(runtime.get("failed") or 0),
            "inference_pool_timed_out": int(runtime.get("timed_out") or 0),
            "inference_pool_replaced": int(runtime.get("replaced") or 0),
            "inference_pool_rejected": int(runtime.get("rejected") or 0),
            "inference_pool_dropped_oldest": int(runtime.get("dropped_oldest") or 0),
            "inference_pool_stale_dropped": int(runtime.get("stale_dropped") or 0),
            "inference_pool_last_wait_ms": round(float(runtime.get("last_wait_ms") or 0.0), 2),
            "inference_pool_last_total_latency_ms": round(float(runtime.get("last_total_latency_ms") or 0.0), 2),
            "inference_pool_last_infer_ms": round(float(runtime.get("last_infer_ms") or 0.0), 2),
            "inference_pool_central_http_ms": round(float(runtime.get("central_http_ms") or 0.0), 2),
            "inference_pool_central_jpeg_quality": int(runtime.get("central_jpeg_quality") or settings.inference_pool_central_jpeg_quality),
            "inference_transport_mode": str(
                runtime.get("inference_transport_mode") or "http"
            ),
            "inference_jobs_submitted_total": int(
                runtime.get("inference_jobs_submitted_total") or 0
            ),
            "inference_payload_bytes_total": int(
                runtime.get("inference_payload_bytes_total") or 0
            ),
            "inference_transport_latency_ms": round(
                float(runtime.get("inference_transport_latency_ms") or 0.0), 3
            ),
            "inference_transport_fallback_total": int(
                runtime.get("inference_transport_fallback_total") or 0
            ),
            "inference_transport_errors_total": int(
                runtime.get("inference_transport_errors_total") or 0
            ),
            **self._aux_inference_state(camera_id),
            "inference_pool_overflow_policy": str(runtime.get("overflow_policy") or settings.inference_pool_overflow_policy),
            "inference_pool_max_job_age_seconds": float(runtime.get("max_job_age_seconds") or settings.inference_pool_max_job_age_seconds),
            "infer_input_width": int(infer_input_width),
            "infer_input_height": int(infer_input_height),
            "tracks_count": tracks_count,
            "latest_tracks": self._serialize_tracks(tracks),
            "reconnect_count": reconnect_count,
            "dropped_frames_count": dropped_frames_count,
            "last_successful_inference_at": last_successful_inference_at,
            "last_frame_at": last_frame_at,
            "last_processed_frame_at": last_processed_frame_at,
            "last_metrics_at": last_metrics_at,
            "health_status": health_status,
            "consecutive_stall_checks": consecutive_stall_checks,
            "detector_model_path": settings.detector_model_path,
            "track_exit_timeout_seconds": settings.track_exit_timeout_seconds,
            "roi_enabled": len(safe_roi_polygon) >= 3,
            "roi_name": roi_name,
            "roi_crop_active": roi_crop_active,
            "roi_crop_meta": roi_crop_meta,
            "line_enabled": bool(line_pixels),
            "line_direction": line_direction,
            "worker_mode": worker_mode,
            "worker_generation": str(worker_generation or ""),
            "capture_source": capture_source or "-",
            "gateway_fallback_active": bool(gateway_fallback_active),
            "gateway_recovery_count": int(gateway_recovery_count or 0),
            "gateway_recovery_last_success_at": gateway_recovery_last_success_at,
            "gateway_fallback_started_at": gateway_fallback_started_at,
            "frame_transport_mode": str(
                transport.get("frame_transport_mode") or "http"
            ),
            "shared_buffer_frames_read_total": int(
                transport.get("shared_buffer_frames_read_total") or 0
            ),
            "shared_buffer_frames_skipped_total": int(
                transport.get("shared_buffer_frames_skipped_total") or 0
            ),
            "shared_buffer_corrupt_frames_total": int(
                transport.get("shared_buffer_corrupt_frames_total") or 0
            ),
            "shared_buffer_generation_changes_total": int(
                transport.get("shared_buffer_generation_changes_total") or 0
            ),
            "shared_buffer_read_latency_ms": round(
                float(transport.get("shared_buffer_read_latency_ms") or 0.0), 3
            ),
            "shared_buffer_wait_ms": round(
                float(transport.get("shared_buffer_wait_ms") or 0.0), 3
            ),
            "shared_buffer_frame_age_ms": round(
                float(transport.get("shared_buffer_frame_age_ms") or 0.0), 3
            ),
            "shared_buffer_generation": int(
                transport.get("shared_buffer_generation") or 0
            ),
            "shared_buffer_last_frame_id": int(
                transport.get("shared_buffer_last_frame_id") or 0
            ),
            "frame_transport_http_fallback_total": int(
                transport.get("frame_transport_http_fallback_total") or 0
            ),
            "frame_transport_errors_total": int(
                transport.get("frame_transport_errors_total") or 0
            ),
            "worker_pid": self.process.pid,
        }
        if motion_info:
            data.update(
                {
                    "motion_detected": bool(motion_info.get("motion_detected", False)),
                    "motion_score": round(float(motion_info.get("motion_score", 0.0)), 2),
                    "motion_ratio": round(float(motion_info.get("motion_ratio", 0.0)), 6),
                    "global_change_ratio": round(float(motion_info.get("global_change_ratio", 0.0)), 6),
                    "gate_state": motion_info.get("state", "idle"),
                }
            )
        payload = metrics_store.set_metrics(camera_id, data)
        self.last_metrics_ts = now
        return payload
