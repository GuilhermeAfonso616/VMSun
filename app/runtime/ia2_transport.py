"""Transporte binário local da IA2 — Etapa 3B.

Canal dedicado (`/run/sunorus/ia2.sock`), separado do socket da IA1 da Etapa 2B,
justamente para que IA2 e IA1 não compartilhem fila nem sofram head-of-line
blocking uma por causa da outra.

Diferença deliberada em relação à Etapa 2B: o payload aqui é **BGR cru**, não
JPEG. Recomprimir o recorte introduziria perda e quebraria a equivalência entre
execução local e central, que é requisito duro desta etapa. Recortes de pessoa
são pequenos, então o custo de banda é aceitável.

Layout da requisição (little-endian, offsets explícitos, sem padding implícito):

| Offset | Bytes | Tipo | Campo |
|---:|---:|---|---|
| 0 | 8 | bytes | magic `SUNIA201` |
| 8 | 2 | uint16 | protocol_version |
| 10 | 2 | uint16 | header_size = 84 |
| 12 | 4 | uint32 | camera_id |
| 16 | 16 | bytes | job_id (uuid) |
| 32 | 8 | int64 | frame_id (-1 = ausente) |
| 40 | 8 | int64 | generation_id do worker (-1 = ausente) |
| 48 | 8 | int64 | track_id (-1 = ausente) |
| 56 | 8 | uint64 | deadline monotônico (ns, 0 = sem deadline) |
| 64 | 4 | int32 | priority |
| 68 | 2 | uint16 | crop_height |
| 70 | 2 | uint16 | crop_width |
| 72 | 2 | uint16 | crop_channels |
| 74 | 2 | uint16 | quality_size |
| 76 | 4 | uint32 | payload_size |
| 80 | 4 | uint32 | CRC32 do payload |
| 84 | quality_size | bytes | quality em JSON |
| … | payload_size | bytes | recorte BGR cru |

Resposta: cabeçalho de 40 bytes (magic `SUNIA2RS`, versão, header_size, status,
job_id de 16 bytes, body_size, CRC32) seguido de um corpo JSON pequeno com o
resultado e a identidade repetida. JSON aqui é aceitável: são metadados, não
imagem.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import struct
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import settings
from app.core.logging import get_logger


logger = get_logger("app.runtime.ia2_transport")

REQUEST_MAGIC = b"SUNIA201"
RESPONSE_MAGIC = b"SUNIA2RS"
PROTOCOL_VERSION = 1
REQUEST_HEADER = struct.Struct("<8sHHI16sqqqQiHHHHII")
RESPONSE_HEADER = struct.Struct("<8sHHI16sII")
REQUEST_HEADER_SIZE = REQUEST_HEADER.size
RESPONSE_HEADER_SIZE = RESPONSE_HEADER.size

MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_QUALITY_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 1 * 1024 * 1024
MAX_CROP_DIM = 8192

STATUS_OK = 0
STATUS_INVALID = 1
STATUS_QUEUE_FULL = 2
STATUS_TIMEOUT = 3
STATUS_UNAVAILABLE = 4
STATUS_ERROR = 5

VALID_MODES = {"http", "binary_prefer", "binary_strict"}


class IA2TransportError(RuntimeError):
    pass


class IA2TransportUnavailable(IA2TransportError):
    pass


class IA2TransportQueueFull(IA2TransportError):
    pass


class IA2TransportTimeout(IA2TransportError):
    pass


def ia2_transport_mode() -> str:
    mode = str(settings.ia2_transport_mode or "http").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError("IA2_TRANSPORT_MODE deve ser http, binary_prefer ou binary_strict")
    return mode


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise IA2TransportUnavailable("conexao encerrada pelo par")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _job_id_bytes(job_id: str) -> bytes:
    try:
        return uuid.UUID(hex=str(job_id)).bytes
    except Exception:
        return str(job_id).encode("utf-8")[:16].ljust(16, b"\0")


class IA2BinaryTransport:
    """Cliente do worker: envia o recorte BGR para a pool central."""

    def __init__(self, socket_path: str | None = None) -> None:
        self.socket_path = Path(socket_path or str(settings.ia2_transport_socket_path))
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self.jobs_submitted_total = 0
        self.payload_bytes_total = 0
        self.errors_total = 0
        self.last_latency_ms = 0.0
        self.last_queue_wait_ms = 0.0

    def _connect(self) -> socket.socket:
        if self._socket is not None:
            return self._socket
        if not hasattr(socket, "AF_UNIX"):
            raise IA2TransportUnavailable("unix socket indisponivel nesta plataforma")
        try:
            info = os.lstat(self.socket_path)
        except OSError as exc:
            raise IA2TransportUnavailable("socket da IA2 ausente") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISSOCK(info.st_mode):
            raise IA2TransportUnavailable("caminho do socket da IA2 invalido")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(max(0.2, float(settings.ia2_transport_timeout_ms) / 1000.0))
        try:
            sock.connect(str(self.socket_path))
        except OSError as exc:
            sock.close()
            raise IA2TransportUnavailable("falha ao conectar no socket da IA2") from exc
        self._socket = sock
        return sock

    def close(self) -> None:
        with self._lock:
            if self._socket is not None:
                try:
                    self._socket.close()
                except Exception:
                    pass
                self._socket = None

    def submit(self, request: Any, crop: Any, quality: dict[str, Any] | None) -> dict[str, Any]:
        """Envia um recorte e devolve o dicionário de resultado da pool."""
        array = np.ascontiguousarray(crop)
        if array.ndim != 3 or array.shape[2] not in (1, 3):
            raise IA2TransportError("formato de recorte nao suportado")
        height, width, channels = array.shape
        if not (1 <= width <= MAX_CROP_DIM and 1 <= height <= MAX_CROP_DIM):
            raise IA2TransportError("dimensoes de recorte fora do limite")
        payload = array.tobytes()
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise IA2TransportError("recorte maior que a capacidade do transporte")
        quality_bytes = json.dumps(quality or {}, separators=(",", ":"), default=str).encode("utf-8")
        if len(quality_bytes) > MAX_QUALITY_BYTES:
            quality_bytes = b"{}"

        header = REQUEST_HEADER.pack(
            REQUEST_MAGIC,
            PROTOCOL_VERSION,
            REQUEST_HEADER_SIZE,
            int(request.camera_id),
            _job_id_bytes(request.job_id),
            int(request.frame_id) if request.frame_id is not None else -1,
            int(request.generation_id) if request.generation_id is not None else -1,
            int(request.track_id) if isinstance(request.track_id, int) else -1,
            int(request.deadline_monotonic_ns or 0),
            int(request.priority),
            int(height),
            int(width),
            int(channels),
            len(quality_bytes),
            len(payload),
            zlib.crc32(payload) & 0xFFFFFFFF,
        )

        started = time.perf_counter()
        with self._lock:
            try:
                sock = self._connect()
                sock.sendall(header)
                sock.sendall(quality_bytes)
                sock.sendall(payload)
                response_header = _recv_exact(sock, RESPONSE_HEADER_SIZE)
            except (OSError, IA2TransportUnavailable) as exc:
                self.errors_total += 1
                self.close()
                raise IA2TransportUnavailable(str(exc)) from exc

            (
                magic,
                version,
                header_size,
                status,
                job_bytes,
                body_size,
                body_crc,
            ) = RESPONSE_HEADER.unpack(response_header)
            if magic != RESPONSE_MAGIC or version != PROTOCOL_VERSION or header_size != RESPONSE_HEADER_SIZE:
                self.errors_total += 1
                self.close()
                raise IA2TransportError("resposta da IA2 incompativel")
            if body_size > MAX_RESPONSE_BYTES:
                self.errors_total += 1
                self.close()
                raise IA2TransportError("resposta da IA2 maior que o limite")
            body = _recv_exact(sock, body_size) if body_size else b""

        if zlib.crc32(body) & 0xFFFFFFFF != body_crc:
            self.errors_total += 1
            raise IA2TransportError("checksum da resposta da IA2 invalido")
        if job_bytes != _job_id_bytes(request.job_id):
            self.errors_total += 1
            raise IA2TransportError("identidade da resposta da IA2 divergente")

        if status == STATUS_QUEUE_FULL:
            raise IA2TransportQueueFull("fila da pool IA2 cheia")
        if status == STATUS_TIMEOUT:
            raise IA2TransportTimeout("timeout na pool IA2")
        if status == STATUS_UNAVAILABLE:
            raise IA2TransportUnavailable("pool IA2 indisponivel")
        if status != STATUS_OK:
            self.errors_total += 1
            raise IA2TransportError("pool IA2 retornou erro")

        parsed = json.loads(body.decode("utf-8")) if body else {}
        self.jobs_submitted_total += 1
        self.payload_bytes_total += len(payload)
        self.last_latency_ms = (time.perf_counter() - started) * 1000.0
        self.last_queue_wait_ms = float(parsed.get("queue_wait_ms") or 0.0)
        return parsed

    def metrics(self) -> dict[str, Any]:
        return {
            "ia2_transport_mode": "binary_local",
            "ia2_jobs_submitted_total": self.jobs_submitted_total,
            "ia2_payload_bytes_total": self.payload_bytes_total,
            "ia2_transport_latency_ms": round(self.last_latency_ms, 3),
            "ia2_transport_errors_total": self.errors_total,
            "ia2_last_queue_wait_ms": round(self.last_queue_wait_ms, 3),
        }


class IA2SocketServer:
    """Servidor no processo do runtime: recebe recortes e chama a pool."""

    def __init__(self, socket_path: str | None = None, pool: Any = None) -> None:
        self.socket_path = Path(socket_path or str(settings.ia2_transport_socket_path))
        self._pool = pool
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def _resolve_pool(self):
        if self._pool is not None:
            return self._pool
        from app.runtime.ia2_pool import get_ia2_pool

        return get_ia2_pool()

    def start(self) -> None:
        if not bool(settings.ia2_pool_enabled):
            return
        if not hasattr(socket, "AF_UNIX"):
            logger.warning(
                "Socket da IA2 indisponivel nesta plataforma; pool central inacessivel",
                extra={
                    "action": "ia2_socket_unsupported",
                    "status": "degraded",
                    "reason": "af_unix_missing",
                },
            )
            return
        self.socket_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            info = os.lstat(self.socket_path)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISSOCK(info.st_mode):
                raise RuntimeError("caminho do socket da IA2 ocupado por arquivo invalido")
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(0.2)
                probe.connect(str(self.socket_path))
            except OSError:
                self.socket_path.unlink()
            else:
                probe.close()
                raise RuntimeError("socket da IA2 ja esta em uso")
            finally:
                try:
                    probe.close()
                except Exception:
                    pass

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o660)
        sock.listen(16)
        sock.settimeout(0.5)
        self._socket = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, name="ia2-socket", daemon=True)
        self._thread.start()
        logger.info(
            "Servidor de socket da IA2 iniciado path=%s",
            self.socket_path,
            extra={
                "action": "ia2_socket_started",
                "status": "running",
                "reason": "listen",
            },
        )

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        try:
            if self.socket_path.exists():
                self.socket_path.unlink()
        except Exception:
            pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set() and self._socket is not None:
            try:
                connection, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                name="ia2-socket-conn",
                daemon=True,
            ).start()

    @staticmethod
    def _response(job_bytes: bytes, status: int, payload: dict[str, Any]) -> bytes:
        body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        header = RESPONSE_HEADER.pack(
            RESPONSE_MAGIC,
            PROTOCOL_VERSION,
            RESPONSE_HEADER_SIZE,
            int(status),
            job_bytes,
            len(body),
            zlib.crc32(body) & 0xFFFFFFFF,
        )
        return header + body

    def _handle_connection(self, connection: socket.socket) -> None:
        connection.settimeout(max(0.5, float(settings.ia2_transport_timeout_ms) / 1000.0))
        try:
            while not self._stop.is_set():
                try:
                    header_data = _recv_exact(connection, REQUEST_HEADER_SIZE)
                except IA2TransportUnavailable:
                    return
                (
                    magic,
                    version,
                    header_size,
                    camera_id,
                    job_bytes,
                    frame_id,
                    generation_id,
                    track_id,
                    deadline_ns,
                    priority,
                    height,
                    width,
                    channels,
                    quality_size,
                    payload_size,
                    payload_crc,
                ) = REQUEST_HEADER.unpack(header_data)

                if (
                    magic != REQUEST_MAGIC
                    or version != PROTOCOL_VERSION
                    or header_size != REQUEST_HEADER_SIZE
                    or camera_id <= 0
                    or not 1 <= width <= MAX_CROP_DIM
                    or not 1 <= height <= MAX_CROP_DIM
                    or channels not in (1, 3)
                    or quality_size > MAX_QUALITY_BYTES
                    or not 0 < payload_size <= MAX_PAYLOAD_BYTES
                    or payload_size != width * height * channels
                ):
                    connection.sendall(
                        self._response(job_bytes, STATUS_INVALID, {"error": "cabecalho da IA2 invalido"})
                    )
                    return

                quality_raw = _recv_exact(connection, quality_size) if quality_size else b"{}"
                payload = _recv_exact(connection, payload_size)
                if zlib.crc32(payload) & 0xFFFFFFFF != payload_crc:
                    connection.sendall(
                        self._response(job_bytes, STATUS_INVALID, {"error": "checksum do recorte invalido"})
                    )
                    continue

                try:
                    quality = json.loads(quality_raw.decode("utf-8"))
                except Exception:
                    quality = {}
                crop = np.frombuffer(payload, dtype=np.uint8).reshape((height, width, channels))

                pool = self._resolve_pool()
                try:
                    result = pool.submit(
                        crop,
                        quality=quality,
                        priority=int(priority),
                        deadline_monotonic_ns=int(deadline_ns),
                        payload_bytes=len(payload),
                    )
                except Exception as exc:
                    status = self._status_for(exc)
                    connection.sendall(
                        self._response(job_bytes, status, {"error": exc.__class__.__name__})
                    )
                    continue

                body = self._serialize_result(
                    result,
                    camera_id=camera_id,
                    frame_id=frame_id,
                    generation_id=generation_id,
                    track_id=track_id,
                    pool=pool,
                )
                connection.sendall(self._response(job_bytes, STATUS_OK, body))
        except Exception:
            logger.exception(
                "Falha na conexao do socket da IA2",
                extra={
                    "action": "ia2_socket_connection_failed",
                    "status": "degraded",
                    "reason": "connection_error",
                },
            )
        finally:
            try:
                connection.close()
            except Exception:
                pass

    @staticmethod
    def _status_for(exc: BaseException) -> int:
        from app.runtime.ia2_pool import (
            IA2PoolQueueFull,
            IA2PoolTimeout,
            IA2PoolUnavailable,
        )

        if isinstance(exc, IA2PoolQueueFull):
            return STATUS_QUEUE_FULL
        if isinstance(exc, IA2PoolTimeout):
            return STATUS_TIMEOUT
        if isinstance(exc, IA2PoolUnavailable):
            return STATUS_UNAVAILABLE
        return STATUS_ERROR

    @staticmethod
    def _serialize_result(result: Any, *, camera_id: int, frame_id: int, generation_id: int, track_id: int, pool: Any) -> dict[str, Any]:
        """A resposta repete a identidade recebida, para o worker validar."""
        return {
            "camera_id": int(camera_id),
            "frame_id": None if frame_id < 0 else int(frame_id),
            "generation_id": None if generation_id < 0 else int(generation_id),
            "track_id": None if track_id < 0 else int(track_id),
            "pool_generation_id": int(getattr(pool, "generation_id", 0) or 0),
            "queue_wait_ms": round(float(getattr(pool.stats, "last_queue_wait_ms", 0.0)), 3),
            "enabled": bool(getattr(result, "enabled", True)),
            "applied": bool(getattr(result, "applied", False)),
            "person_score": getattr(result, "person_score", None),
            "not_person_score": getattr(result, "not_person_score", None),
            "passed": getattr(result, "passed", None),
            "threshold": getattr(result, "threshold", None),
            "mode": getattr(result, "mode", "audit"),
            "inference_ms": float(getattr(result, "inference_ms", 0.0) or 0.0),
            "model_path": getattr(result, "model_path", None),
            "reason": getattr(result, "reason", None),
            "block_eligible": bool(getattr(result, "block_eligible", False)),
            "block_reason": getattr(result, "block_reason", None),
            "quality": getattr(result, "quality", None) or {},
            "device": getattr(result, "device", None),
        }


ia2_socket_server = IA2SocketServer()


__all__ = [
    "IA2BinaryTransport",
    "IA2SocketServer",
    "IA2TransportError",
    "IA2TransportQueueFull",
    "IA2TransportTimeout",
    "IA2TransportUnavailable",
    "PROTOCOL_VERSION",
    "REQUEST_HEADER",
    "REQUEST_HEADER_SIZE",
    "REQUEST_MAGIC",
    "RESPONSE_HEADER",
    "RESPONSE_HEADER_SIZE",
    "RESPONSE_MAGIC",
    "STATUS_OK",
    "ia2_socket_server",
    "ia2_transport_mode",
]
