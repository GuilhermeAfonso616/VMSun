"""Plano de dados binário local entre workers e o pool central."""

from __future__ import annotations

import json
import os
import secrets
import socket
import stat
import struct
import threading
import time
import zlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from app.core.config import settings
from app.core.logging import get_logger


REQUEST_MAGIC = b"SUNINF01"
RESPONSE_MAGIC = b"SUNIRSP1"
PROTOCOL_VERSION = 1
REQUEST_HEADER = struct.Struct("<8sHHIIQQQiiffIIHHII")
RESPONSE_HEADER = struct.Struct("<8sHHIQII")
REQUEST_HEADER_SIZE = REQUEST_HEADER.size
RESPONSE_HEADER_SIZE = RESPONSE_HEADER.size
PAYLOAD_JPEG = 1
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

STATUS_OK = 0
STATUS_INVALID = 1
STATUS_BACKPRESSURE = 2
STATUS_ERROR = 3

VALID_MODES = {"http", "binary_prefer", "binary_strict"}
logger = get_logger("app.inference_transport")


class InferenceTransportError(RuntimeError):
    pass


class InferenceTransportUnavailable(InferenceTransportError):
    pass


class InferenceTransportBackpressure(InferenceTransportError):
    pass


def inference_transport_mode() -> str:
    mode = str(settings.inference_transport_mode or "http").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(
            "INFERENCE_TRANSPORT_MODE deve ser http, binary_prefer "
            "ou binary_strict"
        )
    return mode


def inference_transport_selected(camera_id: int) -> bool:
    if inference_transport_mode() == "http":
        return False
    raw = str(settings.inference_transport_camera_ids or "").strip()
    if raw == "*":
        return True
    selected: set[int] = set()
    for item in raw.split(","):
        try:
            value = int(item.strip())
        except ValueError:
            continue
        if value > 0:
            selected.add(value)
    return int(camera_id) in selected


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = int(size)
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise InferenceTransportUnavailable("socket de inferencia encerrado")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class InferenceTransport(ABC):
    @abstractmethod
    def submit(
        self,
        jpeg: bytes,
        *,
        width: int,
        height: int,
        offset_x: int,
        offset_y: int,
        scale_x: float,
        scale_y: float,
    ) -> tuple[list[dict], float, dict]:
        raise NotImplementedError

    @abstractmethod
    def metrics(self) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        return None


class HttpInferenceTransport(InferenceTransport):
    """Adaptador do transporte existente, preservado para rollback."""

    def __init__(self, submitter: Callable):
        self._submitter = submitter

    def submit(self, jpeg: bytes, **kwargs):
        return self._submitter(jpeg=jpeg, **kwargs)

    def metrics(self) -> dict:
        return {"inference_transport_mode": "http"}


