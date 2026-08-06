import os
import time
from urllib.parse import urlsplit

import cv2

from app.core.config import settings
from app.core.logging import get_logger
from app.core.url_safety import sanitize_url_for_log


def mask_rtsp(rtsp_url: str | None) -> str | None:
    return sanitize_url_for_log(rtsp_url)


def _is_internal_media_backbone_url(rtsp_url: str | None) -> bool:
    try:
        candidate = urlsplit(str(rtsp_url or "").strip())
        backbone = urlsplit(str(settings.webrtc_gateway_rtsp_base_url or "").strip())
    except Exception:
        return False
    if not candidate.hostname or not backbone.hostname:
        return False
    candidate_port = candidate.port or (322 if candidate.scheme == "rtsps" else 554)
    backbone_port = backbone.port or (322 if backbone.scheme == "rtsps" else 554)
    return (
        candidate.scheme.lower() in {"rtsp", "rtsps"}
        and candidate.scheme.lower() == backbone.scheme.lower()
        and candidate.hostname.lower() == backbone.hostname.lower()
        and candidate_port == backbone_port
    )


class RTSPCapture:
    def __init__(
        self,
        rtsp_url: str,
        *,
        allow_gateway_exclusive_probe: bool = False,
        allow_transport_fallback: bool = True,
    ):
        self.rtsp_url = rtsp_url
        # Runtime workers must respect gateway-only mode so they do not keep a
        # second, permanent connection open against the camera/NVR. Short-lived
        # discovery probes are explicitly allowed by their caller.
        self.allow_gateway_exclusive_probe = bool(allow_gateway_exclusive_probe)
        self.allow_transport_fallback = bool(allow_transport_fallback)
        self.cap = None
        self.logger = get_logger("app.rtsp_capture")
        self.consecutive_read_failures = 0
        self.last_read_error_log_ts = 0.0

    def _normalized_transport(self) -> str:
        transport = str(settings.rtsp_transport or "tcp").strip().lower()
        if transport not in {"udp", "tcp"}:
            transport = "tcp"
        return transport

    def _apply_opencv_timeouts(self):
        if self.cap is None:
            return

        open_timeout_prop = getattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC", None)
        read_timeout_prop = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)

        try:
            if open_timeout_prop is not None:
                self.cap.set(open_timeout_prop, float(settings.rtsp_open_timeout_ms))
        except Exception:
            pass

        try:
            if read_timeout_prop is not None:
                self.cap.set(read_timeout_prop, float(settings.rtsp_read_timeout_ms))
        except Exception:
            pass

        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

    def _build_ffmpeg_capture_options(self, transport: str) -> str:
        parts = [
            f"rtsp_transport;{transport}",
            f"stimeout;{int(settings.rtsp_open_timeout_ms) * 1000}",
            f"rw_timeout;{int(settings.rtsp_read_timeout_ms) * 1000}",
            f"max_delay;{int(settings.rtsp_max_delay_us)}",
        ]

        if settings.rtsp_enable_low_latency:
            parts.extend([
                "fflags;nobuffer",
                "flags;low_delay",
            ])

        return "|".join(parts)

    def _apply_ffmpeg_log_settings(self):
        log_level = str(getattr(settings, "rtsp_ffmpeg_log_level", "error") or "error").strip().lower()
        if log_level not in {"quiet", "panic", "fatal", "error", "warning", "info", "verbose", "debug", "trace"}:
            log_level = "error"

        os.environ["OPENCV_FFMPEG_LOGLEVEL"] = log_level
        os.environ["OPENCV_LOG_LEVEL"] = "ERROR"

    def _open_once(self, transport: str):
        ffmpeg_options = self._build_ffmpeg_capture_options(transport)
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = ffmpeg_options
        self._apply_ffmpeg_log_settings()

        started = time.perf_counter()
        masked_url = mask_rtsp(self.rtsp_url)

        self.logger.info(
            "Tentando abrir RTSP (vídeo apenas; áudio ignorado no MVP): %s transport=%s open_timeout_ms=%s read_timeout_ms=%s ffmpeg_log_level=%s",
            masked_url,
            transport,
            settings.rtsp_open_timeout_ms,
            settings.rtsp_read_timeout_ms,
            settings.rtsp_ffmpeg_log_level,
        )

        # Importante:
        # NÃO passar capture_params no construtor do VideoCapture.
        # Em algumas builds do OpenCV/FFmpeg (como a sua no Windows),
        # isso gera:
        # "VIDEOIO/FFMPEG: unsupported parameters in .open()"
        self.cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)

        self._apply_opencv_timeouts()

        if not self.cap or not self.cap.isOpened():
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self.logger.error(
                "Falha ao abrir RTSP em %.2f ms: %s transport=%s",
                elapsed_ms,
                masked_url,
                transport,
            )
            return False

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.consecutive_read_failures = 0
        self.last_read_error_log_ts = 0.0

        self.logger.info(
            "RTSP aberto com sucesso em %.2f ms transport=%s",
            elapsed_ms,
            transport,
        )
        return True

    def open(self):
        if (
            settings.camera_gateway_enabled
            and settings.camera_gateway_worker_capture_enabled
            and not settings.camera_gateway_worker_rtsp_fallback_enabled
            and not self.allow_gateway_exclusive_probe
            and not _is_internal_media_backbone_url(self.rtsp_url)
        ):
            raise RuntimeError(
                "Direct RTSP capture is disabled while gateway worker capture is enabled without fallback"
            )

        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            finally:
                self.cap = None

        preferred_transport = self._normalized_transport()
        masked_url = mask_rtsp(self.rtsp_url)

        # tenta primeiro no transporte configurado
        if self._open_once(preferred_transport):
            return

        if not self.allow_transport_fallback:
            raise RuntimeError(
                f"Não foi possível abrir RTSP com transporte={preferred_transport}: {masked_url}"
            )

        # fallback automático para o outro transporte
        fallback_transport = "udp" if preferred_transport == "tcp" else "tcp"
        self.logger.warning(
            "Falha ao abrir RTSP com transporte=%s. Tentando fallback transporte=%s url=%s",
            preferred_transport,
            fallback_transport,
            masked_url,
        )

        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            finally:
                self.cap = None

        if self._open_once(fallback_transport):
            return

        raise RuntimeError(f"Não foi possível abrir RTSP: {masked_url}")

    def read_latest(self, drop_frames: int = 2):
        if self.cap is None:
            raise RuntimeError("Capture ainda não foi aberto")

        safe_drop_frames = max(0, int(drop_frames))

        for _ in range(safe_drop_frames):
            grabbed = self.cap.grab()
            if not grabbed:
                break

        ok, frame = self.cap.read()

        if not ok or frame is None:
            self.consecutive_read_failures += 1
            now = time.perf_counter()

            if (
                self.consecutive_read_failures == 1
                or self.consecutive_read_failures % 10 == 0
                or (now - self.last_read_error_log_ts) >= 5.0
            ):
                self.logger.warning(
                    "Falha ao ler frame do RTSP. consecutive_read_failures=%s url=%s",
                    self.consecutive_read_failures,
                    mask_rtsp(self.rtsp_url),
                )
                self.last_read_error_log_ts = now

            return False, None

        if self.consecutive_read_failures > 0:
            self.logger.info(
                "Leitura do RTSP recuperada após %s falhas consecutivas",
                self.consecutive_read_failures,
            )
            self.consecutive_read_failures = 0

        return True, frame

    def release(self):
        if self.cap is not None:
            try:
                self.cap.release()
            finally:
                self.cap = None
            self.logger.info("RTSP liberado")

    def reopen_with_retry(self, retry_seconds: int = 5):
        self.logger.warning(
            "Reabrindo RTSP após falha. retry_seconds=%s url=%s",
            retry_seconds,
            mask_rtsp(self.rtsp_url),
        )
        self.release()
        time.sleep(retry_seconds)
        self.open()
