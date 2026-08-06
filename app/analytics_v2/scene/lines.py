from __future__ import annotations

from dataclasses import dataclass

from .geometry import point_side_of_line


@dataclass(slots=True)
class LineCrossing:
    line_id: str
    line_name: str
    previous_side: float | None
    current_side: float | None

    @property
    def crossed(self) -> bool:
        return self.previous_side is not None and self.current_side is not None and (self.previous_side * self.current_side) < 0


def line_side(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    return point_side_of_line(point, start, end)
