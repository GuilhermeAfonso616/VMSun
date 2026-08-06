"""Mantem uma sessao leve de preview MJPEG para cameras paradas ou em ajuste.

Este servico evita abrir o worker completo quando a tela so precisa de uma
pre-visualizacao e uma ultima imagem do RTSP.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import cv2

from app.camera.rtsp_capture import RTSPCapture
from app.core.logging import get_camera_logger
from app.services.display_resize import normalize_display_frame


class _PreviewSession:
    IDLE_TIMEOUT_SECONDS = 20.0
    JPEG_QUALITY = 68
    PREVIEW_WIDTH = 640
    PREVIEW_HEIGHT = 360

    def __init__(self, camera_id: int, rtsp_url: str):
        self.camera_id = int(camera_id)
        self.rtsp_url = rtsp_url

        self.logger = get_camera_logger(
            "app.preview_stream",
            camera_id=self.camera_id,
            worker_mode="preview",
        )

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._consumers = 0
        self._last_consumer_ts = time.monotonic()

        self._last_jpeg: bytes | None = None
        self._last_frame_ts = 0.0
        self._last_error = "Conectando..."
        self._frame_counter = 0
        self._fps_window_start = time.monotonic()
        self._current_fps = 0.0

    def update_rtsp_url(self, rtsp_url: str):
        with self._lock:
            self.rtsp_url = rtsp_url

    def acquire(self):
        with self._lock:
            self._consumers += 1
            self._last_consumer_ts = time.monotonic()

        self._ensure_started()

    def release(self):
        with self._lock:
            if self._consumers > 0:
                self._consumers -= 1
            self._last_consumer_ts = time.monotonic()

    def get_jpeg(self) -> bytes | None:
        with self._lock:
            return self._last_jpeg

    def get_error(self) -> str:
        with self._lock:
            return self._last_error

    def get_stats(self) -> dict[str, float | str | bool]:
        with self._lock:
            last_frame_age = 0.0
            if self._last_frame_ts > 0:
                last_frame_age = max(0.0, time.monotonic() - self._last_frame_ts)
            return {
                "fps": round(float(self._current_fps or 0.0), 2),
                "last_frame_age_seconds": round(last_frame_age, 2),
                "has_frame": bool(self._last_jpeg),
                "last_error": self._last_error,
            }

    def _ensure_started(self):
        thread = self._thread
        if thread and thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"preview-camera-{self.camera_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def is_idle_expired(self) -> bool:
        with self._lock:
            if self._consumers > 0:
                return False
            return (time.monotonic() - self._last_consumer_ts) >= self.IDLE_TIMEOUT_SECONDS

    def _run(self):
        capture: RTSPCapture | None = None
        self.logger.info("Preview session iniciada")

        try:
            while not self._stop_event.is_set():
                if self.is_idle_expired():
                    self.logger.info("Preview session encerrada por inatividade")
                    break

                if capture is None:
                    try:
                        capture = RTSPCapture(self.rtsp_url)
                        capture.open()
                        with self._lock:
                            self._last_error = ""
                    except Exception as exc:
                        with self._lock:
                            self._last_error = str(exc)
                        time.sleep(1.0)
                        continue

                try:
                    ok, frame = capture.read_latest(drop_frames=2)
                    if not ok or frame is None:
                        raise RuntimeError("Sem frames do RTSP no preview")

                    # O preview da UI usa o mesmo letterbox do editor de ROI.
                    display_frame = normalize_display_frame(
                        frame,
                        width=self.PREVIEW_WIDTH,
                        height=self.PREVIEW_HEIGHT,
                    )

                    ok, encoded = cv2.imencode(
                        ".jpg",
                        display_frame,
                        [int(cv2.IMWRITE_JPEG_QUALITY), self.JPEG_QUALITY],
                    )
                    if not ok:
                        time.sleep(0.03)
                        continue

                    jpeg_bytes = encoded.tobytes()
                    with self._lock:
                        self._last_jpeg = jpeg_bytes
                        self._last_frame_ts = time.monotonic()
                        self._last_error = ""
                        self._frame_counter += 1
                        elapsed = self._last_frame_ts - self._fps_window_start
                        if elapsed >= 1.0:
                            self._current_fps = round(self._frame_counter / elapsed, 2)
                            self._fps_window_start = self._last_frame_ts
                            self._frame_counter = 0

                    time.sleep(0.03)

                except Exception as exc:
                    with self._lock:
                        self._last_error = str(exc)

                    try:
                        capture.release()
                    except Exception:
                        pass
                    capture = None
                    time.sleep(0.5)

        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass

            self.logger.info("Preview session finalizada")


class PreviewStreamManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[int, _PreviewSession] = {}

    def acquire(self, camera_id: int, rtsp_url: str):
        with self._lock:
            session = self._sessions.get(int(camera_id))
            if session is None:
                session = _PreviewSession(camera_id=int(camera_id), rtsp_url=rtsp_url)
                self._sessions[int(camera_id)] = session
            else:
                session.update_rtsp_url(rtsp_url)

        session.acquire()

    def release(self, camera_id: int):
        session = None
        with self._lock:
            session = self._sessions.get(int(camera_id))

        if session is None:
            return

        session.release()

        if session.is_idle_expired():
            session.stop()
            with self._lock:
                current = self._sessions.get(int(camera_id))
                if current is session:
                    self._sessions.pop(int(camera_id), None)

    def get_jpeg(self, camera_id: int) -> bytes | None:
        with self._lock:
            session = self._sessions.get(int(camera_id))
        if session is None:
            return None
        return session.get_jpeg()

    def get_error(self, camera_id: int) -> str:
        with self._lock:
            session = self._sessions.get(int(camera_id))
        if session is None:
            return "Preview inativo"
        return session.get_error()

    def get_stats(self, camera_id: int) -> dict[str, float | str | bool]:
        with self._lock:
            session = self._sessions.get(int(camera_id))
        if session is None:
            return {
                "fps": 0.0,
                "last_frame_age_seconds": 0.0,
                "has_frame": False,
                "last_error": "Preview inativo",
            }
        return session.get_stats()

    def stop(self, camera_id: int):
        with self._lock:
            session = self._sessions.pop(int(camera_id), None)
        if session is not None:
            session.stop()

    def stop_all(self):
        with self._lock:
            sessions = list(self._sessions.items())
            self._sessions.clear()

        for _, session in sessions:
            session.stop()


preview_stream_manager = PreviewStreamManager()
