from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..detection.types import DetectionCandidate
from ..scene.geometry import bbox_footpoint, iou, point_distance


@dataclass(slots=True)
class AssociationMatch:
    track_id: int
    detection_index: int
    score: float
    method: str


class AssociationStrategy(Protocol):
    def match(
        self,
        tracks: list,
        detections: list[DetectionCandidate],
        *,
        iou_threshold: float,
        reid_threshold: float,
    ) -> list[AssociationMatch]:
        ...


def _cosine_similarity(vec_a: list[float] | None, vec_b: list[float] | None) -> float:
    if not vec_a or not vec_b:
        return -1.0
    a = np.asarray(vec_a, dtype=float)
    b = np.asarray(vec_b, dtype=float)
    if a.size == 0 or b.size == 0 or a.size != b.size:
        return -1.0
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 0:
        return -1.0
    return float(np.dot(a, b) / denom)


class MultiStageAssociator:
    """Association in layers using prediction, IoU, distance and embedding."""

    def match(
        self,
        tracks: list,
        detections: list[DetectionCandidate],
        *,
        iou_threshold: float,
        reid_threshold: float,
        max_distance_px: float = 120.0,
        weak_distance_px: float = 180.0,
    ) -> list[AssociationMatch]:
        matches: list[AssociationMatch] = []
        if not tracks or not detections:
            return matches

        unmatched_track_ids = {track.track_id for track in tracks}
        unmatched_det_indices = set(range(len(detections)))

        strong_candidates = [idx for idx, det in enumerate(detections) if det.score >= 0.35]
        matches.extend(
            self._greedy_match(
                tracks,
                detections,
                strong_candidates,
                unmatched_track_ids,
                unmatched_det_indices,
                iou_threshold=iou_threshold,
                reid_threshold=reid_threshold,
                max_distance_px=max_distance_px,
                use_embedding=True,
                use_fallback_distance=True,
            )
        )

        weak_candidates = [idx for idx in unmatched_det_indices if detections[idx].score >= 0.18]
        matches.extend(
            self._greedy_match(
                tracks,
                detections,
                weak_candidates,
                unmatched_track_ids,
                unmatched_det_indices,
                iou_threshold=max(0.15, iou_threshold * 0.8),
                reid_threshold=reid_threshold,
                max_distance_px=weak_distance_px,
                use_embedding=False,
                use_fallback_distance=True,
            )
        )

        return matches

    def _greedy_match(
        self,
        tracks: list,
        detections: list[DetectionCandidate],
        candidate_indices: list[int],
        unmatched_track_ids: set[int],
        unmatched_det_indices: set[int],
        *,
        iou_threshold: float,
        reid_threshold: float,
        max_distance_px: float,
        use_embedding: bool,
        use_fallback_distance: bool,
    ) -> list[AssociationMatch]:
        local_matches: list[AssociationMatch] = []
        scored_pairs: list[tuple[float, int, int, str]] = []

        for track in tracks:
            if track.track_id not in unmatched_track_ids:
                continue
            predicted = track.kalman.peek_prediction() if getattr(track, "kalman", None) else None
            predicted_point = predicted.point if predicted is not None else track.footpoint_current
            predicted_bbox = track.bbox_current
            if predicted_point is None or predicted_bbox is None:
                continue

            for det_index in candidate_indices:
                if det_index not in unmatched_det_indices:
                    continue
                det = detections[det_index]
                det_fp = bbox_footpoint(det.bbox)
                iou_score = iou(predicted_bbox, det.bbox) if predicted_bbox else 0.0
                distance = point_distance(predicted_point, (det_fp.x, det_fp.y))
                distance_score = max(0.0, 1.0 - (distance / float(max(1.0, max_distance_px))))
                track_quality = getattr(track, "track_quality", 0.5)
                class_bonus = self._class_compatibility(track, det)

                if class_bonus <= 0.15:
                    continue

                score = 0.0
                method = "distance"
                if iou_score >= iou_threshold:
                    score = 0.45 * iou_score + 0.25 * distance_score + 0.20 * det.score + 0.10 * track_quality
                    method = "iou"
                elif use_embedding and getattr(track, "embedding_mean", None) and getattr(det, "embedding", None):
                    similarity = _cosine_similarity(track.embedding_mean, det.embedding)
                    if similarity >= reid_threshold:
                        score = 0.55 * similarity + 0.20 * distance_score + 0.15 * track_quality + 0.10 * det.score
                        method = "reid"
                elif use_fallback_distance:
                    score = 0.60 * distance_score + 0.25 * det.score + 0.15 * track_quality
                    if score < 0.2:
                        continue

                score *= class_bonus
                if score <= 0.0:
                    continue
                scored_pairs.append((score, track.track_id, det_index, method))

        scored_pairs.sort(key=lambda item: item[0], reverse=True)

        used_tracks: set[int] = set()
        used_dets: set[int] = set()
        for score, track_id, det_index, method in scored_pairs:
            if track_id in used_tracks or det_index in used_dets:
                continue
            if track_id not in unmatched_track_ids or det_index not in unmatched_det_indices:
                continue
            used_tracks.add(track_id)
            used_dets.add(det_index)
            unmatched_track_ids.discard(track_id)
            unmatched_det_indices.discard(det_index)
            local_matches.append(AssociationMatch(track_id=track_id, detection_index=det_index, score=score, method=method))

        return local_matches

    def _class_compatibility(self, track, det: DetectionCandidate) -> float:
        dominant = getattr(track, "dominant_class", None)
        if dominant is None or not getattr(det, "class_name", None):
            return 1.0
        if dominant == det.class_name:
            return 1.0
        votes = getattr(track, "class_votes", {}) or {}
        total = sum(votes.values()) or 1
        dominant_ratio = max(votes.values()) / float(total) if votes else 0.0
        if dominant_ratio >= 0.8:
            return 0.25
        if dominant_ratio >= 0.6:
            return 0.55
        return 0.8
