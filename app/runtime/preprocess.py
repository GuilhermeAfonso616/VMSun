"""Prepara geometria e frames para inferencia.

ROI e linha descrevem regras analiticas sobre a cena. O detector sempre ve o
frame inteiro (apenas redimensionado), para que uma pessoa possa ser rastreada
antes de entrar, sair ou cruzar uma regra geometrica.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2

from app.runtime.camera_config import AnalyticsConfig
from app.analytics.spatial import normalized_line_to_pixels, normalized_polygon_to_pixels
from app.services.display_resize import (
    DISPLAY_FRAME_HEIGHT,
    DISPLAY_FRAME_WIDTH,
    display_normalized_line_to_source_normalized,
    display_normalized_polygon_to_source_normalized,
)


@dataclass(slots=True)
class SceneGeometry:
    roi_polygon: list[tuple[int, int]]
    line_pixels: Optional[tuple[tuple[int, int], tuple[int, int]]]
    frame_width: int
    frame_height: int


@dataclass(slots=True)
class InferenceFrame:
    frame: object
    offset_x: int
    offset_y: int
    roi_crop_active: bool
    roi_crop_meta: Optional[dict]
    input_width: int
    input_height: int
    source_width: int
    source_height: int
    scale_x: float = 1.0
    scale_y: float = 1.0


class FramePreprocessor:
    def build_geometry(self, analytics: AnalyticsConfig, frame_width: int, frame_height: int) -> SceneGeometry:
        roi_points = analytics.roi_points or []
        line = analytics.line

        if (analytics.coordinate_space or "display").lower() == "display":
            roi_points = display_normalized_polygon_to_source_normalized(
                roi_points,
                frame_width,
                frame_height,
                DISPLAY_FRAME_WIDTH,
                DISPLAY_FRAME_HEIGHT,
            )
            line = display_normalized_line_to_source_normalized(
                line,
                frame_width,
                frame_height,
                DISPLAY_FRAME_WIDTH,
                DISPLAY_FRAME_HEIGHT,
            )

        roi_polygon = normalized_polygon_to_pixels(roi_points, frame_width, frame_height) if roi_points else []
        line_pixels = normalized_line_to_pixels(line, frame_width, frame_height) if line else None
        return SceneGeometry(
            roi_polygon=roi_polygon,
            line_pixels=line_pixels,
            frame_width=frame_width,
            frame_height=frame_height,
        )

    def _resize_for_inference(
        self,
        frame,
        *,
        max_width: int | None = None,
        max_height: int | None = None,
        allow_upscale: bool | None = None,
    ):
        h, w = frame.shape[:2]
        from app.core.config import settings

        max_w = max(1, int(max_width if max_width is not None else settings.processing_max_width))
        max_h = max(1, int(max_height if max_height is not None else settings.processing_max_height))
        allow_upscale = bool(settings.processing_upscale_small_frames if allow_upscale is None else allow_upscale)

        if w <= 0 or h <= 0:
            return frame, 1.0, 1.0

        scale_w = max_w / float(w)
        scale_h = max_h / float(h)
        scale = min(scale_w, scale_h)

        if not allow_upscale:
            scale = min(scale, 1.0)

        if scale >= 0.999:
            return frame, 1.0, 1.0

        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        scale_x = w / float(new_w)
        scale_y = h / float(new_h)
        return resized, scale_x, scale_y

    def build_inference_frame(
        self,
        frame,
        roi_polygon: list[tuple[int, int]],
        *,
        max_width: int | None = None,
        max_height: int | None = None,
        allow_upscale: bool | None = None,
    ) -> InferenceFrame:
        # ``roi_polygon`` remains part of this API because it is built alongside
        # the inference frame, but it must not alter detector input. The
        # analytics pipeline receives the same full-frame coordinates and uses
        # the ROI/line only to decide which events are emitted.
        del roi_polygon
        h, w = frame.shape[:2]
        resized, scale_x, scale_y = self._resize_for_inference(
            frame,
            max_width=max_width,
            max_height=max_height,
            allow_upscale=allow_upscale,
        )
        rh, rw = resized.shape[:2]
        return InferenceFrame(
            frame=resized,
            offset_x=0,
            offset_y=0,
            roi_crop_active=False,
            roi_crop_meta=None,
            input_width=rw,
            input_height=rh,
            source_width=w,
            source_height=h,
            scale_x=scale_x,
            scale_y=scale_y,
        )