class BinaryLocalInferenceTransport(InferenceTransport):
    def __init__(self, camera_id: int):
        self.camera_id = int(camera_id)
        self.socket_path = Path(str(settings.inference_transport_socket_path))
        self.timeout_seconds = max(
            0.2, float(settings.inference_transport_timeout_ms) / 1000.0
        )
        self.generation_id = secrets.randbits(64) or 1
        self._job_id = 0
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self.jobs_submitted_total = 0
        self.payload_bytes_total = 0
        self.fallback_total = 0
        self.errors_total = 0
        self.last_latency_ms = 0.0

    def _connect(self) -> socket.socket:
        if os.name == "nt":
            raise InferenceTransportUnavailable(
                "Unix Domain Socket indisponivel no Windows"
            )
        if self._socket is not None:
            return self._socket
        try:
            info = os.lstat(self.socket_path)
        except FileNotFoundError as exc:
            raise InferenceTransportUnavailable(
                "socket binario de inferencia ausente"
            ) from exc
        if not stat.S_ISSOCK(info.st_mode):
            raise InferenceTransportUnavailable(
                "recurso de inferencia local nao e socket"
            )
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout_seconds)
        try:
            sock.connect(str(self.socket_path))
        except OSError as exc:
            sock.close()
            raise InferenceTransportUnavailable(
                "falha ao conectar no transporte binario"
            ) from exc
        self._socket = sock
        return sock

    def _disconnect(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None

    def submit(
        self,
        jpeg: bytes,
        *,
        width: int,
        height: int,
        offset_x: int,
        offset_y: int,
        scale_x: float,
        scale_y: float,
    ) -> tuple[list[dict], float, dict]:
        payload = bytes(jpeg)
        if not payload or len(payload) > MAX_PAYLOAD_BYTES:
            raise InferenceTransportError("payload JPEG de inferencia invalido")
        if not 1 <= int(width) <= 16384 or not 1 <= int(height) <= 16384:
            raise InferenceTransportError("dimensoes de inferencia invalidas")

        started = time.perf_counter()
        with self._lock:
            self._job_id += 1
            job_id = self._job_id
            header = REQUEST_HEADER.pack(
                REQUEST_MAGIC,
                PROTOCOL_VERSION,
                REQUEST_HEADER_SIZE,
                0,
                self.camera_id,
                self.generation_id,
                job_id,
                time.monotonic_ns(),
                int(offset_x),
                int(offset_y),
                float(scale_x),
                float(scale_y),
                int(width),
                int(height),
                PAYLOAD_JPEG,
                0,
                len(payload),
                zlib.crc32(payload) & 0xFFFFFFFF,
            )
            try:
                sock = self._connect()
                sock.sendall(header)
                sock.sendall(payload)
                response_header = _recv_exact(sock, RESPONSE_HEADER_SIZE)
                (
                    magic,
                    version,
                    header_size,
                    status_code,
                    response_job_id,
                    response_size,
                    response_crc,
                ) = RESPONSE_HEADER.unpack(response_header)
                if (
                    magic != RESPONSE_MAGIC
                    or version != PROTOCOL_VERSION
                    or header_size != RESPONSE_HEADER_SIZE
                    or response_job_id != job_id
                    or response_size > MAX_RESPONSE_BYTES
                ):
                    raise InferenceTransportError(
                        "resposta binaria de inferencia incompativel"
                    )
                response_payload = _recv_exact(sock, response_size)
                if zlib.crc32(response_payload) & 0xFFFFFFFF != response_crc:
                    raise InferenceTransportError(
                        "checksum da resposta de inferencia invalido"
                    )
                parsed = json.loads(response_payload.decode("utf-8") or "{}")
            except (OSError, TimeoutError, json.JSONDecodeError) as exc:
                self._disconnect()
                self.errors_total += 1
                raise InferenceTransportUnavailable(
                    "transporte binario de inferencia indisponivel"
                ) from exc
            except InferenceTransportError:
                self._disconnect()
                self.errors_total += 1
                raise

        self.jobs_submitted_total += 1
        self.payload_bytes_total += len(payload)
        self.last_latency_ms = (time.perf_counter() - started) * 1000.0
        if status_code == STATUS_BACKPRESSURE:
            raise InferenceTransportBackpressure(
                str(parsed.get("error") or "pool central sob pressao")
            )
        if status_code != STATUS_OK or not bool(parsed.get("ok")):
            raise InferenceTransportError(
                str(parsed.get("error") or "inferencia binaria falhou")
            )
        if (
            int(parsed.get("camera_id") or 0) != self.camera_id
            or int(parsed.get("job_id") or 0) != job_id
            or int(parsed.get("generation_id") or 0) != self.generation_id
        ):
            self.errors_total += 1
            raise InferenceTransportError(
                "identidade da resposta de inferencia divergente"
            )
        tracks = parsed.get("tracks") if isinstance(parsed.get("tracks"), list) else []
        infer_ms = float(parsed.get("infer_ms") or 0.0)
        runtime = parsed.get("runtime") if isinstance(parsed.get("runtime"), dict) else {}
        runtime.update(self.metrics())
        return tracks, infer_ms, runtime

    def metrics(self) -> dict:
        return {
            "inference_transport_mode": "binary_local",
            "inference_jobs_submitted_total": self.jobs_submitted_total,
            "inference_payload_bytes_total": self.payload_bytes_total,
            "inference_transport_latency_ms": round(self.last_latency_ms, 3),
            "inference_transport_fallback_total": self.fallback_total,
            "inference_transport_errors_total": self.errors_total,
        }

    def close(self) -> None:
        with self._lock:
            self._disconnect()


class FallbackInferenceTransport(InferenceTransport):
    """Binário preferencial com fallback HTTP sempre explícito."""

    def __init__(
        self,
        binary: BinaryLocalInferenceTransport,
        http_submitter: Callable,
    ):
        self.binary = binary
        self.http_submitter = http_submitter
        self._last_log_at = 0.0

    def submit(self, jpeg: bytes, **kwargs):
        try:
            return self.binary.submit(jpeg, **kwargs)
        except InferenceTransportBackpressure:
            raise
        except InferenceTransportError as exc:
            self.binary.fallback_total += 1
            now = time.monotonic()
            if now - self._last_log_at >= 5.0:
                self._last_log_at = now
                logger.warning(
                    "Binary inference transport unavailable; explicit HTTP "
                    "fallback activated camera_id=%s reason=%s fallback_total=%s",
                    self.binary.camera_id,
                    type(exc).__name__,
                    self.binary.fallback_total,
                    extra={
                        "camera_id": self.binary.camera_id,
                        "action": "inference_transport_fallback",
                        "status": "degraded",
                        "reason": type(exc).__name__,
                    },
                )
            result = self.http_submitter(jpeg=jpeg, **kwargs)
            if len(result) == 3:
                tracks, infer_ms, runtime = result
            else:
                tracks, infer_ms = result
                runtime = {}
            runtime = {
                **runtime,
                **self.binary.metrics(),
                "inference_transport_mode": "http_fallback",
            }
            return tracks, infer_ms, runtime

    def metrics(self) -> dict:
        return self.binary.metrics()

    def close(self) -> None:
        self.binary.close()


class InferenceSocketServer:
    def __init__(self, socket_path: str | None = None):
        self.socket_path = Path(
            socket_path or str(settings.inference_transport_socket_path)
        )
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @staticmethod
    def _response(job_id: int, status_code: int, payload: dict) -> bytes:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_RESPONSE_BYTES:
            body = b'{"ok":false,"error":"resposta excedeu limite"}'
            status_code = STATUS_ERROR
        header = RESPONSE_HEADER.pack(
            RESPONSE_MAGIC,
            PROTOCOL_VERSION,
            RESPONSE_HEADER_SIZE,
            status_code,
            int(job_id),
            len(body),
            zlib.crc32(body) & 0xFFFFFFFF,
        )
        return header + body

    def _handle_connection(self, connection: socket.socket) -> None:
        connection.settimeout(
            max(0.2, float(settings.inference_transport_timeout_ms) / 1000.0)
        )
        try:
            while not self._stop.is_set():
                try:
                    header_data = _recv_exact(connection, REQUEST_HEADER_SIZE)
                except InferenceTransportUnavailable:
                    return
                (
                    magic,
                    version,
                    header_size,
                    _flags,
                    camera_id,
                    generation_id,
                    job_id,
                    _captured_ns,
                    offset_x,
                    offset_y,
                    scale_x,
                    scale_y,
                    width,
                    height,
                    payload_format,
                    _reserved,
                    payload_size,
                    payload_crc,
                ) = REQUEST_HEADER.unpack(header_data)
                if (
                    magic != REQUEST_MAGIC
                    or version != PROTOCOL_VERSION
                    or header_size != REQUEST_HEADER_SIZE
                    or camera_id <= 0
                    or not 1 <= width <= 16384
                    or not 1 <= height <= 16384
                    or payload_format != PAYLOAD_JPEG
                    or not 0 < payload_size <= MAX_PAYLOAD_BYTES
                ):
                    connection.sendall(
                        self._response(
                            job_id,
                            STATUS_INVALID,
                            {"ok": False, "error": "cabecalho de inferencia invalido"},
                        )
                    )
                    return
                payload = _recv_exact(connection, payload_size)
                if zlib.crc32(payload) & 0xFFFFFFFF != payload_crc:
                    connection.sendall(
                        self._response(
                            job_id,
                            STATUS_INVALID,
                            {"ok": False, "error": "checksum JPEG invalido"},
                        )
                    )
                    continue
                frame = cv2.imdecode(
                    np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR
                )
                if (
                    frame is None
                    or frame.shape[1] != width
                    or frame.shape[0] != height
                ):
                    connection.sendall(
                        self._response(
                            job_id,
                            STATUS_INVALID,
                            {"ok": False, "error": "frame JPEG invalido"},
                        )
                    )
                    continue
                try:
                    from app.runtime.inference_pool import get_inference_pool_group

                    tracks, infer_ms, runtime = get_inference_pool_group().infer(
                        camera_id=camera_id,
                        infer_frame=frame,
                        offset_x=offset_x,
                        offset_y=offset_y,
                        scale_x=scale_x,
                        scale_y=scale_y,
                    )
                    response = self._response(
                        job_id,
                        STATUS_OK,
                        {
                            "ok": True,
                            "camera_id": camera_id,
                            "job_id": job_id,
                            "generation_id": generation_id,
                            "tracks": tracks,
                            "infer_ms": infer_ms,
                            "runtime": runtime,
                        },
                    )
                except TimeoutError as exc:
                    response = self._response(
                        job_id,
                        STATUS_BACKPRESSURE,
                        {"ok": False, "error": str(exc)[:500]},
                    )
                except Exception as exc:
                    logger.exception(
                        "Binary inference failed camera_id=%s job_id=%s",
                        camera_id,
                        job_id,
                        extra={
                            "camera_id": camera_id,
                            "action": "inference_transport_server",
                            "status": "error",
                            "reason": type(exc).__name__,
                        },
                    )
                    response = self._response(
                        job_id,
                        STATUS_ERROR,
                        {"ok": False, "error": str(exc)[:500]},
                    )
                connection.sendall(response)
        except (OSError, InferenceTransportError):
            return
        finally:
            try:
                connection.close()
            except OSError:
                pass

    def _serve(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                connection, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                daemon=True,
                name="binary-inference-client",
            ).start()

    def start(self) -> None:
        if inference_transport_mode() == "http" or self._thread is not None:
            return
        if os.name == "nt":
            if inference_transport_mode() == "binary_strict":
                raise RuntimeError("transporte binario estrito requer Unix")
            logger.warning("Binary inference transport disabled on Windows")
            return
        self.socket_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            info = os.lstat(self.socket_path)
            if not stat.S_ISSOCK(info.st_mode):
                raise RuntimeError("caminho do socket de inferencia e inseguro")
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.1)
            try:
                probe.connect(str(self.socket_path))
            except OSError:
                self.socket_path.unlink()
            else:
                raise RuntimeError("outro servidor de inferencia esta ativo")
            finally:
                probe.close()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o660)
        sock.listen(32)
        sock.settimeout(0.5)
        self._socket = sock
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve,
            daemon=True,
            name="binary-inference-server",
        )
        self._thread.start()
        logger.info(
            "Binary inference transport listening path=%s",
            self.socket_path,
            extra={
                "action": "inference_transport_server",
                "status": "running",
                "reason": "socket_ready",
            },
        )

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        try:
            if self.socket_path.exists() and stat.S_ISSOCK(
                os.lstat(self.socket_path).st_mode
            ):
                self.socket_path.unlink()
        except OSError:
            pass


inference_socket_server = InferenceSocketServer()
