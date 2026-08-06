from __future__ import annotations

from dataclasses import dataclass

from .geometry import bbox_area, bbox_height


@dataclass(slots=True)
class PerspectiveProfile:
    bands: list[dict]

    def score_size_plausibility(self, bbox, footpoint_y_ratio: float) -> float:
        if not self.bands:
            return 0.5
        height = bbox_height(bbox)
        area = bbox_area(bbox)
        for band in self.bands:
            y_min = float(band.get("y_min", 0.0))
            y_max = float(band.get("y_max", 1.0))
            if y_min <= footpoint_y_ratio <= y_max:
                height_ok = True
                area_ok = True
                min_h = band.get("min_bbox_height")
                max_h = band.get("max_bbox_height")
                min_a = band.get("min_bbox_area")
                max_a = band.get("max_bbox_area")
                if min_h is not None:
                    height_ok = height >= float(min_h)
                if max_h is not None:
                    height_ok = height_ok and height <= float(max_h)
                if min_a is not None:
                    area_ok = area >= float(min_a)
                if max_a is not None:
                    area_ok = area_ok and area <= float(max_a)
                return 1.0 if (height_ok and area_ok) else 0.2
        return 0.5
