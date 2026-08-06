"""Servico de deteccao e adaptador para inferencia local ou central."""

from __future__ import annotations

from typing import Any

import cv2

from app.analytics.detector import PersonDetector
from app.core.config import settings
from app.core.logging import get_inference_pool_logger


pool_logger = get_inference_pool_logger()


def is_detector_engine_failure(error: BaseException) -> bool:
    text = f"{type(error).__name__}: {error}".lower()
    return any(
        token in text
        for token in (
            "create_execution_context",
            "execution context",
            "tensorrt",
            "cuda error",
            "illegal memory access",
            "engine is none",
            "nonetype",
            # Ultralytics/TensorRT can surface a poisoned engine as a bare
            # AttributeError (for example: ``AttributeError: bn``).
            "attributeerror: bn",
        )
    )


def _disable_corrupt_detector_engine() -> None:
    """Prevent a broken cached engine from being selected on every retry."""
    import os

    engine_path = str(getattr(settings, "detector_engine_path", "") or "").strip()
    if engine_path and os.path.exists(engine_path):
        try:
            os.remove(engine_path)
        except OSError as exc:
            pool_logger.warning("Falha ao remover engine do detector: %s", exc)
    settings.detector_engine_path = ""
    os.environ.pop("DETECTOR_ENGINE_PATH", None)
    PersonDetector.reset_shared_model()


class InferenceBackpressureError(TimeoutError):
    """A central pool could not accept this frame, but the camera worker is healthy."""


def _get_inference_pool():
    """Resolve o pool tardiamente para evitar ciclo entre deteccao e coordenacao."""
    from app.runtime.inference_pool import get_inference_pool

    return get_inference_pool()


