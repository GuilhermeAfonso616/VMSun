from __future__ import annotations

from dataclasses import dataclass
from math import hypot


@dataclass(slots=True)
class Footpoint:
    x: float
    y: float


def normalize_footpoint(value) -> tuple[float, float]:
    if value is None:
        return (0.0, 0.0)
    if isinstance(value, Footpoint):
        return (float(value.x), float(value.y))
    if isinstance(value, tuple) or isinstance(value, list):
        if len(value) >= 2:
            return (float(value[0]), float(value[1]))
    return (float(getattr(value, "x", 0.0)), float(getattr(value, "y", 0.0)))


def bbox_footpoint(bbox: list[float] | tuple[float, float, float, float]) -> Footpoint:
    x1, y1, x2, y2 = [float(v) for v in bbox]
    return Footpoint(x=(x1 + x2) / 2.0, y=y2)


def bbox_center(bbox: list[float] | tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = [float(v) for v in bbox]
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def bbox_width(bbox: list[float] | tuple[float, float, float, float]) -> float:
    x1, _, x2, _ = [float(v) for v in bbox]
    return max(0.0, x2 - x1)


def bbox_height(bbox: list[float] | tuple[float, float, float, float]) -> float:
    _, y1, _, y2 = [float(v) for v in bbox]
    return max(0.0, y2 - y1)


def bbox_area(bbox: list[float] | tuple[float, float, float, float]) -> float:
    return bbox_width(bbox) * bbox_height(bbox)


def bbox_aspect_ratio(bbox: list[float] | tuple[float, float, float, float]) -> float:
    height = bbox_height(bbox)
    if height <= 0.0:
        return 0.0
    return bbox_width(bbox) / height


def bbox_dimensions(bbox: list[float] | tuple[float, float, float, float]) -> tuple[float, float, float]:
    width = bbox_width(bbox)
    height = bbox_height(bbox)
    return width, height, width * height


def iou(bbox_a, bbox_b) -> float:
    ax1, ay1, ax2, ay2 = [float(v) for v in bbox_a]
    bx1, by1, bx2, by2 = [float(v) for v in bbox_b]

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area <= 0.0:
        return 0.0

    area_a = bbox_area(bbox_a)
    area_b = bbox_area(bbox_b)
    denom = area_a + area_b - inter_area
    return float(inter_area / denom) if denom > 0 else 0.0


def point_distance(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    return float(hypot(point_a[0] - point_b[0], point_a[1] - point_b[1]))


def border_proximity_score(
    point: tuple[float, float],
    width: float | None,
    height: float | None,
    *,
    margin_ratio: float = 0.06,
) -> float:
    if not width or not height:
        return 0.75

    margin_x = max(1.0, float(width) * float(margin_ratio))
    margin_y = max(1.0, float(height) * float(margin_ratio))
    left = float(point[0])
    top = float(point[1])
    right = float(width) - left
    bottom = float(height) - top
    nearest_edge = min(left, top, right, bottom)
    if nearest_edge <= 0.0:
        return 0.0
    if nearest_edge >= max(margin_x, margin_y):
        return 1.0
    return max(0.0, min(1.0, nearest_edge / float(max(margin_x, margin_y))))


def point_ratio(point: tuple[float, float], width: float | None, height: float | None) -> tuple[float, float]:
    if not width or not height:
        return (0.0, 0.0)
    return (float(point[0]) / float(width), float(point[1]) / float(height))


def point_side_of_line(point: tuple[float, float], line_start: tuple[float, float], line_end: tuple[float, float]) -> float:
    x, y = point
    x1, y1 = line_start
    x2, y2 = line_end
    return (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)


def point_side_of_line_with_deadband(
    previous_point: tuple[float, float],
    current_point: tuple[float, float],
    line_start: tuple[float, float],
    line_end: tuple[float, float],
    *,
    deadband: float = 1.5,
) -> tuple[float | None, float | None, bool]:
    previous_side = point_side_of_line(previous_point, line_start, line_end)
    current_side = point_side_of_line(current_point, line_start, line_end)
    if abs(previous_side) <= deadband or abs(current_side) <= deadband:
        return previous_side, current_side, False
    crossed = previous_side * current_side < 0
    return previous_side, current_side, crossed


def movement_crosses_line_segment(
    previous_point: tuple[float, float],
    current_point: tuple[float, float],
    line_start: tuple[float, float],
    line_end: tuple[float, float],
    *,
    deadband: float = 1.5,
) -> tuple[float | None, float | None, bool]:
    previous_side, current_side, crossed = point_side_of_line_with_deadband(
        previous_point,
        current_point,
        line_start,
        line_end,
        deadband=deadband,
    )
    if not crossed:
        return previous_side, current_side, False

    px, py = previous_point
    rx = current_point[0] - px
    ry = current_point[1] - py
    qx, qy = line_start
    sx = line_end[0] - qx
    sy = line_end[1] - qy
    denom = rx * sy - ry * sx
    if abs(denom) <= 1e-9:
        return previous_side, current_side, False

    qpx = qx - px
    qpy = qy - py
    movement_t = (qpx * sy - qpy * sx) / denom
    line_t = (qpx * ry - qpy * rx) / denom
    intersects_movement = 0.0 <= movement_t <= 1.0
    intersects_line_segment = 0.0 <= line_t <= 1.0
    return previous_side, current_side, bool(intersects_movement and intersects_line_segment)


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False

    x, y = point

    def _point_on_segment(px, py, ax, ay, bx, by, epsilon: float = 1e-6) -> bool:
        cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
        if abs(cross) > epsilon:
            return False
        min_x = min(ax, bx) - epsilon
        max_x = max(ax, bx) + epsilon
        min_y = min(ay, by) - epsilon
        max_y = max(ay, by) + epsilon
        return min_x <= px <= max_x and min_y <= py <= max_y

    for i in range(len(polygon)):
        ax, ay = polygon[i - 1]
        bx, by = polygon[i]
        if _point_on_segment(x, y, ax, ay, bx, by):
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


def _band_get(band, name: str, default=None):
    if isinstance(band, dict):
        return band.get(name, default)
    return getattr(band, name, default)


def select_perspective_band(y_ratio: float, bands: list | None):
    if not bands:
        return None
    for band in bands:
        y_min = float(_band_get(band, "y_min", 0.0))
        y_max = float(_band_get(band, "y_max", 1.0))
        if y_min <= y_ratio <= y_max:
            return band
    return bands[-1]


def size_plausibility_from_profile(
    bbox: list[float] | tuple[float, float, float, float] | None,
    y_ratio: float,
    bands: list | None,
    min_size_by_region: dict[str, dict[str, float]] | None = None,
    max_size_by_region: dict[str, dict[str, float]] | None = None,
    *,
    point: tuple[float, float] | None = None,
    frame_width: float | None = None,
    frame_height: float | None = None,
    min_aspect_ratio: float = 0.22,
    max_aspect_ratio: float = 1.25,
    border_margin_ratio: float = 0.06,
) -> float:
    if not bbox:
        return 0.0
    width = bbox_width(bbox)
    height = bbox_height(bbox)
    area = bbox_area(bbox)
    aspect = bbox_aspect_ratio(bbox)
    score = 0.72
    band = select_perspective_band(y_ratio, bands)
    if band is not None:
        min_height = _band_get(band, "min_bbox_height")
        max_height = _band_get(band, "max_bbox_height")
        min_area = _band_get(band, "min_bbox_area")
        max_area = _band_get(band, "max_bbox_area")
        if min_height is not None and height < float(min_height):
            score *= 0.35
        if max_height is not None and height > float(max_height):
            score *= 0.5
        if min_area is not None and area < float(min_area):
            score *= 0.4
        if max_area is not None and area > float(max_area):
            score *= 0.6
    if aspect > 0.0 and (aspect < float(min_aspect_ratio) or aspect > float(max_aspect_ratio)):
        score *= 0.45
    if y_ratio > 0.82 and (height < 35 or area < 900):
        score *= 0.35
    if y_ratio < 0.18 and height > 0 and width / max(height, 1.0) > 1.2:
        score *= 0.8
    if point is not None and frame_width and frame_height:
        border_score = border_proximity_score(point, frame_width, frame_height, margin_ratio=border_margin_ratio)
        if border_score < 0.2:
            score *= 0.25
        elif border_score < 0.4:
            score *= 0.55
        else:
            score *= (0.85 + (0.15 * border_score))
    if min_size_by_region or max_size_by_region:
        score *= 0.95
    return max(0.0, min(1.0, score))


def normalized_band(value: float, bands: list[tuple[float, float, float]]) -> float:
    """Retorna um fator 0-1 baseado em bandas ordenadas por faixa."""
    for lower, upper, score in bands:
        if lower <= value <= upper:
            return float(score)
    return 0.5
