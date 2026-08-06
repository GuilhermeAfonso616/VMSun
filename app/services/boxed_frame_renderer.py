from __future__ import annotations

from typing import Any

import cv2
import numpy as np


BOX_COLOR = (255, 96, 24)
LABEL_BACKGROUND = (210, 72, 18)


def map_bbox_to_frame(
    bbox: list[float] | tuple[float, float, float, float],
    *,
    source_width: int,
    source_height: int,
    frame_width: int,
    frame_height: int,
) -> tuple[int, int, int, int] | None:
    if (
        len(bbox) != 4
        or source_width <= 0
        or source_height <= 0
        or frame_width <= 0
        or frame_height <= 0
    ):
        return None

    try:
        x1, y1, x2, y2 = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return None

    scale = min(frame_width / source_width, frame_height / source_height)
    content_width = source_width * scale
    content_height = source_height * scale
    offset_x = (frame_width - content_width) / 2.0
    offset_y = (frame_height - content_height) / 2.0

    mx1 = int(round(offset_x + x1 * scale))
    my1 = int(round(offset_y + y1 * scale))
    mx2 = int(round(offset_x + x2 * scale))
    my2 = int(round(offset_y + y2 * scale))
    mx1 = max(0, min(frame_width - 1, mx1))
    my1 = max(0, min(frame_height - 1, my1))
    mx2 = max(0, min(frame_width - 1, mx2))
    my2 = max(0, min(frame_height - 1, my2))
    if mx2 <= mx1 or my2 <= my1:
        return None
    return mx1, my1, mx2, my2


def render_tracks_on_frame(frame: np.ndarray, payload: dict[str, Any] | None) -> np.ndarray:
    if frame is None or not payload:
        return frame

    frame_height, frame_width = frame.shape[:2]
    source_width = int(payload.get("source_frame_width") or frame_width)
    source_height = int(payload.get("source_frame_height") or frame_height)
    tracks = payload.get("tracks") or []
    line_thickness = max(2, int(round(min(frame_width, frame_height) / 270)))
    font_scale = max(0.45, min(frame_width, frame_height) / 900)
    text_thickness = max(1, line_thickness - 1)

    for track in tracks[:30]:
        if not isinstance(track, dict):
            continue
        bbox = track.get("bbox")
        if not isinstance(bbox, (list, tuple)):
            continue
        mapped = map_bbox_to_frame(
            bbox,
            source_width=source_width,
            source_height=source_height,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        if mapped is None:
            continue

        x1, y1, x2, y2 = mapped
        cv2.rectangle(frame, (x1, y1), (x2, y2), BOX_COLOR, line_thickness, cv2.LINE_AA)

        label = str(track.get("label") or "person")
        track_id = track.get("track_id")
        try:
            if track_id is not None and int(track_id) >= 0:
                label += f" #{int(track_id)}"
        except (TypeError, ValueError):
            pass
        try:
            if track.get("confidence") is not None:
                label += f" {float(track['confidence']):.2f}"
        except (TypeError, ValueError):
            pass

        (text_width, text_height), baseline = cv2.getTextSize(
            label,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            text_thickness,
        )
        label_top = max(0, y1 - text_height - baseline - 8)
        label_bottom = min(frame_height - 1, label_top + text_height + baseline + 8)
        label_right = min(frame_width - 1, x1 + text_width + 10)
        cv2.rectangle(frame, (x1, label_top), (label_right, label_bottom), LABEL_BACKGROUND, -1)
        cv2.putText(
            frame,
            label,
            (x1 + 5, label_bottom - baseline - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 255),
            text_thickness,
            cv2.LINE_AA,
        )

    return frame


def render_tracks_on_jpeg(jpg_bytes: bytes | None, payload: dict[str, Any] | None) -> bytes | None:
    if not jpg_bytes:
        return jpg_bytes
    encoded = np.frombuffer(jpg_bytes, dtype=np.uint8)
    frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if frame is None:
        return jpg_bytes
    render_tracks_on_frame(frame, payload)
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buffer.tobytes() if ok else jpg_bytes
