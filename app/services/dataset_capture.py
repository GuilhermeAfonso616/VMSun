import os
import time
import cv2

from app.core.config import settings


class DatasetCaptureService:
    def __init__(self):
        self._last_saved_at = {}

    def _ensure_dir(self, path: str):
        os.makedirs(path, exist_ok=True)

    def _can_save(self, camera_id: int, category: str) -> bool:
        now = time.time()
        key = f"{camera_id}:{category}"
        last = self._last_saved_at.get(key, 0)

        if now - last >= settings.save_interval_seconds:
            self._last_saved_at[key] = now
            return True
        return False

    def save_frame(self, camera_id: int, category: str, frame, extra_text: str = ""):
        if not settings.save_debug_frames:
            return

        if frame is None:
            return

        if not self._can_save(camera_id, category):
            return

        base_dir = settings.debug_frames_dir
        target_dir = os.path.join(base_dir, f"camera_{camera_id}", category)
        self._ensure_dir(target_dir)

        ts = time.strftime("%Y%m%d_%H%M%S")
        ms = int((time.time() % 1) * 1000)

        output = frame.copy()
        label = f"{category}"
        if extra_text:
            label += f" | {extra_text}"

        cv2.putText(
            output,
            label,
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 255),
            2,
            cv2.LINE_AA
        )

        filename = os.path.join(target_dir, f"{ts}_{ms}.jpg")
        cv2.imwrite(filename, output)


dataset_capture = DatasetCaptureService()