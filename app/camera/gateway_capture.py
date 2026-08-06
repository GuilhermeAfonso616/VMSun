from __future__ import annotations

import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import cv2
import numpy as np

from app.core.config import settings
from app.core.logging import get_logger
from app.core.url_safety import mask_url_credentials
from app.services.camera_gateway_client import gateway_camera_url, register_camera_source


class GatewayMJPEGCapture:
    """Capture compatível com RTSPCapture, mas consumindo MJPEG do camera-gateway.

    O worker Python continua recebendo frames BGR do OpenCV, porém a conexão RTSP,
    reconexão e buffer de último frame ficam no gateway Go.
    """

    def __init__(self, camera_id: int, source_url: str):
        self.camera_id = camera_id
        self.source_url = source_url
        self.stream_url = gateway_camera_url(camera_id, "/stream/live")
        self.status_url = gateway_camera_url(camera_id, "/status")
        self.logger = get_logger("app.gateway_capture")

        self.cap = None
        self.consecutive_read_failures = 0
        self.last_read_error_log_ts = 0.0

        self._lock = threading.Lock()
        self._frame_ready = threading.Condition(self._lock)
        self._latest_frame = None
        self._latest_seq = 0
        self._last_returned_seq = 0
        self._reader_thread: threading.Thread | None = None
        self._running = False
        self._last_connect_log_ts = 0.0

    def open(self):
        self.release()

        registered = register_camera_source(
            self.camera_id,
            self.source_url,
            timeout_seconds=max(1.0, float(settings.camera_gateway_register_timeout_seconds)),
        )
        if not registered:
            raise RuntimeError(f"camera gateway source registration failed camera_id={self.camera_id}")

        self._running = True
        self.cap = object()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"gateway-capture-{self.camera_id}",
            daemon=True,
        )
        self._reader_thread.start()

        deadline = time.monotonic() + max(1.0, float(settings.camera_gateway_first_frame_timeout_seconds))
        with self._frame_ready:
            while self._running and self._latest_frame is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._frame_ready.wait(timeout=min(0.25, remaining))

            if self._latest_frame is None:
                self.release()
                raise RuntimeError(
                    f"camera gateway did not deliver first frame within "
                    f"{settings.camera_gateway_first_frame_timeout_seconds}s camera_id={self.camera_id}"
                )

        self.logger.info(
            "Gateway capture opened camera_id=%s stream_url=%s",
            self.camera_id,
            mask_url_credentials(self.stream_url),
        )

    def _reader_loop(self) -> None:
        reconnect_delay = max(0.2, float(settings.camera_gateway_reader_reconnect_delay_seconds))
        chunk_size = max(4096, int(settings.camera_gateway_reader_chunk_size_bytes))

        while self._running:
            try:
                self._read_stream_once(chunk_size=chunk_size)
            except Exception as exc:
                self.consecutive_read_failures += 1
                now = time.perf_counter()
                if (
                    self.consecutive_read_failures == 1
                    or self.consecutive_read_failures % 10 == 0
                    or (now - self.last_read_error_log_ts) >= 5.0
                ):
                    self.logger.warning(
                        "Gateway stream read failed camera_id=%s failures=%s error=%s",
                        self.camera_id,
                        self.consecutive_read_failures,
                        exc,
                    )
                    self.last_read_error_log_ts = now

            if self._running:
                time.sleep(reconnect_delay)

    def _read_stream_once(self, *, chunk_size: int) -> None:
        request = Request(self.stream_url, headers={"Accept": "multipart/x-mixed-replace,image/jpeg,*/*"})
        timeout = max(1.0, float(settings.camera_gateway_stream_timeout_seconds))

        now = time.perf_counter()
        if (now - self._last_connect_log_ts) >= 30.0:
            self.logger.info(
                "Opening gateway MJPEG stream camera_id=%s url=%s",
                self.camera_id,
                mask_url_credentials(self.stream_url),
            )
            self._last_connect_log_ts = now

        try:
            response_ctx = urlopen(request, timeout=timeout)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"gateway stream open failed: {exc}") from exc

        with response_ctx as response:
            status = int(getattr(response, "status", 200) or 200)
            if status >= 400:
                raise RuntimeError(f"gateway stream returned HTTP {status}")

            buffer = bytearray()
            while self._running:
                chunk = response.read(chunk_size)
                if not chunk:
                    raise RuntimeError("gateway stream ended")
                buffer.extend(chunk)

                while True:
                    start = buffer.find(b"\xff\xd8")
                    if start < 0:
                        if len(buffer) > 2:
                            del buffer[:-2]
                        break

                    end = buffer.find(b"\xff\xd9", start + 2)
                    if end < 0:
                        if start > 0:
                            del buffer[:start]
                        break

                    jpeg = bytes(buffer[start : end + 2])
                    del buffer[: end + 2]
                    self._store_jpeg(jpeg)

                if len(buffer) > int(settings.camera_gateway_reader_max_buffer_bytes):
                    keep = max(2, int(settings.camera_gateway_reader_chunk_size_bytes))
                    del buffer[:-keep]

    def _store_jpeg(self, jpeg: bytes) -> None:
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return

        with self._frame_ready:
            self._latest_frame = frame
            self._latest_seq += 1
            self.consecutive_read_failures = 0
            self._frame_ready.notify_all()

    def read_latest(self, drop_frames: int = 2):
        if self.cap is None:
            raise RuntimeError("Gateway capture ainda não foi aberto")

        deadline = time.monotonic() + max(0.1, float(settings.camera_gateway_worker_read_timeout_seconds))
        with self._frame_ready:
            while self._running and self._latest_seq == self._last_returned_seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._frame_ready.wait(timeout=min(0.05, remaining))

            if self._latest_frame is None:
                self.consecutive_read_failures += 1
                return False, None

            # Se não chegou frame novo no prazo, devolvemos falha para o health/reconnect
            # perceber o stall em vez de reprocessar o mesmo frame indefinidamente.
            if self._latest_seq == self._last_returned_seq:
                self.consecutive_read_failures += 1
                return False, None

            self._last_returned_seq = self._latest_seq
            return True, self._latest_frame.copy()

    def release(self):
        self._running = False
        self.cap = None
        with self._frame_ready:
            self._frame_ready.notify_all()

        thread = self._reader_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._reader_thread = None

        with self._frame_ready:
            self._latest_frame = None
            self._latest_seq = 0
            self._last_returned_seq = 0

    def reopen_with_retry(self, retry_seconds: int = 5):
        self.release()
        time.sleep(max(0, retry_seconds))
        self.open()
