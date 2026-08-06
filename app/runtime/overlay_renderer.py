"""Renderizacao de tracks, regioes analiticas e indicadores de movimento."""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np


class OverlayRenderer:
    ROI_COLOR = (0, 230, 255)
    LINE_ANY_COLOR = (0, 165, 255)
    LINE_A_TO_B_COLOR = (0, 200, 0)
    LINE_B_TO_A_COLOR = (0, 0, 255)
    BOX_COLOR = (255, 110, 0)
    REVALIDATED_BOX_COLOR = (255, 110, 0)
    MOTION_BOX_COLOR = (255, 0, 255)
    STATUS_OK_COLOR = (0, 220, 0)
    STATUS_IDLE_COLOR = (200, 200, 200)
    MOTION_INDICATOR_COLOR = (0, 255, 0)
    MOTION_INDICATOR_OFF_COLOR = (120, 120, 120)

    def get_line_color(self, line_direction: str):
        if line_direction == "a_to_b":
            return self.LINE_A_TO_B_COLOR
        if line_direction == "b_to_a":
            return self.LINE_B_TO_A_COLOR
        return self.LINE_ANY_COLOR

    def _draw_centered_flow(self, frame, start, end, color, line_direction: str):
        dx = float(end[0] - start[0])
        dy = float(end[1] - start[1])
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        ux = dy / length
        uy = -dx / length
        mid = (
            (float(start[0]) + float(end[0])) / 2.0,
            (float(start[1]) + float(end[1])) / 2.0,
        )
        marker_gap = 16.0
        marker_length = 28.0

        def marker(sign: float):
            marker_start = (
                int(round(mid[0] + ux * marker_gap * sign)),
                int(round(mid[1] + uy * marker_gap * sign)),
            )
            marker_end = (
                int(round(marker_start[0] + ux * marker_length * sign)),
                int(round(marker_start[1] + uy * marker_length * sign)),
            )
            cv2.arrowedLine(
                frame,
                marker_start,
                marker_end,
                color,
                3,
                cv2.LINE_AA,
                tipLength=0.45,
            )

        if line_direction == "b_to_a":
            marker(-1.0)
        elif line_direction == "a_to_b":
            marker(1.0)
        else:
            marker(1.0)
            marker(-1.0)

    def _safe_label_pos(self, point):
        x, y = int(point[0]), int(point[1])
        return (max(0, x), max(20, y - 10))

    def draw_tracks(self, frame, tracks: list[dict]):
        for track in tracks:
            bbox = track.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = [int(v) for v in bbox]
            track_id = track.get("track_id", -1)
            conf = track.get("confidence")
            color = (
                self.REVALIDATED_BOX_COLOR
                if track.get("visual_status") == "revalidated"
                else self.BOX_COLOR
            )
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = str(track.get("label") or "person")
            if track_id is not None and track_id >= 0:
                label += f" #{track_id}"
            if conf is not None:
                label += f" {conf:.2f}"
            if track.get("visual_status") == "revalidated":
                label += " IA"
            cv2.putText(
                frame,
                label,
                (x1, max(20, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )

    def draw_analytics_overlay(
        self,
        frame,
        roi_polygon,
        line_pixels,
        roi_name: Optional[str],
        line_direction: str,
    ):
        if len(roi_polygon) >= 3:
            pts = np.array(roi_polygon, dtype=np.int32).reshape((-1, 1, 2))
            overlay = frame.copy()
            cv2.fillPoly(overlay, [pts], self.ROI_COLOR)
            cv2.addWeighted(overlay, 0.12, frame, 0.88, 0, frame)
            cv2.polylines(
                frame,
                [pts],
                isClosed=True,
                color=self.ROI_COLOR,
                thickness=2,
            )
            for point in roi_polygon:
                cv2.circle(
                    frame,
                    (int(point[0]), int(point[1])),
                    4,
                    self.ROI_COLOR,
                    -1,
                )
            label = roi_name or "ROI"
            cv2.putText(
                frame,
                label,
                self._safe_label_pos(roi_polygon[0]),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                self.ROI_COLOR,
                2,
                cv2.LINE_AA,
            )

        if line_pixels:
            start, end = line_pixels
            start = (int(start[0]), int(start[1]))
            end = (int(end[0]), int(end[1]))
            color = self.get_line_color(line_direction)
            cv2.line(frame, start, end, color, 3, cv2.LINE_AA)
            self._draw_centered_flow(frame, start, end, color, line_direction)
            cv2.circle(frame, start, 5, color, -1)
            cv2.circle(frame, end, 5, color, -1)
            cv2.putText(
                frame,
                "A",
                (start[0] + 8, max(20, start[1] - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                "B",
                (end[0] + 8, max(20, end[1] - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
                cv2.LINE_AA,
            )

    def draw_motion_boxes(
        self,
        frame,
        moving_boxes,
        roi_meta=None,
        downscale_width: int = 384,
    ):
        if not moving_boxes:
            return
        scale_x = 1.0
        scale_y = 1.0
        offset_x = 0
        offset_y = 0
        if roi_meta:
            offset_x = int(roi_meta["x"])
            offset_y = int(roi_meta["y"])
            roi_w = max(1, int(roi_meta["w"]))
            roi_h = max(1, int(roi_meta["h"]))
        else:
            roi_h, roi_w = frame.shape[:2]
        if downscale_width > 0:
            scale_x = roi_w / float(downscale_width)
            scaled_h = max(
                1,
                int((downscale_width / float(max(1, roi_w))) * roi_h),
            )
            scale_y = roi_h / float(max(1, scaled_h))
        for bx in moving_boxes:
            x1, y1, x2, y2 = bx
            fx1 = int(x1 * scale_x) + offset_x
            fy1 = int(y1 * scale_y) + offset_y
            fx2 = int(x2 * scale_x) + offset_x
            fy2 = int(y2 * scale_y) + offset_y
            cv2.rectangle(frame, (fx1, fy1), (fx2, fy2), self.MOTION_BOX_COLOR, 1)

    def draw_motion_indicator(self, frame, motion_detected: bool):
        label = "MOVIMENTO" if motion_detected else "SEM MOVIMENTO"
        color = (
            self.MOTION_INDICATOR_COLOR
            if motion_detected
            else self.MOTION_INDICATOR_OFF_COLOR
        )
        x = frame.shape[1] - 210
        y = 18
        w = 190
        h = 42
        overlay = frame.copy()
        cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)
        cv2.circle(frame, (x + 18, y + 21), 8, color, -1)
        cv2.putText(
            frame,
            label,
            (x + 34, y + 27),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            color,
            2,
            cv2.LINE_AA,
        )

    def draw_motion_status_panel(
        self,
        frame,
        motion_info: dict,
        should_infer: bool,
        infer_ran: bool,
        tracks_count: int,
    ):
        state = motion_info.get("state", "idle")
        motion_detected = bool(motion_info.get("motion_detected"))
        motion_ratio = float(motion_info.get("motion_ratio", 0.0))
        global_change_ratio = float(motion_info.get("global_change_ratio", 0.0))
        lines = [
            f"mode: {state}",
            f"motion: {'ON' if motion_detected else 'OFF'}",
            f"motion_ratio: {motion_ratio:.4f}",
            f"global_change: {global_change_ratio:.4f}",
            f"gate_trigger: {'YES' if should_infer else 'NO'}",
            f"infer_ran: {'YES' if infer_ran else 'NO'}",
            f"tracks: {tracks_count}",
        ]
        x = 10
        y = 24
        box_w = 290
        box_h = 24 + len(lines) * 22
        overlay = frame.copy()
        cv2.rectangle(
            overlay,
            (x, y - 18),
            (x + box_w, y - 18 + box_h),
            (0, 0, 0),
            -1,
        )
        cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)
        for index, line in enumerate(lines):
            color = (
                self.STATUS_OK_COLOR
                if ("ON" in line or "YES" in line)
                else self.STATUS_IDLE_COLOR
            )
            cv2.putText(
                frame,
                line,
                (x + 10, y + index * 22),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )
