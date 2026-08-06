"""Leitura de frames, snapshots, overlays e geracao MJPEG para cameras."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timezone
import time

import cv2
import numpy as np

from app.services.camera_registry import registry
from app.services.frame_store import frame_store
from app.services.preview_stream import preview_stream_manager
from app.services.camera_gateway_client import resolve_camera_gateway_source_url
from app.services.media_backbone_service import MediaBackboneUnavailable
from app.services.runtime_client import fetch_runtime_camera_frame, remote_runtime_enabled

def render_tracks_on_jpeg(jpeg_bytes, *args, **kwargs):
    return jpeg_bytes

class DummyTrackStore:
    def get_tracks(self, *args, **kwargs): return []

track_store = DummyTrackStore()


STREAM_STALE_MAX_AGE_SECONDS = 12.0
SNAPSHOT_PREVIEW_TIMEOUT_SECONDS = 5.0
MJPEG_MEDIA_TYPE = "multipart/x-mixed-replace; boundary=frame"


def frame_to_jpeg_bytes(frame) -> bytes | None:
    if frame is None:
        return None
    ok, buffer = cv2.imencode(".jpg", frame)
    return buffer.tobytes() if ok else None


def frame_metadata_age_seconds(metadata: dict | None) -> float | None:
    if not metadata:
        return None
    updated_at = metadata.get("updated_at")
    if not isinstance(updated_at, datetime):
        return None
    try:
        now = datetime.now(timezone.utc)
        if updated_at.tzinfo is None:
            now = now.replace(tzinfo=None)
        return max(0.0, (now - updated_at).total_seconds())
    except Exception:
        return None


def build_stream_status_frame(
    title: str,
    message: str = "",
    width: int = 960,
    height: int = 540,
):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (18, 18, 18)
    cv2.rectangle(frame, (0, 0), (width - 1, height - 1), (60, 60, 60), 2)
    cv2.putText(
        frame,
        title,
        (40, 90),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.2,
        (0, 180, 255),
        3,
        cv2.LINE_AA,
    )
    if message:
        safe_message = str(message)
        if len(safe_message) > 90:
            safe_message = safe_message[:87] + "..."
        cv2.putText(
            frame,
            safe_message,
            (40, 145),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (220, 220, 220),
            2,
            cv2.LINE_AA,
        )
    cv2.putText(
        frame,
        "O mosaico continua ativo. O sistema vai tentar reconectar automaticamente.",
        (40, height - 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (180, 180, 180),
        2,
        cv2.LINE_AA,
    )
    return frame


def encode_mjpeg_chunk(frame) -> bytes | None:
    jpg_bytes = frame_to_jpeg_bytes(frame)
    return mjpeg_chunk(jpg_bytes) if jpg_bytes else None


def mjpeg_chunk(jpg_bytes: bytes | None) -> bytes | None:
    if not jpg_bytes:
        return None
    return b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg_bytes + b"\r\n"


def get_raw_stream_bytes(camera_id: int) -> bytes | None:
    if remote_runtime_enabled():
        return fetch_runtime_camera_frame(camera_id, "raw")
    if hasattr(frame_store, "get_raw_jpeg"):
        try:
            age_seconds = frame_metadata_age_seconds(frame_store.get_raw_frame_metadata(camera_id))
            if age_seconds is not None and age_seconds > STREAM_STALE_MAX_AGE_SECONDS:
                return frame_to_jpeg_bytes(
                    build_stream_status_frame(
                        "SEM FRAME RECENTE",
                        f"Ultimo frame ha {age_seconds:.1f}s.",
                    )
                )
            jpg_bytes = frame_store.get_raw_jpeg(camera_id)
            if jpg_bytes:
                return jpg_bytes
        except Exception:
            pass
    try:
        frame = frame_store.get_raw_frame(camera_id)
    except Exception:
        return None
    return frame_to_jpeg_bytes(frame)


def get_processed_stream_bytes(camera_id: int) -> bytes | None:
    if remote_runtime_enabled():
        jpg_bytes = fetch_runtime_camera_frame(camera_id, "processed")
        if jpg_bytes:
            return jpg_bytes
        return frame_to_jpeg_bytes(
            build_stream_status_frame(
                "SEM FRAME PROCESSADO",
                "Aguardando o runtime publicar o ultimo frame.",
            )
        )
    if hasattr(frame_store, "get_processed_jpeg"):
        try:
            age_seconds = frame_metadata_age_seconds(
                frame_store.get_processed_frame_metadata(camera_id)
            )
            if age_seconds is not None and age_seconds > STREAM_STALE_MAX_AGE_SECONDS:
                return frame_to_jpeg_bytes(
                    build_stream_status_frame(
                        "SEM FRAME PROCESSADO RECENTE",
                        f"Ultimo frame ha {age_seconds:.1f}s.",
                    )
                )
            jpg_bytes = frame_store.get_processed_jpeg(camera_id)
            if jpg_bytes:
                return jpg_bytes
        except Exception:
            pass
    try:
        frame = frame_store.get_processed_frame(camera_id)
    except Exception:
        frame = None
    if frame is None:
        return frame_to_jpeg_bytes(
            build_stream_status_frame(
                "SEM FRAME PROCESSADO",
                "Aguardando o analítico publicar o último frame.",
            )
        )
    return frame_to_jpeg_bytes(frame)


def get_boxed_stream_bytes(camera_id: int, minimum_confidence: float) -> bytes | None:
    jpg_bytes = get_raw_stream_bytes(camera_id) or get_processed_stream_bytes(camera_id)
    if not jpg_bytes:
        return jpg_bytes
    payload = track_store.get_tracks(camera_id, max_age_seconds=1.8)
    if payload:
        filtered_payload = dict(payload)
        filtered_tracks = []
        for track in payload.get("tracks") or []:
            if not isinstance(track, dict):
                continue
            try:
                if float(track.get("confidence")) < minimum_confidence:
                    continue
            except (TypeError, ValueError):
                continue
            filtered_tracks.append(track)
        filtered_payload["tracks"] = filtered_tracks
        payload = filtered_payload
    return render_tracks_on_jpeg(jpg_bytes, payload)


def get_camera_snapshot_bytes(camera_id: int, rtsp_url: str | None) -> bytes | None:
    if remote_runtime_enabled():
        jpg_bytes = fetch_runtime_camera_frame(camera_id, "raw") or fetch_runtime_camera_frame(
            camera_id, "processed"
        )
        if jpg_bytes:
            return jpg_bytes
    if registry.get_worker(camera_id) is not None:
        return get_raw_stream_bytes(camera_id)
    if not rtsp_url:
        return frame_to_jpeg_bytes(
            build_stream_status_frame(
                "SNAPSHOT INDISPONIVEL",
                "A camera nao possui RTSP configurado.",
            )
        )
    try:
        preview_source = resolve_camera_gateway_source_url(camera_id, rtsp_url)
    except MediaBackboneUnavailable:
        raise
    preview_stream_manager.acquire(camera_id, preview_source)
    try:
        # Um source on-demand H.265 pode levar alguns segundos para negociar a
        # origem e produzir o primeiro keyframe.
        deadline = time.monotonic() + SNAPSHOT_PREVIEW_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            jpg_bytes = preview_stream_manager.get_jpeg(camera_id)
            if jpg_bytes:
                return jpg_bytes
            time.sleep(0.05)
        return frame_to_jpeg_bytes(
            build_stream_status_frame(
                "SNAPSHOT CONECTANDO",
                "Aguardando o primeiro frame.",
            )
        )
    finally:
        preview_stream_manager.release(camera_id)


def generate_mjpeg_bytes(
    get_bytes_fn: Callable[[int], bytes | None],
    camera_id: int,
) -> Iterator[bytes]:
    placeholder_bytes = None
    while True:
        jpg_bytes = get_bytes_fn(camera_id)
        if not jpg_bytes:
            if placeholder_bytes is None:
                placeholder_bytes = frame_to_jpeg_bytes(
                    build_stream_status_frame(
                        "STREAM INDISPONÍVEL",
                        "Aguardando publicação de frame.",
                    )
                )
            jpg_bytes = placeholder_bytes
            if not jpg_bytes:
                time.sleep(0.2)
                continue
        chunk = mjpeg_chunk(jpg_bytes)
        if chunk:
            yield chunk
        time.sleep(0.2 if remote_runtime_enabled() or jpg_bytes == placeholder_bytes else 0.03)


def generate_camera_raw_mjpeg(camera_id: int, rtsp_url: str) -> Iterator[bytes]:
    preview_acquired = False
    waiting_frame = build_stream_status_frame(
        "CONECTANDO PREVIEW",
        "Aguardando frames da câmera...",
    )
    try:
        try:
            preview_source = resolve_camera_gateway_source_url(camera_id, rtsp_url)
        except MediaBackboneUnavailable as exc:
            preview_source = ""
            waiting_frame = build_stream_status_frame("BACKBONE DE MIDIA INDISPONIVEL", exc.code)
        while True:
            if remote_runtime_enabled():
                jpg_bytes = get_raw_stream_bytes(camera_id)
                if jpg_bytes:
                    if preview_acquired:
                        preview_stream_manager.release(camera_id)
                        preview_acquired = False
                    chunk = mjpeg_chunk(jpg_bytes)
                    if chunk:
                        yield chunk
                    time.sleep(0.2)
                    continue
                if not preview_acquired and preview_source:
                    preview_stream_manager.acquire(camera_id, preview_source)
                    preview_acquired = True
                jpg_bytes = (
                    preview_stream_manager.get_jpeg(camera_id)
                    if preview_acquired
                    else None
                )
                if not jpg_bytes:
                    chunk = encode_mjpeg_chunk(waiting_frame)
                    if chunk:
                        yield chunk
                    time.sleep(0.2)
                    continue
                chunk = mjpeg_chunk(jpg_bytes)
                if chunk:
                    yield chunk
                time.sleep(0.03)
                continue

            worker = registry.get_worker(camera_id)
            if worker is not None:
                if preview_acquired:
                    preview_stream_manager.stop(camera_id)
                    preview_acquired = False
                jpg_bytes = get_raw_stream_bytes(camera_id)
            else:
                if not preview_acquired:
                    if not preview_source:
                        chunk = encode_mjpeg_chunk(waiting_frame)
                        if chunk:
                            yield chunk
                        time.sleep(0.2)
                        continue
                    preview_stream_manager.acquire(camera_id, preview_source)
                    preview_acquired = True
                jpg_bytes = preview_stream_manager.get_jpeg(camera_id)
            if not jpg_bytes:
                chunk = encode_mjpeg_chunk(waiting_frame)
                if chunk:
                    yield chunk
                time.sleep(0.2)
                continue
            chunk = mjpeg_chunk(jpg_bytes)
            if chunk:
                yield chunk
            time.sleep(0.03)
    finally:
        if preview_acquired:
            preview_stream_manager.release(camera_id)


def generate_status_mjpeg(title: str, message: str = "") -> Iterator[bytes]:
    frame = build_stream_status_frame(title, message)
    while True:
        chunk = encode_mjpeg_chunk(frame)
        if chunk:
            yield chunk
        time.sleep(0.5)
