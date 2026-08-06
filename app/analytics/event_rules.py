"""Regras operacionais de eventos humanos para o motor de analítica.

Este módulo decide quando emitir eventos de presença, ROI, linha e permanência
com base nos tracks recebidos do detector/tracker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import hypot

from app.analytics.spatial import bbox_centroid, bbox_bottom_center, point_in_polygon, point_line_side
from app.core.config import settings
from app.core.timezone import utc_now_naive
from app.core.logging import get_event_rules_debug_logger


HUMAN_EVENT_MODE_DEFAULTS = {
    "person_entered": True,
    "person_left": True,
    "person_entered_roi": True,
    "person_left_roi": True,
    "person_loitering": False,
    "line_crossing": True,
}

HUMAN_DETECTION_SENSITIVITY_THRESHOLDS = {
    "very_low": 0.20,
    "low": 0.30,
    "medium": 0.45,
    "high": 0.60,
}


@dataclass(slots=True)
class TrackLifecycle:
    stable_id: int
    first_seen: datetime
    last_seen: datetime
    seen_frames: int = 0
    entered_emitted: bool = False
    last_bbox: list[float] | None = None
    roi_inside: bool = False
    line_last_side: float | None = None
    missing_since: datetime | None = None
    missing_frames: int = 0
    loitering_emitted: bool = False


class EventRules:
    def __init__(
        self,
        roi_polygon: list[tuple[int, int]] | None = None,
        line: tuple[tuple[int, int], tuple[int, int]] | None = None,
        line_direction: str = "any",
        human_event_modes: list[str] | None = None,
        human_loitering_seconds: float | None = None,
        human_detection_sensitivity: str | None = None,
    ):
        self.roi_polygon = roi_polygon or []
        self.line = line
        self.line_direction = line_direction or "any"
        self.human_event_modes = human_event_modes
        self.human_loitering_seconds = human_loitering_seconds
        self.human_detection_sensitivity = human_detection_sensitivity
        self.active_tracks: dict[int, TrackLifecycle] = {}
        self.lost_tracks: dict[int, TrackLifecycle] = {}
        self.next_stable_id = 1
        self.debug_logger = get_event_rules_debug_logger()

    def update_policy(
        self,
        human_event_modes: list[str] | None = None,
        human_loitering_seconds: float | None = None,
        human_detection_sensitivity: str | None = None,
    ) -> None:
        self.human_event_modes = human_event_modes
        self.human_loitering_seconds = human_loitering_seconds
        self.human_detection_sensitivity = human_detection_sensitivity

    def process(self, camera_id: int, tracks: list[dict]):
        generated: list[dict] = []
        now = utc_now_naive()
        current_raw_ids: set[int] = set()
        has_roi = len(self.roi_polygon) >= 3
        # Cada família de evento pode ser ligada/desligada por câmera sem quebrar as outras.
        roi_family_enabled = self._mode_enabled("person_entered_roi") or self._mode_enabled("person_left_roi")
        presence_family_enabled = self._mode_enabled("person_entered") or self._mode_enabled("person_left")
        loitering_enabled = self._mode_enabled("person_loitering")
        line_enabled = self._mode_enabled("line_crossing") and bool(self.line)
        batch_summary = {
            "camera_id": camera_id,
            "timestamp": now.isoformat(),
            "roi_enabled": has_roi,
            "roi_polygon": self.roi_polygon,
            "line_enabled": line_enabled,
            "line_direction": self.line_direction,
            "human_event_modes": self.human_event_modes,
            "human_detection_sensitivity": self.human_detection_sensitivity,
            "track_count": len(tracks or []),
            "active_tracks": len(self.active_tracks),
            "lost_tracks": len(self.lost_tracks),
            "tracks": [],
        }

        for track in tracks:
            raw_track_id = track.get("track_id")
            if raw_track_id is None or raw_track_id < 0:
                continue

            raw_track_id = int(raw_track_id)
            current_raw_ids.add(raw_track_id)

            state = self.active_tracks.get(raw_track_id)
            if state is None:
                state = self._reacquire_lost_track(track, now)
                if state is None:
                    state = self._create_new_track(now)
                self.active_tracks[raw_track_id] = state

            self._update_track_state(state, track, now)

            track_point = None
            bbox = track.get("bbox")
            if bbox is not None:
                try:
                    track_point = bbox_bottom_center(bbox)
                except Exception:
                    track_point = None

            track_snapshot = {
                "raw_track_id": raw_track_id,
                "stable_id": state.stable_id,
                "confidence": track.get("confidence"),
                "bbox": bbox,
                "bottom_center": track_point,
                "seen_frames": state.seen_frames,
                "entered_emitted": state.entered_emitted,
                "roi_inside": state.roi_inside,
                "line_last_side": state.line_last_side,
                "missing_frames": state.missing_frames,
                "loitering_emitted": state.loitering_emitted,
            }

            before_count = len(generated)
            if presence_family_enabled and (not has_roi or not roi_family_enabled) and not state.entered_emitted and self._should_emit_enter(state, track, now):
                generated.append(
                    {
                        "camera_id": camera_id,
                        "event_type": "person_entered",
                        "track_id": state.stable_id,
                        "confidence": track.get("confidence"),
                        "details": f"Pessoa entrou na cena. bbox={track.get('bbox')} sensitivity={self._confidence_label()}",
                        "bbox": track.get("bbox"),
                        "severity": "medium",
                    }
                )
                state.entered_emitted = True

            if has_roi and roi_family_enabled:
                generated.extend(self._process_roi(camera_id, state, track))

            if loitering_enabled:
                generated.extend(self._process_loitering(camera_id, state, track, now))

            if line_enabled:
                generated.extend(self._process_line(camera_id, state, track))

            track_snapshot["generated_events"] = [event["event_type"] for event in generated[before_count:]]
            batch_summary["tracks"].append(track_snapshot)

        self._move_missing_active_tracks_to_lost(current_raw_ids, now)
        before_finalize = len(generated)
        generated.extend(self._finalize_lost_tracks(camera_id, now, has_roi))
        if len(generated) > before_finalize:
            batch_summary["finalized_events"] = [event["event_type"] for event in generated[before_finalize:]]
        batch_summary["generated_events"] = [event["event_type"] for event in generated]
        batch_summary["generated_count"] = len(generated)
        self._debug_log(batch_summary)
        return generated

    def _create_new_track(self, now: datetime) -> TrackLifecycle:
        state = TrackLifecycle(
            stable_id=self.next_stable_id,
            first_seen=now,
            last_seen=now,
        )
        self.next_stable_id += 1
        return state

    def _update_track_state(self, state: TrackLifecycle, track: dict, now: datetime) -> None:
        state.last_seen = now
        state.seen_frames += 1
        state.missing_since = None
        state.missing_frames = 0

        bbox = track.get("bbox")
        if bbox and len(bbox) == 4:
            state.last_bbox = [float(v) for v in bbox]

    def _reacquire_lost_track(self, track: dict, now: datetime) -> TrackLifecycle | None:
        centroid = bbox_centroid(track.get("bbox"))
        if centroid is None:
            return None

        window = timedelta(seconds=float(settings.track_reacquire_window_seconds))
        max_distance_px = float(settings.track_reacquire_max_distance_px)
        best_match: tuple[float, int, TrackLifecycle] | None = None

        for stable_id, state in list(self.lost_tracks.items()):
            if (now - state.last_seen) > window:
                continue

            previous_centroid = bbox_centroid(state.last_bbox)
            if previous_centroid is None:
                continue

            distance = hypot(
                float(previous_centroid[0]) - float(centroid[0]),
                float(previous_centroid[1]) - float(centroid[1]),
            )
            if distance > max_distance_px:
                continue

            if best_match is None or distance < best_match[0]:
                best_match = (distance, stable_id, state)

        if best_match is None:
            return None

        _, stable_id, matched_state = best_match
        self.lost_tracks.pop(stable_id, None)
        matched_state.missing_since = None
        matched_state.missing_frames = 0
        return matched_state

    def _should_emit_enter(self, state: TrackLifecycle, track: dict, now: datetime) -> bool:
        if state.entered_emitted:
            return False

        if not self._confidence_allows(track):
            return False

        dwell_seconds = max(0.0, (now - state.first_seen).total_seconds())
        enough_frames = state.seen_frames >= int(settings.track_enter_min_seen_frames)
        enough_dwell = dwell_seconds >= float(settings.track_enter_min_dwell_seconds)
        return bool(enough_frames and enough_dwell)

    def _move_missing_active_tracks_to_lost(self, current_raw_ids: set[int], now: datetime) -> None:
        missing_raw_ids = [raw_id for raw_id in self.active_tracks.keys() if raw_id not in current_raw_ids]

        for raw_id in missing_raw_ids:
            state = self.active_tracks.pop(raw_id, None)
            if state is None:
                continue
            state.missing_since = now
            state.missing_frames = 1
            self.lost_tracks[state.stable_id] = state

    def _finalize_lost_tracks(self, camera_id: int, now: datetime, has_roi: bool) -> list[dict]:
        generated: list[dict] = []
        timeout = timedelta(seconds=float(settings.track_exit_timeout_seconds))
        roi_timeout = timedelta(seconds=float(settings.intrusion_lost_track_timeout_seconds))
        min_missing_frames = max(1, int(settings.track_exit_min_missing_frames))
        roi_family_enabled = self._mode_enabled("person_entered_roi") or self._mode_enabled("person_left_roi")
        presence_left_enabled = self._mode_enabled("person_left")

        for stable_id, state in list(self.lost_tracks.items()):
            if state.missing_since is None:
                state.missing_since = state.last_seen

            if state.missing_frames < min_missing_frames:
                state.missing_frames += 1
                continue

            if (now - state.missing_since) < timeout:
                continue

            if state.entered_emitted and presence_left_enabled and not has_roi:
                generated.append(
                    {
                        "camera_id": camera_id,
                        "event_type": "person_left",
                        "track_id": state.stable_id,
                        "confidence": None,
                        "details": "Pessoa saiu da cena",
                        "bbox": None,
                        "severity": "medium",
                    }
                )

            if has_roi and roi_family_enabled and state.roi_inside and self._mode_enabled("person_left_roi"):
                if (now - state.missing_since) < roi_timeout:
                    continue

                generated.append(
                    {
                        "camera_id": camera_id,
                        "event_type": "person_left_roi",
                        "track_id": state.stable_id,
                        "confidence": None,
                        "details": "Pessoa saiu da ROI",
                        "bbox": state.last_bbox,
                        "severity": "low",
                    }
                )
                state.roi_inside = False

            self.lost_tracks.pop(stable_id, None)

        return generated

    def _process_roi(self, camera_id: int, state: TrackLifecycle, track: dict):
        if len(self.roi_polygon) < 3:
            return []

        bbox = track.get("bbox")
        decision_points = self._roi_decision_points(bbox)
        if not decision_points:
            return []

        if not self._confidence_allows(track):
            self._debug_log(
                {
                    "camera_id": camera_id,
                    "stage": "roi_low_confidence",
                    "track_id": state.stable_id,
                    "confidence": track.get("confidence"),
                    "bbox": bbox,
                    "roi_inside": state.roi_inside,
                    "result": "hold",
                    "threshold": self._confidence_threshold(),
                    "decision_points": self._format_roi_decision_points(decision_points),
                }
            )
            return []

        decision_point_name = None
        decision_point = None
        for candidate_name, candidate_point in decision_points:
            if point_in_polygon(candidate_point, self.roi_polygon):
                decision_point_name = candidate_name
                decision_point = candidate_point
                break

        is_inside = decision_point is not None
        point = decision_point or decision_points[0][1]

        if not state.roi_inside:
            if is_inside:
                if self._mode_enabled("person_entered_roi"):
                    state.roi_inside = True
                    event = {
                        "camera_id": camera_id,
                        "event_type": "person_entered_roi",
                        "track_id": state.stable_id,
                        "confidence": track.get("confidence"),
                        "details": f"Pessoa entrou na ROI. point={point} anchor={decision_point_name or decision_points[0][0]} bbox={bbox}",
                        "bbox": bbox,
                        "severity": "high",
                    }
                    self._debug_log(
                        {
                            "camera_id": camera_id,
                            "stage": "roi_enter",
                            "track_id": state.stable_id,
                            "confidence": track.get("confidence"),
                            "bbox": bbox,
                            "point": point,
                            "decision_anchor": decision_point_name or decision_points[0][0],
                            "decision_points": self._format_roi_decision_points(decision_points),
                            "roi_inside": True,
                            "result": "event",
                            "event_type": "person_entered_roi",
                        }
                    )
                    return [
                        event
                    ]
                state.roi_inside = True
            self._debug_log(
                {
                    "camera_id": camera_id,
                    "stage": "roi_outside",
                    "track_id": state.stable_id,
                    "confidence": track.get("confidence"),
                    "bbox": bbox,
                    "point": point,
                    "decision_anchor": decision_point_name or decision_points[0][0],
                    "decision_points": self._format_roi_decision_points(decision_points),
                    "roi_inside": False,
                    "result": "none",
                }
            )
            return []

        if is_inside:
            self._debug_log(
                {
                    "camera_id": camera_id,
                    "stage": "roi_inside",
                    "track_id": state.stable_id,
                    "confidence": track.get("confidence"),
                    "bbox": bbox,
                    "point": point,
                    "decision_anchor": decision_point_name or decision_points[0][0],
                    "decision_points": self._format_roi_decision_points(decision_points),
                    "roi_inside": True,
                    "result": "hold",
                }
            )
            return []

        if not self._mode_enabled("person_left_roi"):
            self._debug_log(
                {
                    "camera_id": camera_id,
                    "stage": "roi_exit_disabled",
                    "track_id": state.stable_id,
                    "confidence": track.get("confidence"),
                    "bbox": bbox,
                    "point": point,
                    "decision_anchor": decision_point_name or decision_points[0][0],
                    "decision_points": self._format_roi_decision_points(decision_points),
                    "roi_inside": True,
                    "result": "hold",
                }
            )
            return []

        state.roi_inside = False
        event = {
            "camera_id": camera_id,
            "event_type": "person_left_roi",
            "track_id": state.stable_id,
            "confidence": track.get("confidence"),
            "details": f"Pessoa saiu da ROI. point={point} anchor={decision_point_name or decision_points[0][0]} bbox={bbox}",
            "bbox": bbox,
            "severity": "low",
        }
        self._debug_log(
            {
                "camera_id": camera_id,
                "stage": "roi_exit",
                "track_id": state.stable_id,
                "confidence": track.get("confidence"),
                "bbox": bbox,
                "point": point,
                "decision_anchor": decision_point_name or decision_points[0][0],
                "decision_points": self._format_roi_decision_points(decision_points),
                "roi_inside": False,
                "result": "event",
                "event_type": "person_left_roi",
            }
        )
        return [event]

    def _process_loitering(self, camera_id: int, state: TrackLifecycle, track: dict, now: datetime):
        if not self._mode_enabled("person_loitering"):
            return []

        if state.loitering_emitted:
            return []

        confidence = track.get("confidence")
        if not self._confidence_allows(track):
            return []

        loitering_seconds = self._loitering_seconds()
        dwell_seconds = max(0.0, (now - state.first_seen).total_seconds())
        if dwell_seconds < loitering_seconds:
            return []

        state.loitering_emitted = True
        return [
            {
                "camera_id": camera_id,
                "event_type": "person_loitering",
                "track_id": state.stable_id,
                "confidence": confidence,
                "details": f"Pessoa permaneceu por {dwell_seconds:.1f}s (min={loitering_seconds:.1f}s) bbox={track.get('bbox')} sensitivity={self._confidence_label()}",
                "bbox": track.get("bbox"),
                "severity": "high",
            }
        ]

    def _roi_decision_points(self, bbox) -> list[tuple[str, tuple[float, float]]]:
        if not bbox or len(bbox) != 4:
            return []

        points: list[tuple[str, tuple[float, float]]] = []
        bottom_center = bbox_bottom_center(bbox)
        centroid = bbox_centroid(bbox)

        if bottom_center is not None:
            points.append(("bottom_center", bottom_center))

        try:
            x1, y1, x2, y2 = [float(v) for v in bbox]
            height = max(0.0, y2 - y1)
            if bottom_center is not None and height > 0:
                lifted_y = max(y1, bottom_center[1] - max(12.0, height * 0.07))
                lifted_point = (bottom_center[0], lifted_y)
                if lifted_point != bottom_center:
                    points.append(("bottom_lifted", lifted_point))
        except Exception:
            pass

        if centroid is not None:
            points.append(("centroid", centroid))

        return points

    def _format_roi_decision_points(self, decision_points: list[tuple[str, tuple[float, float]]]) -> list[dict]:
        formatted = []
        for name, point in decision_points:
            formatted.append(
                {
                    "anchor": name,
                    "point": [round(float(point[0]), 3), round(float(point[1]), 3)],
                }
            )
        return formatted

    def _mode_enabled(self, mode: str) -> bool:
        if self.human_event_modes is None:
            return HUMAN_EVENT_MODE_DEFAULTS.get(mode, False)

        return mode in self.human_event_modes

    def _confidence_threshold(self) -> float:
        # O seletor de sensibilidade da câmera vira um threshold simples de confiança.
        value = str(self.human_detection_sensitivity or "medium").strip().lower()
        return HUMAN_DETECTION_SENSITIVITY_THRESHOLDS.get(value, HUMAN_DETECTION_SENSITIVITY_THRESHOLDS["medium"])

    def _confidence_value(self, track: dict) -> float | None:
        confidence = track.get("confidence")
        if confidence is None:
            return None
        try:
            return float(confidence)
        except Exception:
            return None

    def _confidence_allows(self, track: dict) -> bool:
        confidence = self._confidence_value(track)
        if confidence is None:
            return False
        return confidence >= self._confidence_threshold()

    def _confidence_label(self) -> str:
        value = str(self.human_detection_sensitivity or "medium").strip().lower()
        return {
            "very_low": "Muito sensível",
            "low": "Sensível",
            "medium": "Padrão",
            "high": "Conservador",
        }.get(value, "Padrão")

    def _loitering_seconds(self) -> float:
        try:
            value = float(self.human_loitering_seconds if self.human_loitering_seconds is not None else 10.0)
            return value if value > 0 else 10.0
        except Exception:
            return 10.0

    def _process_line(self, camera_id: int, state: TrackLifecycle, track: dict):
        if not self.line or not self._mode_enabled("line_crossing"):
            return []

        centroid = bbox_centroid(track.get("bbox"))
        if centroid is None:
            return []

        if not self._confidence_allows(track):
            return []

        start, end = self.line
        current_side = point_line_side(centroid, start, end)
        if abs(current_side) < 1e-6:
            return []

        previous_side = state.line_last_side
        state.line_last_side = current_side

        if previous_side is None or previous_side == 0:
            return []

        crossed = (previous_side < 0 < current_side) or (previous_side > 0 > current_side)
        if not crossed:
            return []

        direction = "a_to_b" if previous_side < 0 < current_side else "b_to_a"
        if self.line_direction != "any" and self.line_direction != direction:
            return []

        return [
            {
                "camera_id": camera_id,
                "event_type": f"person_crossed_line_{direction}",
                "track_id": state.stable_id,
                "confidence": track.get("confidence"),
                "details": f"Pessoa cruzou a linha ({direction})",
                "bbox": track.get("bbox"),
                "severity": "high",
            }
        ]

    def _debug_log(self, payload: dict) -> None:
        try:
            self.debug_logger.info(json.dumps(payload, ensure_ascii=False, default=str))
        except Exception:
            pass
