"""Versao simplificada/legada das regras de eventos.

Mantida para compatibilidade com caminhos antigos do projeto e para facilitar
comparacao com a regra operacional mais nova em event_rules.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import hypot

from app.analytics.spatial import bbox_centroid, point_in_polygon, point_line_side
from app.core.config import settings
from app.core.timezone import utc_now_naive


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


class EventRules:
    def __init__(
        self,
        roi_polygon: list[tuple[int, int]] | None = None,
        line: tuple[tuple[int, int], tuple[int, int]] | None = None,
        line_direction: str = "any",
    ):
        self.roi_polygon = roi_polygon or []
        self.line = line
        self.line_direction = line_direction or "any"

        # raw track_id do tracker -> estado lógico
        self.active_tracks: dict[int, TrackLifecycle] = {}

        # tracks que sumiram recentemente e ainda podem ser reconciliados
        self.lost_tracks: dict[int, TrackLifecycle] = {}

        # id estável interno para eventos
        self.next_stable_id = 1

    def process(self, camera_id: int, tracks: list[dict]):
        generated: list[dict] = []
        now = utc_now_naive()
        current_raw_ids: set[int] = set()

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

            if not self.roi_polygon and self._should_emit_enter(state, track, now):
                state.entered_emitted = True
                generated.append(
                    {
                        "camera_id": camera_id,
                        "event_type": "person_entered",
                        "track_id": state.stable_id,
                        "confidence": track.get("confidence"),
                        "details": f"Pessoa entrou na cena. bbox={track.get('bbox')}",
                        "bbox": track.get("bbox"),
                    }
                )

            if state.entered_emitted:
                generated.extend(self._process_roi(camera_id, state, track))
                generated.extend(self._process_line(camera_id, state, track))

        self._move_missing_active_tracks_to_lost(current_raw_ids, now)
        generated.extend(self._finalize_lost_tracks(camera_id, now))
        return generated

    def _create_new_track(self, now: datetime) -> TrackLifecycle:
        state = TrackLifecycle(
            stable_id=self.next_stable_id,
            first_seen=now,
            last_seen=now,
            seen_frames=0,
            entered_emitted=False,
            last_bbox=None,
            roi_inside=False,
            line_last_side=None,
            missing_since=None,
            missing_frames=0,
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

        confidence = track.get("confidence")
        if confidence is not None and float(confidence) < float(settings.weak_detection_threshold):
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

    def _finalize_lost_tracks(self, camera_id: int, now: datetime) -> list[dict]:
        generated: list[dict] = []
        timeout = timedelta(seconds=float(settings.track_exit_timeout_seconds))
        min_missing_frames = max(1, int(settings.track_exit_min_missing_frames))

        for stable_id, state in list(self.lost_tracks.items()):
            if state.missing_since is None:
                state.missing_since = state.last_seen

            if state.missing_frames < min_missing_frames:
                state.missing_frames += 1
                continue

            if (now - state.missing_since) < timeout:
                continue

            if state.entered_emitted:
                generated.append(
                    {
                        "camera_id": camera_id,
                        "event_type": "person_left",
                        "track_id": state.stable_id,
                        "confidence": None,
                        "details": "Pessoa saiu da cena",
                        "bbox": None,
                    }
                )

                if state.roi_inside:
                    generated.append(
                        {
                            "camera_id": camera_id,
                            "event_type": "person_left_roi",
                            "track_id": state.stable_id,
                            "confidence": None,
                            "details": "Pessoa saiu da ROI",
                            "bbox": None,
                        }
                    )

            self.lost_tracks.pop(stable_id, None)

        return generated

    def _process_roi(self, camera_id: int, state: TrackLifecycle, track: dict):
        if len(self.roi_polygon) < 3:
            return []

        centroid = bbox_centroid(track.get("bbox"))
        if centroid is None:
            return []

        is_inside = point_in_polygon(centroid, self.roi_polygon)
        was_inside = state.roi_inside
        state.roi_inside = is_inside

        if is_inside and not was_inside:
            state.entered_emitted = True
            return [
                {
                    "camera_id": camera_id,
                    "event_type": "person_entered_roi",
                    "track_id": state.stable_id,
                    "confidence": track.get("confidence"),
                    "details": "Pessoa entrou na ROI",
                    "bbox": track.get("bbox"),
                }
            ]

        if was_inside and not is_inside:
            return [
                {
                    "camera_id": camera_id,
                    "event_type": "person_left_roi",
                    "track_id": state.stable_id,
                    "confidence": track.get("confidence"),
                    "details": "Pessoa saiu da ROI",
                    "bbox": track.get("bbox"),
                }
            ]

        return []

    def _process_line(self, camera_id: int, state: TrackLifecycle, track: dict):
        if not self.line:
            return []

        centroid = bbox_centroid(track.get("bbox"))
        if centroid is None:
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
            }
        ]
