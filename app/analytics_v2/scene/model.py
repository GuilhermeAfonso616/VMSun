from __future__ import annotations

from dataclasses import dataclass, field

from ..config.schema import DirectionalLine, SceneZone
from .geometry import (
    bbox_footpoint,
    bbox_area,
    bbox_aspect_ratio,
    bbox_height,
    bbox_width,
    border_proximity_score,
    point_in_polygon,
    point_distance,
    point_ratio,
    movement_crosses_line_segment,
    select_perspective_band,
    size_plausibility_from_profile,
)
from .lines import LineCrossing


@dataclass(slots=True)
class SceneObservation:
    footpoint: tuple[float, float]
    bbox_width: float = 0.0
    bbox_height: float = 0.0
    bbox_area: float = 0.0
    aspect_ratio: float = 0.0
    motion_step_px: float = 0.0
    restricted_zone: SceneZone | None = None
    buffer_zone: SceneZone | None = None
    exclusion_zone: SceneZone | None = None
    line_crossings: list[LineCrossing] = field(default_factory=list)
    perspective_band: object | None = None
    size_plausibility: float = 0.0
    border_score: float = 1.0
    near_border: bool = False
    in_restricted_area: bool = False


@dataclass(slots=True)
class AnalyticsScene:
    width: int | None = None
    height: int | None = None
    restricted_zones: list[SceneZone] = field(default_factory=list)
    exclusion_zones: list[SceneZone] = field(default_factory=list)
    buffer_zones: list[SceneZone] = field(default_factory=list)
    directional_lines: list[DirectionalLine] = field(default_factory=list)
    perspective_profile: list[dict] = field(default_factory=list)
    border_margin_ratio: float = 0.06
    min_bbox_aspect_ratio: float = 0.22
    max_bbox_aspect_ratio: float = 1.25

    def first_restricted_zone(self) -> SceneZone | None:
        return self.restricted_zones[0] if self.restricted_zones else None

    def observe_point(
        self,
        point: tuple[float, float],
        *,
        bbox: list[float] | None = None,
        previous_point: tuple[float, float] | None = None,
    ) -> SceneObservation:
        restricted_zone = self._match_zone(point, self.restricted_zones)
        buffer_zone = self._match_zone(point, self.buffer_zones)
        exclusion_zone = self._match_zone(point, self.exclusion_zones)
        width = float(self.width or 0.0)
        height = float(self.height or 0.0)
        _, y_ratio = point_ratio(point, width, height)
        band = select_perspective_band(y_ratio, self.perspective_profile)
        line_crossings: list[LineCrossing] = []
        motion_step_px = point_distance(previous_point, point) if previous_point is not None else 0.0
        border_score = border_proximity_score(point, width, height, margin_ratio=self.border_margin_ratio)
        near_border = border_score < 0.45 if width and height else False
        bbox_w = bbox_h = bbox_a = bbox_aspect = 0.0
        size_score = 0.0
        if bbox is not None:
            bbox_w = bbox_width(bbox)
            bbox_h = bbox_height(bbox)
            bbox_a = bbox_area(bbox)
            bbox_aspect = bbox_aspect_ratio(bbox)
        if previous_point is not None:
            for line in self.directional_lines:
                if not line.enabled:
                    continue
                prev_side, curr_side, crossed = movement_crosses_line_segment(previous_point, point, line.start, line.end)
                if crossed:
                    line_crossings.append(
                        LineCrossing(
                            line_id=line.line_id,
                            line_name=line.name,
                            previous_side=prev_side,
                            current_side=curr_side,
                        )
                    )
        size_score = size_plausibility_from_profile(
            bbox,
            y_ratio,
            self.perspective_profile,
            point=point,
            frame_width=width,
            frame_height=height,
            min_aspect_ratio=self.min_bbox_aspect_ratio,
            max_aspect_ratio=self.max_bbox_aspect_ratio,
            border_margin_ratio=self.border_margin_ratio,
        )
        return SceneObservation(
            footpoint=point,
            bbox_width=bbox_w,
            bbox_height=bbox_h,
            bbox_area=bbox_a,
            aspect_ratio=bbox_aspect,
            motion_step_px=motion_step_px,
            restricted_zone=restricted_zone,
            buffer_zone=buffer_zone,
            exclusion_zone=exclusion_zone,
            line_crossings=line_crossings,
            perspective_band=band,
            size_plausibility=size_score,
            border_score=border_score,
            near_border=near_border,
            in_restricted_area=restricted_zone is not None and exclusion_zone is None,
        )

    def observe_track(self, track) -> SceneObservation:
        current_point = track.footpoint_current
        if current_point is None and track.bbox_current:
            current_point = bbox_footpoint(track.bbox_current)
        if current_point is None:
            observation = SceneObservation(footpoint=(0.0, 0.0))
            track.metadata["scene_observation"] = observation
            return observation

        previous_point = None
        if len(track.bbox_history) >= 2:
            previous_point = track.bbox_history[-2].footpoint
        observation = self.observe_point(current_point, bbox=track.bbox_current, previous_point=previous_point)
        track.metadata["scene_observation"] = observation
        track.metadata["scene_zone_id"] = getattr(observation.restricted_zone, "zone_id", None)
        track.metadata["scene_buffer_zone_id"] = getattr(observation.buffer_zone, "zone_id", None)
        track.metadata["scene_exclusion_zone_id"] = getattr(observation.exclusion_zone, "zone_id", None)
        track.size_confidence = observation.size_plausibility
        track.border_confidence = observation.border_score
        track.geometry_confidence = max(0.0, min(1.0, (0.65 * observation.size_plausibility) + (0.35 * observation.border_score)))
        track.metadata["scene_geometry_confidence"] = track.geometry_confidence
        track.metadata["scene_aspect_ratio"] = observation.aspect_ratio
        track.metadata["scene_motion_step_px"] = observation.motion_step_px
        track.metadata["scene_near_border"] = observation.near_border

        if observation.exclusion_zone is not None:
            zone_id = f"exclusion:{observation.exclusion_zone.zone_id}"
            track.zone_history.append(zone_id)
            if len(track.zone_history) > 60:
                track.zone_history = track.zone_history[-60:]
            if track.bbox_history:
                track.bbox_history[-1].zone_ids.append(zone_id)
        elif observation.restricted_zone is not None:
            zone_id = observation.restricted_zone.zone_id
            track.zone_history.append(zone_id)
            if len(track.zone_history) > 60:
                track.zone_history = track.zone_history[-60:]
            if track.bbox_history:
                track.bbox_history[-1].zone_ids.append(zone_id)
        elif observation.buffer_zone is not None:
            zone_id = observation.buffer_zone.zone_id
            track.zone_history.append(zone_id)
            if len(track.zone_history) > 60:
                track.zone_history = track.zone_history[-60:]
            if track.bbox_history:
                track.bbox_history[-1].zone_ids.append(zone_id)
        else:
            track.zone_history.append("outside")
            if len(track.zone_history) > 60:
                track.zone_history = track.zone_history[-60:]

        for crossing in observation.line_crossings:
            if track.bbox_history:
                track.bbox_history[-1].line_ids.append(crossing.line_id)
            track.line_crossing_history.append(
                {
                    "line_id": crossing.line_id,
                    "line_name": crossing.line_name,
                    "previous_side": crossing.previous_side,
                    "current_side": crossing.current_side,
                }
            )
            if len(track.line_crossing_history) > 60:
                track.line_crossing_history = track.line_crossing_history[-60:]

        return observation

    def observe_tracks(self, tracks: list) -> list[SceneObservation]:
        return [self.observe_track(track) for track in tracks]

    def _match_zone(self, point: tuple[float, float], zones: list[SceneZone]) -> SceneZone | None:
        for zone in zones:
            if zone.enabled and point_in_polygon(point, zone.polygon):
                return zone
        return None
