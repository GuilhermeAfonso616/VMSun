"""Funcoes geometricas usadas por ROI, linha e editor de camera.

Mantemos este modulo pequeno para concentrar as conversoes espaciais e evitar
duplicacao entre UI, preprocessamento e regras de evento.
"""

from __future__ import annotations

from typing import Iterable


def bbox_centroid(bbox) -> tuple[float, float] | None:
    if not bbox or len(bbox) != 4:
        return None
    x1, y1, x2, y2 = [float(v) for v in bbox]
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def bbox_bottom_center(bbox) -> tuple[float, float] | None:
    if not bbox or len(bbox) != 4:
        return None
    x1, y1, x2, y2 = [float(v) for v in bbox]
    return ((x1 + x2) / 2.0, y2)


def bbox_area(bbox) -> float | None:
    if not bbox or len(bbox) != 4:
        return None
    x1, y1, x2, y2 = [float(v) for v in bbox]
    width = max(0.0, x2 - x1)
    height = max(0.0, y2 - y1)
    return width * height


def normalized_polygon_to_pixels(points: Iterable[dict], frame_width: int, frame_height: int) -> list[tuple[int, int]]:
    output = []
    for point in points or []:
        try:
            x = max(0.0, min(1.0, float(point["x"])))
            y = max(0.0, min(1.0, float(point["y"])))
        except Exception:
            continue
        output.append((int(round(x * frame_width)), int(round(y * frame_height))))
    return output


def normalized_line_to_pixels(line: dict | None, frame_width: int, frame_height: int):
    if not line:
        return None
    try:
        x1 = int(round(max(0.0, min(1.0, float(line["x1"]))) * frame_width))
        y1 = int(round(max(0.0, min(1.0, float(line["y1"]))) * frame_height))
        x2 = int(round(max(0.0, min(1.0, float(line["x2"]))) * frame_width))
        y2 = int(round(max(0.0, min(1.0, float(line["y2"]))) * frame_height))
        return (x1, y1), (x2, y2)
    except Exception:
        return None


def _point_on_segment(point: tuple[float, float], segment_start: tuple[int, int], segment_end: tuple[int, int], epsilon: float = 1e-6) -> bool:
    px, py = point
    x1, y1 = segment_start
    x2, y2 = segment_end

    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if abs(cross) > epsilon:
        return False

    min_x = min(x1, x2) - epsilon
    max_x = max(x1, x2) + epsilon
    min_y = min(y1, y2) - epsilon
    max_y = max(y1, y2) + epsilon
    return min_x <= px <= max_x and min_y <= py <= max_y


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[int, int]]) -> bool:
    # O teste inclui borda como dentro para reduzir falso negativo em ROI apertada.
    if not point or len(polygon) < 3:
        return False
    x, y = point

    for i in range(len(polygon)):
        if _point_on_segment(point, polygon[i - 1], polygon[i]):
            return True

    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def point_line_side(point: tuple[float, float], line_start: tuple[int, int], line_end: tuple[int, int]) -> float:
    x, y = point
    x1, y1 = line_start
    x2, y2 = line_end
    return (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