class DetectionService:
    def __init__(
        self,
        *,
        camera_id: int | None = None,
        use_pool: bool | None = None,
        force_pytorch: bool = False,
    ):
        self.camera_id = int(camera_id or 0)
        self.use_pool = bool(settings.inference_pool_enabled) if use_pool is None else bool(use_pool)
        self.pool_backend = self._pool_backend()
        self._force_pytorch_detector = bool(force_pytorch)
        self.detector = None if self.use_pool else PersonDetector(force_pytorch=self._force_pytorch_detector)
        self._detector_engine_failures = 0
        self._binary_transport = None
        self._last_runtime_stats: dict[str, Any] = {
            "mode": "pool" if self.use_pool else "direct",
            "backend": self.pool_backend if self.use_pool else "direct",
        }

    @staticmethod
    def _pool_backend() -> str:
        backend = str(getattr(settings, "inference_pool_backend", "local") or "local").strip().lower()
        if backend in {"central", "remote", "runtime", "runtime_api"}:
            return "central"
        return "local"

    @staticmethod
    def _central_url() -> str:
        configured = str(getattr(settings, "inference_pool_central_url", "") or "").strip().rstrip("/")
        if configured:
            return configured
        return f"http://127.0.0.1:{int(settings.app_port)}/internal/inference/track"

    @classmethod
    def _central_release_url(cls, camera_id: int) -> str:
        track_url = cls._central_url().rstrip("/")
        base_url = track_url[:-len("/track")] if track_url.endswith("/track") else track_url
        return f"{base_url}/cameras/{int(camera_id)}"

    def release_camera(self) -> dict[str, Any]:
        """Libera atribuicao/cache sem interromper uma inferencia GPU em andamento."""
        if self._binary_transport is not None:
            self._binary_transport.close()
            self._binary_transport = None
        if self.camera_id <= 0 or not self.use_pool:
            return {"ok": True, "camera_id": self.camera_id, "released": False, "reason": "pool_not_used"}

        if self.pool_backend == "central":
            from urllib.error import HTTPError, URLError
            from urllib.request import Request, urlopen

            request = Request(self._central_release_url(self.camera_id), method="DELETE")
            try:
                with urlopen(request, timeout=max(0.5, float(settings.inference_pool_job_timeout_seconds))) as response:
                    return {
                        "ok": int(getattr(response, "status", 200) or 200) < 400,
                        "camera_id": self.camera_id,
                        "released": True,
                        "backend": "central",
                    }
            except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
                pool_logger.warning(
                    "event=release_camera_failed camera_id=%s backend=central error=%s",
                    self.camera_id,
                    exc,
                    extra={
                        "camera_id": self.camera_id,
                        "action": "inference_pool_release_camera",
                        "status": "degraded",
                        "reason": "central_release_failed",
                    },
                )
                return {
                    "ok": False,
                    "camera_id": self.camera_id,
                    "released": False,
                    "backend": "central",
                    "error": str(exc),
                }

        from app.runtime.inference_pool import release_inference_camera

        return {
            "ok": True,
            "camera_id": self.camera_id,
            "released": True,
            "backend": "local",
            "result": release_inference_camera(self.camera_id),
        }

    def infer(self, infer_frame, offset_x: int = 0, offset_y: int = 0, scale_x: float = 1.0, scale_y: float = 1.0):
        if self.use_pool and bool(settings.inference_pool_enabled):
            if self.pool_backend == "central":
                try:
                    return self._infer_central(
                        infer_frame,
                        offset_x=offset_x,
                        offset_y=offset_y,
                        scale_x=scale_x,
                        scale_y=scale_y,
                    )
                except Exception:
                    if not bool(getattr(settings, "inference_pool_central_fallback_direct", False)):
                        raise
                    tracks, infer_ms = self._infer_direct(
                        infer_frame,
                        offset_x=offset_x,
                        offset_y=offset_y,
                        scale_x=scale_x,
                        scale_y=scale_y,
                    )
                    self._last_runtime_stats = {"enabled": False, "mode": "direct", "backend": "fallback_direct"}
                    return tracks, infer_ms
            pool = _get_inference_pool()
            result = pool.infer(
                camera_id=self.camera_id,
                infer_frame=infer_frame,
                offset_x=offset_x,
                offset_y=offset_y,
                scale_x=scale_x,
                scale_y=scale_y,
            )
            self._last_runtime_stats = pool.stats()
            return result
        tracks, infer_ms = self._infer_direct(
            infer_frame,
            offset_x=offset_x,
            offset_y=offset_y,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        self._last_runtime_stats = {"enabled": False, "mode": "direct", "backend": "direct"}
        return tracks, infer_ms

    def _infer_central(self, infer_frame, offset_x: int = 0, offset_y: int = 0, scale_x: float = 1.0, scale_y: float = 1.0):
        from app.runtime.inference_transport import (
            BinaryLocalInferenceTransport,
            InferenceTransportBackpressure,
            InferenceTransportError,
            inference_transport_mode,
            inference_transport_selected,
        )

        if not inference_transport_selected(self.camera_id):
            return self._infer_central_http(
                infer_frame,
                offset_x=offset_x,
                offset_y=offset_y,
                scale_x=scale_x,
                scale_y=scale_y,
            )

        quality = max(
            40,
            min(
                95,
                int(
                    getattr(
                        settings,
                        "inference_pool_central_jpeg_quality",
                        80,
                    )
                ),
            ),
        )
        ok, encoded = cv2.imencode(
            ".jpg",
            infer_frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), quality],
        )
        if not ok:
            raise RuntimeError("falha ao codificar frame para inferencia central")
        if self._binary_transport is None:
            self._binary_transport = BinaryLocalInferenceTransport(self.camera_id)
        try:
            tracks, infer_ms, runtime = self._binary_transport.submit(
                encoded.tobytes(),
                width=int(infer_frame.shape[1]),
                height=int(infer_frame.shape[0]),
                offset_x=offset_x,
                offset_y=offset_y,
                scale_x=scale_x,
                scale_y=scale_y,
            )
        except InferenceTransportBackpressure as exc:
            self._last_runtime_stats = {
                "enabled": True,
                "mode": "pool",
                "backend": "central",
                **self._binary_transport.metrics(),
                "backpressure": True,
            }
            raise InferenceBackpressureError(str(exc)) from exc
        except InferenceTransportError as exc:
            mode = inference_transport_mode()
            if mode == "binary_strict":
                self._last_runtime_stats = {
                    "enabled": True,
                    "mode": "pool",
                    "backend": "central",
                    **self._binary_transport.metrics(),
                    "transport_degraded": True,
                }
                pool_logger.warning(
                    "Binary inference strict mode blocked HTTP fallback "
                    "camera_id=%s reason=%s",
                    self.camera_id,
                    type(exc).__name__,
                    extra={
                        "camera_id": self.camera_id,
                        "action": "inference_transport_strict",
                        "status": "degraded",
                        "reason": type(exc).__name__,
                    },
                )
                raise RuntimeError(
                    "transporte binario de inferencia obrigatorio indisponivel"
                ) from exc
            self._binary_transport.fallback_total += 1
            pool_logger.warning(
                "Binary inference transport unavailable; explicit HTTP "
                "fallback activated camera_id=%s reason=%s fallback_total=%s",
                self.camera_id,
                type(exc).__name__,
                self._binary_transport.fallback_total,
                extra={
                    "camera_id": self.camera_id,
                    "action": "inference_transport_fallback",
                    "status": "degraded",
                    "reason": type(exc).__name__,
                },
            )
            tracks, infer_ms = self._infer_central_http(
                infer_frame,
                offset_x=offset_x,
                offset_y=offset_y,
                scale_x=scale_x,
                scale_y=scale_y,
            )
            self._last_runtime_stats.update(self._binary_transport.metrics())
            self._last_runtime_stats["inference_transport_mode"] = "http_fallback"
            return tracks, infer_ms

        runtime.update(
            {
                "enabled": True,
                "mode": "pool",
                "backend": "central",
                "central_jpeg_quality": quality,
            }
        )
        self._last_runtime_stats = runtime
        return tracks, infer_ms

    def _infer_central_http(self, infer_frame, offset_x: int = 0, offset_y: int = 0, scale_x: float = 1.0, scale_y: float = 1.0):
        import base64
        import json
        import time
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        quality = max(40, min(95, int(getattr(settings, "inference_pool_central_jpeg_quality", 80))))
        ok, encoded = cv2.imencode(".jpg", infer_frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            raise RuntimeError("falha ao codificar frame para inferencia central")

        payload = {
            "camera_id": self.camera_id,
            "image_b64": base64.b64encode(encoded.tobytes()).decode("ascii"),
            "offset_x": int(offset_x),
            "offset_y": int(offset_y),
            "scale_x": float(scale_x),
            "scale_y": float(scale_y),
        }
        request = Request(
            self._central_url(),
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        timeout = max(0.2, float(settings.inference_pool_job_timeout_seconds) + 0.5)
        started = time.perf_counter()
        pool_logger.info(
            "event=central_submit camera_id=%s url=%s jpeg_bytes=%s quality=%s timeout_seconds=%.2f",
            self.camera_id,
            self._central_url(),
            len(encoded),
            quality,
            timeout,
            extra={
                "camera_id": self.camera_id,
                "action": "inference_pool_central_submit",
                "status": "submitted",
                "reason": "http_request",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                parsed = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
        except HTTPError as exc:
            http_ms = round((time.perf_counter() - started) * 1000.0, 2)
            if int(getattr(exc, "code", 0) or 0) in {429, 502, 503, 504}:
                pool_logger.warning(
                    "event=central_backpressure camera_id=%s url=%s http_ms=%.2f status_code=%s error=%s",
                    self.camera_id,
                    self._central_url(),
                    http_ms,
                    exc.code,
                    exc,
                    extra={
                        "camera_id": self.camera_id,
                        "action": "inference_pool_central_backpressure",
                        "status": "deferred",
                        "reason": f"http_{exc.code}",
                    },
                )
                self._last_runtime_stats = {
                    "enabled": True,
                    "mode": "pool",
                    "backend": "central",
                    "central_http_ms": http_ms,
                    "central_jpeg_quality": quality,
                    "backpressure": True,
                    "http_status": int(getattr(exc, "code", 0) or 0),
                }
                raise InferenceBackpressureError(
                    f"pool central indisponivel temporariamente (HTTP {exc.code})"
                ) from exc
            pool_logger.error(
                "event=central_error camera_id=%s url=%s http_ms=%.2f status_code=%s error=%s",
                self.camera_id,
                self._central_url(),
                http_ms,
                exc.code,
                exc,
                extra={
                    "camera_id": self.camera_id,
                    "action": "inference_pool_central_error",
                    "status": "error",
                    "reason": f"http_{exc.code}",
                },
            )
            raise
        except (URLError, TimeoutError, OSError) as exc:
            http_ms = round((time.perf_counter() - started) * 1000.0, 2)
            pool_logger.warning(
                "event=central_unavailable camera_id=%s url=%s http_ms=%.2f error_type=%s error=%s",
                self.camera_id,
                self._central_url(),
                http_ms,
                type(exc).__name__,
                exc,
                extra={
                    "camera_id": self.camera_id,
                    "action": "inference_pool_central_unavailable",
                    "status": "deferred",
                    "reason": type(exc).__name__,
                },
            )
            self._last_runtime_stats = {
                "enabled": True,
                "mode": "pool",
                "backend": "central",
                "central_http_ms": http_ms,
                "central_jpeg_quality": quality,
                "backpressure": True,
                "error_type": type(exc).__name__,
            }
            raise InferenceBackpressureError("runtime central indisponivel temporariamente") from exc
        except Exception as exc:
            pool_logger.error(
                "event=central_error camera_id=%s url=%s http_ms=%.2f error_type=%s error=%s",
                self.camera_id,
                self._central_url(),
                (time.perf_counter() - started) * 1000.0,
                type(exc).__name__,
                exc,
                extra={
                    "camera_id": self.camera_id,
                    "action": "inference_pool_central_error",
                    "status": "error",
                    "reason": type(exc).__name__,
                },
            )
            raise
        if not bool(parsed.get("ok", False)):
            error = str(parsed.get("error") or "inferencia central falhou")
            pool_logger.error(
                "event=central_error camera_id=%s url=%s http_ms=%.2f error=%s",
                self.camera_id,
                self._central_url(),
                (time.perf_counter() - started) * 1000.0,
                error,
                extra={
                    "camera_id": self.camera_id,
                    "action": "inference_pool_central_error",
                    "status": "error",
                    "reason": "response_not_ok",
                },
            )
            raise RuntimeError(error)

        tracks = parsed.get("tracks") if isinstance(parsed.get("tracks"), list) else []
        infer_ms = float(parsed.get("infer_ms") or 0.0)
        runtime = parsed.get("runtime") if isinstance(parsed.get("runtime"), dict) else {}
        runtime.update({
            "enabled": True,
            "mode": "pool",
            "backend": "central",
            "central_http_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "central_jpeg_quality": quality,
            "inference_transport_mode": "http",
        })
        self._last_runtime_stats = runtime
        pool_logger.info(
            "event=central_complete camera_id=%s pool_id=%s tracks_count=%s http_ms=%s queue_size=%s submitted=%s completed=%s timed_out=%s rejected=%s dropped_oldest=%s stale_dropped=%s",
            self.camera_id,
            runtime.get("pool_id", "-"),
            len(tracks or []),
            runtime.get("central_http_ms"),
            runtime.get("queue_size", "-"),
            runtime.get("submitted", "-"),
            runtime.get("completed", "-"),
            runtime.get("timed_out", "-"),
            runtime.get("rejected", "-"),
            runtime.get("dropped_oldest", "-"),
            runtime.get("stale_dropped", "-"),
            extra={
                "camera_id": self.camera_id,
                "action": "inference_pool_central_complete",
                "status": "completed",
                "reason": "http_ok",
            },
        )
        return tracks, infer_ms

    def _infer_direct(self, infer_frame, offset_x: int = 0, offset_y: int = 0, scale_x: float = 1.0, scale_y: float = 1.0):
        import time

        if self.detector is None:
            self.detector = PersonDetector(force_pytorch=self._force_pytorch_detector)
        started = time.perf_counter()
        try:
            results = self.detector.track(infer_frame)
        except BaseException as exc:
            if not is_detector_engine_failure(exc):
                raise
            self._detector_engine_failures += 1

            pool_logger.warning(
                "event=detector_fallback_pytorch camera_id=%s error_type=%s error=%s failures=%s",
                self.camera_id,
                type(exc).__name__,
                exc,
                self._detector_engine_failures,
                extra={
                    "camera_id": self.camera_id,
                    "action": "detector_runtime_fallback",
                    "status": "degraded",
                    "reason": "engine_failure_fallback_pytorch",
                },
            )

            _disable_corrupt_detector_engine()
            self.detector = None

            if bool(getattr(settings, "detector_engine_auto_build_required", False)):
                raise
            if not bool(getattr(settings, "detector_engine_runtime_fallback_enabled", True)):
                raise

            self._force_pytorch_detector = True
            self.detector = PersonDetector(force_pytorch=True)
            results = self.detector.track(infer_frame)
        infer_ms = (time.perf_counter() - started) * 1000
        tracks = self._extract_tracks_with_offset(
            results,
            offset_x=offset_x,
            offset_y=offset_y,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        # O downstream so consome 'tracks'. Liberamos o objeto Results (que
        # carrega tensores/arrays do frame) aqui mesmo, em vez de devolve-lo
        # e mante-lo vivo ate a proxima iteracao do loop do worker.
        del results
        return tracks, infer_ms

    def runtime_stats(self) -> dict[str, Any]:
        if self.use_pool and bool(settings.inference_pool_enabled) and self.pool_backend == "local":
            try:
                self._last_runtime_stats = _get_inference_pool().stats()
            except Exception:
                pass
        return dict(self._last_runtime_stats)

    def _extract_tracks_with_offset(self, results, offset_x=0, offset_y=0, scale_x: float = 1.0, scale_y: float = 1.0):
        output = []
        if not results:
            return output
        if not hasattr(results[0], "boxes") or results[0].boxes is None:
            return output

        boxes = results[0].boxes
        xyxy = boxes.xyxy.cpu().tolist() if boxes.xyxy is not None else []
        confs = boxes.conf.cpu().tolist() if boxes.conf is not None else []
        ids = boxes.id.cpu().tolist() if boxes.id is not None else []

        for i, bbox in enumerate(xyxy):
            try:
                track_id = int(ids[i]) if i < len(ids) and ids[i] is not None else -1
            except Exception:
                track_id = -1
            try:
                conf = float(confs[i]) if i < len(confs) and confs[i] is not None else None
            except Exception:
                conf = None

            x1, y1, x2, y2 = bbox
            x1 = float(x1) * float(scale_x)
            y1 = float(y1) * float(scale_y)
            x2 = float(x2) * float(scale_x)
            y2 = float(y2) * float(scale_y)

            output.append({
                "track_id": track_id,
                "confidence": conf,
                "bbox": [x1 + offset_x, y1 + offset_y, x2 + offset_x, y2 + offset_y],
            })
        return output
