"""Transformacoes de resize/letterbox usadas no preview e no editor de ROI.

O display padrao do projeto usa canvas fixo com padding; por isso as funcoes
aqui convertem entre o espaco fonte e o espaco exibido de forma explicita.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


DISPLAY_FRAME_WIDTH = 960
DISPLAY_FRAME_HEIGHT = 540


@dataclass(frozen=True)
class DisplayResizeConfig:
    width: int = DISPLAY_FRAME_WIDTH
    height: int = DISPLAY_FRAME_HEIGHT
    background_color: tuple[int, int, int] = (0, 0, 0)
    scale_up: bool = True


@dataclass(frozen=True)
class LetterboxTransform:
    source_width: int
    source_height: int
    target_width: int
    target_height: int
    scale: float
    content_width: int
    content_height: int
    offset_x: int
    offset_y: int


def build_letterbox_transform(
    source_width: int,
    source_height: int,
    target_width: int = DISPLAY_FRAME_WIDTH,
    target_height: int = DISPLAY_FRAME_HEIGHT,
) -> LetterboxTransform:
    source_width = max(1, int(source_width))
    source_height = max(1, int(source_height))
    target_width = max(1, int(target_width))
    target_height = max(1, int(target_height))

    # Letterbox preserva aspecto e registra o retangulo util dentro do canvas final.
    scale = min(target_width / source_width, target_height / source_height)
    content_width = max(1, int(round(source_width * scale)))
    content_height = max(1, int(round(source_height * scale)))
    offset_x = int(round((target_width - content_width) / 2.0))
    offset_y = int(round((target_height - content_height) / 2.0))

    return LetterboxTransform(
        source_width=source_width,
        source_height=source_height,
        target_width=target_width,
        target_height=target_height,
        scale=scale,
        content_width=content_width,
        content_height=content_height,
        offset_x=offset_x,
        offset_y=offset_y,
    )


def source_normalized_point_to_display(point: dict | tuple[float, float], source_width: int, source_height: int, target_width: int = DISPLAY_FRAME_WIDTH, target_height: int = DISPLAY_FRAME_HEIGHT) -> tuple[float, float] | None:
    if point is None:
        return None

    try:
        if isinstance(point, dict):
            normalized_x = float(point["x"])
            normalized_y = float(point["y"])
        else:
            normalized_x = float(point[0])
            normalized_y = float(point[1])
    except Exception:
        return None

    transform = build_letterbox_transform(source_width, source_height, target_width, target_height)
    x = transform.offset_x + (normalized_x * transform.content_width)
    y = transform.offset_y + (normalized_y * transform.content_height)
    return x, y


def display_point_to_source_normalized(point: tuple[float, float], source_width: int, source_height: int, target_width: int = DISPLAY_FRAME_WIDTH, target_height: int = DISPLAY_FRAME_HEIGHT) -> tuple[float, float] | None:
    if point is None:
        return None

    try:
        display_x = float(point[0])
        display_y = float(point[1])
    except Exception:
        return None

    transform = build_letterbox_transform(source_width, source_height, target_width, target_height)
    local_x = display_x - transform.offset_x
    local_y = display_y - transform.offset_y

    # Cliques fora da area util da imagem sao ignorados para evitar ROI em padding.
    if local_x < 0 or local_y < 0 or local_x > transform.content_width or local_y > transform.content_height:
        return None

    source_x = local_x / max(1.0, float(transform.content_width))
    source_y = local_y / max(1.0, float(transform.content_height))
    return (
        max(0.0, min(1.0, source_x)),
        max(0.0, min(1.0, source_y)),
    )


def display_normalized_point_to_source_normalized(point: dict | tuple[float, float], source_width: int, source_height: int, target_width: int = DISPLAY_FRAME_WIDTH, target_height: int = DISPLAY_FRAME_HEIGHT) -> tuple[float, float] | None:
    if point is None:
        return None

    try:
        if isinstance(point, dict):
            normalized_x = float(point["x"])
            normalized_y = float(point["y"])
        else:
            normalized_x = float(point[0])
            normalized_y = float(point[1])
    except Exception:
        return None

    display_x = normalized_x * float(target_width)
    display_y = normalized_y * float(target_height)
    return display_point_to_source_normalized((display_x, display_y), source_width, source_height, target_width, target_height)


def display_normalized_polygon_to_source_normalized(
    points: list[dict] | list[tuple[float, float]],
    source_width: int,
    source_height: int,
    target_width: int = DISPLAY_FRAME_WIDTH,
    target_height: int = DISPLAY_FRAME_HEIGHT,
) -> list[dict]:
    converted: list[dict] = []
    for point in points or []:
        normalized = display_normalized_point_to_source_normalized(point, source_width, source_height, target_width, target_height)
        if normalized is None:
            continue
        converted.append({"x": float(normalized[0]), "y": float(normalized[1])})
    return converted


def display_normalized_line_to_source_normalized(
    line: dict | None,
    source_width: int,
    source_height: int,
    target_width: int = DISPLAY_FRAME_WIDTH,
    target_height: int = DISPLAY_FRAME_HEIGHT,
) -> dict | None:
    if not line:
        return None

    start = display_normalized_point_to_source_normalized((line.get("x1"), line.get("y1")), source_width, source_height, target_width, target_height)
    end = display_normalized_point_to_source_normalized((line.get("x2"), line.get("y2")), source_width, source_height, target_width, target_height)
    if start is None or end is None:
        return None

    return {
        "x1": float(start[0]),
        "y1": float(start[1]),
        "x2": float(end[0]),
        "y2": float(end[1]),
    }


def normalize_display_frame(
    frame,
    width: int = DISPLAY_FRAME_WIDTH,
    height: int = DISPLAY_FRAME_HEIGHT,
    background_color: tuple[int, int, int] = (0, 0, 0),
    scale_up: bool = True,
):
    if frame is None:
        return None

    if not hasattr(frame, "shape") or len(frame.shape) < 2:
        return frame

    src_h, src_w = frame.shape[:2]
    if src_h <= 0 or src_w <= 0:
        return frame

    target_w = int(width)
    target_h = int(height)

    if target_w <= 0 or target_h <= 0:
        return frame

    transform = build_letterbox_transform(src_w, src_h, target_w, target_h)
    scale = transform.scale
    if not scale_up:
        scale = min(scale, 1.0)
        if scale != transform.scale:
            transform = build_letterbox_transform(src_w, src_h, target_w, target_h)
            transform = LetterboxTransform(
                source_width=transform.source_width,
                source_height=transform.source_height,
                target_width=transform.target_width,
                target_height=transform.target_height,
                scale=scale,
                content_width=max(1, int(round(src_w * scale))),
                content_height=max(1, int(round(src_h * scale))),
                offset_x=int(round((target_w - max(1, int(round(src_w * scale)))) / 2.0)),
                offset_y=int(round((target_h - max(1, int(round(src_h * scale)))) / 2.0)),
            )

    new_w = transform.content_width
    new_h = transform.content_height

    if new_w == src_w and new_h == src_h:
        resized = frame
    else:
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(frame, (new_w, new_h), interpolation=interpolation)

    canvas = np.full((target_h, target_w, 3), background_color, dtype=resized.dtype)

    x = transform.offset_x
    y = transform.offset_y

    canvas[y:y + new_h, x:x + new_w] = resized
    return canvas
