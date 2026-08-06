from __future__ import annotations

from pathlib import Path
import json
import shutil
import subprocess
from datetime import datetime

import cv2
import numpy as np

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timezone import now_brazil_naive


class EventSnapshotStore:
    def __init__(self):
        self.base_dir = Path(settings.event_snapshots_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.logger = get_logger("app.event_snapshot_store")

    def save(self, camera_id: int, frame, event_type: str, track_id: int | None, bbox=None) -> str | None:
        if frame is None or not settings.save_event_snapshots:
            return None

        timestamp = now_brazil_naive().strftime("%Y%m%d_%H%M%S_%f")
        track_label = track_id if track_id is not None else "na"
        camera_dir = self.base_dir / f"camera_{camera_id}"
        camera_dir.mkdir(parents=True, exist_ok=True)
        file_path = camera_dir / f"{event_type}_track_{track_label}_{timestamp}.jpg"

        # Keep the persisted event snapshot clean. Revalidators, exports, and
        # datasets consume this same file; UI routes draw the bbox on demand.
        output = frame.copy()

        ok = cv2.imwrite(str(file_path), output)
        if not ok:
            return None
        return str(file_path).replace("\\", "/")

    @staticmethod
    def draw_bbox(frame, bbox, *, color=(0, 165, 255), thickness: int = 2):
        output = frame.copy()
        if bbox and len(bbox) == 4:
            height, width = output.shape[:2]
            x1, y1, x2, y2 = [int(round(float(v))) for v in bbox]
            x1 = max(0, min(width - 1, x1))
            x2 = max(0, min(width - 1, x2))
            y1 = max(0, min(height - 1, y1))
            y2 = max(0, min(height - 1, y2))
            if x2 > x1 and y2 > y1:
                cv2.rectangle(output, (x1, y1), (x2, y2), color, max(1, int(thickness)))
        return output

    @staticmethod
    def _build_video_timeline(
        frame_count: int,
        offsets: list[float] | None,
        fps: float,
    ) -> list[int]:
        """Indice do frame de origem que ocupa cada quadro do arquivo final.

        O ring de evidencias amostra com jitter (o worker roda entre 1 e 3 fps),
        entao escrever um quadro por frame com fps fixo comprime a linha do
        tempo: um evento de 16s virava um clipe de 8s, acelerado. Aqui cada
        frame passa a ocupar os quadros correspondentes ao tempo real em que
        ficou na tela, e o clipe dura o mesmo que o trecho gravado.

        Sem offsets confiaveis mantemos o espacamento uniforme antigo."""
        if frame_count <= 0:
            return []
        if fps <= 0 or not offsets or len(offsets) != frame_count:
            return list(range(frame_count))
        ordered = [float(value) for value in offsets]
        span = ordered[-1] - ordered[0]
        if span <= 0:
            return list(range(frame_count))
        # Teto defensivo: um relogio fora de ordem nao pode gerar um clipe gigante.
        slot_count = min(int(round(span * fps)) + 1, 1800)
        timeline: list[int] = []
        cursor = 0
        for slot in range(max(slot_count, 1)):
            slot_at = ordered[0] + slot / fps
            while cursor + 1 < frame_count and ordered[cursor + 1] <= slot_at:
                cursor += 1
            timeline.append(cursor)
        return timeline

    @staticmethod
    def _decode_video_frame(frame):
        if isinstance(frame, (bytes, bytearray, memoryview)):
            payload = np.frombuffer(frame, dtype=np.uint8)
            if payload.size == 0:
                return None
            return cv2.imdecode(payload, cv2.IMREAD_COLOR)
        return frame if frame is not None and hasattr(frame, "shape") else None

    def save_clip_pair(
        self,
        camera_id: int,
        frame_before,
        frame_after,
        event_type: str,
        track_id: int | None,
        bbox=None,
        captured_at_before: datetime | None = None,
        captured_at_event: datetime | None = None,
        captured_at_after: datetime | None = None,
        video_frames: list | None = None,
        video_frame_offsets: list[float] | None = None,
    ) -> str | None:
        if frame_before is None and frame_after is None:
            return None
        if not settings.save_event_snapshots:
            return None

        timestamp = now_brazil_naive().strftime("%Y%m%d_%H%M%S_%f")
        track_label = track_id if track_id is not None else "na"
        clip_dir = self.base_dir / f"camera_{camera_id}" / f"clip_{event_type}_track_{track_label}_{timestamp}"
        clip_dir.mkdir(parents=True, exist_ok=True)

        def _write(name: str, frame) -> bool:
            if frame is None:
                return False
            output = self.draw_bbox(frame, bbox, color=(0, 255, 255), thickness=2)
            return bool(cv2.imwrite(str(clip_dir / name), output))

        wrote_before = _write("before.jpg", frame_before)
        wrote_after = _write("after.jpg", frame_after)
        if not (wrote_before or wrote_after):
            return None

        video_path = None
        video_codec = None
        video_frame_count = 0
        output_fps = max(0.1, float(settings.event_clip_video_fps or 2.0))
        source_frame_count = 0
        timeline_kind = "uniform"
        if bool(settings.event_clip_video_enabled):
            # Frame e offset andam juntos: descartar um sem o outro desalinharia
            # a linha do tempo do clipe.
            paired = [
                (frame, offset)
                for frame, offset in zip(
                    video_frames or [],
                    list(video_frame_offsets or []) + [None] * len(video_frames or []),
                )
                if frame is not None
            ]
            frames = [frame for frame, _ in paired]
            offsets = [offset for _, offset in paired]
            source_frame_count = len(frames)
            if frames:
                usable_offsets = offsets if all(o is not None for o in offsets) else None
                timeline = self._build_video_timeline(len(frames), usable_offsets, output_fps)
                if usable_offsets and (usable_offsets[-1] - usable_offsets[0]) > 0:
                    timeline_kind = "real"
                candidate_path = clip_dir / "clip.mp4"
                temp_path = clip_dir / "clip.source.mp4"
                writer = None
                width = 0
                height = 0
                decoded_index = -1
                decoded_frame = None
                for index in timeline:
                    if index != decoded_index:
                        decoded_frame = self._decode_video_frame(frames[index])
                        decoded_index = index
                    current = decoded_frame
                    if current is None:
                        continue
                    if writer is None:
                        source_height, source_width = current.shape[:2]
                        width = max(2, int(source_width) - (int(source_width) % 2))
                        height = max(2, int(source_height) - (int(source_height) % 2))
                        writer = cv2.VideoWriter(
                            str(temp_path),
                            cv2.VideoWriter_fourcc(*"mp4v"),
                            output_fps,
                            (int(width), int(height)),
                        )
                        if not writer.isOpened():
                            writer.release()
                            writer = None
                            break
                    if current.shape[:2] != (height, width):
                        current = cv2.resize(current, (int(width), int(height)))
                        decoded_frame = current
                    writer.write(current)
                    video_frame_count += 1

                if writer is not None:
                    writer.release()
                    if temp_path.exists() and temp_path.stat().st_size > 0:
                        if self._transcode_browser_mp4(temp_path, candidate_path):
                            video_codec = "h264"
                        else:
                            temp_path.replace(candidate_path)
                            video_codec = "mpeg4"
                    if candidate_path.exists() and candidate_path.stat().st_size > 0:
                        video_path = candidate_path
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass

        def _iso(value: datetime | None) -> str | None:
            return value.isoformat() if isinstance(value, datetime) else None

        def _offset(value: datetime | None) -> float | None:
            if not isinstance(value, datetime) or not isinstance(captured_at_event, datetime):
                return None
            return round((value - captured_at_event).total_seconds(), 3)

        metadata = {
            "version": 1,
            "before_captured_at": _iso(captured_at_before),
            "event_captured_at": _iso(captured_at_event),
            "after_captured_at": _iso(captured_at_after),
            "before_offset_seconds": _offset(captured_at_before),
            "after_offset_seconds": _offset(captured_at_after),
            "video_file": video_path.name if video_path else None,
            "video_frame_count": video_frame_count,
            "video_fps": output_fps if video_path else None,
            "video_codec": video_codec,
            # Quantos frames o ring realmente capturou e como eles foram
            # distribuidos no arquivo ("real" = na velocidade do evento).
            "video_source_frame_count": source_frame_count if video_path else None,
            "video_timeline": timeline_kind if video_path else None,
            "video_duration_seconds": (
                round(video_frame_count / output_fps, 3) if video_path and output_fps > 0 else None
            ),
        }
        try:
            (clip_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return str(clip_dir).replace("\\", "/")

    def _transcode_browser_mp4(self, source_path: Path, target_path: Path) -> bool:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False

        temp_target = target_path.with_suffix(".h264.tmp.mp4")
        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_path),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(temp_target),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        except Exception as exc:
            self.logger.warning(
                "Falha ao transcodificar clipe para H.264",
                extra={"action": "event_clip_transcode", "status": "error", "reason": exc.__class__.__name__},
            )
            return False

        if result.returncode != 0 or not temp_target.exists() or temp_target.stat().st_size <= 0:
            stderr = (result.stderr or "").strip()
            self.logger.warning(
                "FFmpeg nao gerou clipe H.264 compativel",
                extra={
                    "action": "event_clip_transcode",
                    "status": "error",
                    "reason": "ffmpeg_failed",
                    "details": stderr[:500],
                },
            )
            if temp_target.exists():
                try:
                    temp_target.unlink()
                except Exception:
                    pass
            return False

        temp_target.replace(target_path)
        return True

    @staticmethod
    def bbox_to_json(bbox) -> str | None:
        if bbox is None:
            return None
        try:
            return json.dumps([float(v) for v in bbox])
        except Exception:
            return None


event_snapshot_store = EventSnapshotStore()
