from __future__ import annotations

from dataclasses import dataclass

from .geometry import point_in_polygon


@dataclass(slots=True)
class ZoneHit:
    zone_id: str
    zone_name: str
    zone_type: str


def footpoint_in_zone(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    return point_in_polygon(point, polygon)
