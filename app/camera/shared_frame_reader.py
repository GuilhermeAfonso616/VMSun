"""Leitor do ring buffer JPEG versionado publicado pelo Camera Gateway."""

from __future__ import annotations

import mmap
import os
import stat
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings


MAGIC = b"SUNFRM01"
PROTOCOL_VERSION = 1
FILE_HEADER_SIZE = 128
SLOT_HEADER_SIZE = 128
PAYLOAD_FORMAT_JPEG = 1
FLAG_READY = 1
MAX_SLOT_COUNT = 16
MAX_SLOT_CAPACITY = 32 * 1024 * 1024


class SharedFrameError(RuntimeError):
    code = "shared_frame_error"


class SharedFrameUnavailable(SharedFrameError):
    code = "shared_frame_unavailable"


class SharedFrameProtocolError(SharedFrameError):
    code = "shared_frame_protocol_error"


class SharedFrameCorrupt(SharedFrameError):
    code = "shared_frame_corrupt"


@dataclass(slots=True, frozen=True)
class FramePacket:
    camera_id: int
    frame_id: int
    generation_id: int
    captured_at_monotonic_ns: int
    published_at_monotonic_ns: int
    captured_at_wall_ns: int
    width: int
    height: int
    channels: int
    pixel_format: int
    payload_format: int
    payload: bytes
    frame_age_ms: float


def frame_buffer_path(camera_id: int, *, root: str | None = None, protocol_version: int | None = None) -> Path:
    normalized_id = int(camera_id)
    version = int(protocol_version or settings.frame_transport_protocol_version)
    if normalized_id <= 0:
        raise ValueError("camera_id invalido")
    if version <= 0:
        raise ValueError("versao de protocolo invalida")
    base = Path(root or settings.frame_transport_root).resolve()
    path = base / f"camera_{normalized_id}_v{version}.mmap"
    if path.parent != base:
        raise ValueError("caminho de frame buffer invalido")
    return path


class SharedFrameReader:
    def __init__(
        self,
        camera_id: int,
        *,
        root: str | None = None,
        protocol_version: int | None = None,
        poll_interval_ms: int | None = None,
    ):
        self.camera_id = int(camera_id)
        self.protocol_version = int(protocol_version or settings.frame_transport_protocol_version)
        self.root = Path(root or settings.frame_transport_root).resolve()
        self.path = frame_buffer_path(
            self.camera_id, root=str(self.root), protocol_version=self.protocol_version
        )
        self.poll_interval_seconds = max(
            0.001,
            float(poll_interval_ms or settings.frame_transport_poll_interval_ms) / 1000.0,
        )
        self._file = None
        self._mapping: mmap.mmap | None = None
        self._identity: tuple[int, int, int] | None = None
        self._slot_count = 0
        self._slot_capacity = 0
        self._generation = 0
        self._last_frame_id = 0
        self.frames_read_total = 0
        self.frames_skipped_total = 0
        self.corrupt_frames_total = 0
        self.generation_changes_total = 0
        self.last_read_latency_ms = 0.0
        self.last_wait_ms = 0.0
        self.last_frame_age_ms = 0.0

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def last_frame_id(self) -> int:
        return self._last_frame_id

    def _validate_file(self) -> os.stat_result:
        try:
            info = os.lstat(self.path)
        except FileNotFoundError as exc:
            raise SharedFrameUnavailable("buffer compartilhado ausente") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SharedFrameProtocolError("recurso de frame inesperado")
        if self.path.resolve().parent != self.root:
            raise SharedFrameProtocolError("frame buffer fora do diretorio permitido")
        return info

    def _open_mapping(self) -> None:
        info = self._validate_file()
        identity = (int(info.st_dev), int(info.st_ino), int(info.st_size))
        if self._mapping is not None and identity == self._identity:
            return
        self.close()
        file_obj = self.path.open("rb", buffering=0)
        try:
            mapping = mmap.mmap(file_obj.fileno(), 0, access=mmap.ACCESS_READ)
        except Exception:
            file_obj.close()
            raise
        self._file = file_obj
        self._mapping = mapping
        self._identity = identity
        try:
            self._read_file_header()
        except Exception:
            self.close()
            raise

    def _read_file_header(self) -> None:
        data = self._mapping
        if data is None or len(data) < FILE_HEADER_SIZE:
            raise SharedFrameProtocolError("cabecalho do frame buffer incompleto")
        if data[0:8] != MAGIC:
            raise SharedFrameProtocolError("magic do frame buffer invalido")
        version, header_size = struct.unpack_from("<HH", data, 8)
        camera_id, slot_count, slot_capacity = struct.unpack_from("<III", data, 12)
        generation = struct.unpack_from("<Q", data, 24)[0]
        if version != self.protocol_version or version != PROTOCOL_VERSION:
            raise SharedFrameProtocolError(
                f"versao incompativel do frame buffer: {version}"
            )
        if header_size != FILE_HEADER_SIZE:
            raise SharedFrameProtocolError("tamanho de cabecalho incompativel")
        if camera_id != self.camera_id:
            raise SharedFrameProtocolError("camera_id divergente no frame buffer")
        if not 2 <= slot_count <= MAX_SLOT_COUNT:
            raise SharedFrameProtocolError("quantidade de slots invalida")
        if not 64 * 1024 <= slot_capacity <= MAX_SLOT_CAPACITY:
            raise SharedFrameProtocolError("capacidade de slot invalida")
        expected_size = FILE_HEADER_SIZE + slot_count * (SLOT_HEADER_SIZE + slot_capacity)
        if len(data) != expected_size:
            raise SharedFrameProtocolError("tamanho do frame buffer divergente")
        if self._generation and generation != self._generation:
            self.generation_changes_total += 1
            self._last_frame_id = 0
        self._generation = generation
        self._slot_count = slot_count
        self._slot_capacity = slot_capacity

    def _frame_age_ms(self, published_monotonic_ns: int, captured_wall_ns: int) -> float:
        now_monotonic = time.monotonic_ns()
        if 0 < published_monotonic_ns <= now_monotonic:
            return max(0.0, (now_monotonic - published_monotonic_ns) / 1_000_000.0)
        if captured_wall_ns > 0:
            return max(0.0, (time.time_ns() - captured_wall_ns) / 1_000_000.0)
        return 0.0

    def _read_current(self) -> FramePacket | None:
        self._open_mapping()
        data = self._mapping
        assert data is not None
        generation, latest_frame_id = struct.unpack_from("<QQ", data, 24)
        latest_slot, active = struct.unpack_from("<II", data, 40)
        if generation != self._generation:
            self.generation_changes_total += 1
            self._generation = generation
            self._last_frame_id = 0
        if active != 1:
            raise SharedFrameUnavailable("stream marcado como inativo no buffer")
        if latest_frame_id <= 0 or latest_slot >= self._slot_count:
            return None
        if latest_frame_id == self._last_frame_id:
            return None

        slot_base = FILE_HEADER_SIZE + latest_slot * (
            SLOT_HEADER_SIZE + self._slot_capacity
        )
        for _attempt in range(3):
            sequence_begin = struct.unpack_from("<Q", data, slot_base)[0]
            if sequence_begin == 0 or sequence_begin & 1:
                continue
            sequence_end = struct.unpack_from("<Q", data, slot_base + 8)[0]
            (
                slot_generation,
                frame_id,
                captured_monotonic_ns,
                published_monotonic_ns,
                captured_wall_ns,
            ) = struct.unpack_from("<QQQQQ", data, slot_base + 16)
            width, height = struct.unpack_from("<II", data, slot_base + 56)
            channels, pixel_format, payload_format, flags = struct.unpack_from(
                "<HHHH", data, slot_base + 64
            )
            payload_size, payload_capacity, checksum, camera_id, slot_index = (
                struct.unpack_from("<IIIII", data, slot_base + 72)
            )
            if sequence_begin != sequence_end or sequence_begin & 1:
                continue
            if (
                slot_generation != generation
                or frame_id != latest_frame_id
                or camera_id != self.camera_id
                or slot_index != latest_slot
            ):
                continue
            if flags & FLAG_READY == 0 or payload_format != PAYLOAD_FORMAT_JPEG:
                raise SharedFrameProtocolError("formato de payload nao suportado")
            if (
                payload_capacity != self._slot_capacity
                or payload_size <= 0
                or payload_size > payload_capacity
            ):
                raise SharedFrameProtocolError("tamanho de payload invalido")
            if width <= 0 or height <= 0 or width > 16384 or height > 16384:
                raise SharedFrameProtocolError("dimensoes do frame invalidas")
            payload_base = slot_base + SLOT_HEADER_SIZE
            payload = bytes(data[payload_base : payload_base + payload_size])
            sequence_after = struct.unpack_from("<Q", data, slot_base)[0]
            if sequence_after != sequence_begin:
                continue
            if zlib.crc32(payload) & 0xFFFFFFFF != checksum:
                raise SharedFrameCorrupt("checksum do frame divergente")
            if self._last_frame_id and frame_id > self._last_frame_id + 1:
                self.frames_skipped_total += frame_id - self._last_frame_id - 1
            age_ms = self._frame_age_ms(published_monotonic_ns, captured_wall_ns)
            self._last_frame_id = frame_id
            self.frames_read_total += 1
            self.last_frame_age_ms = age_ms
            return FramePacket(
                camera_id=camera_id,
                frame_id=frame_id,
                generation_id=slot_generation,
                captured_at_monotonic_ns=captured_monotonic_ns,
                published_at_monotonic_ns=published_monotonic_ns,
                captured_at_wall_ns=captured_wall_ns,
                width=width,
                height=height,
                channels=channels,
                pixel_format=pixel_format,
                payload_format=payload_format,
                payload=payload,
                frame_age_ms=age_ms,
            )
        raise SharedFrameCorrupt("slot sobrescrito ou parcialmente escrito")

    def read_latest(self, timeout: float | None = None) -> FramePacket | None:
        started = time.perf_counter()
        deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
        while True:
            read_started = time.perf_counter()
            try:
                packet = self._read_current()
            except SharedFrameCorrupt:
                self.corrupt_frames_total += 1
                raise
            if packet is not None:
                self.last_read_latency_ms = (
                    time.perf_counter() - read_started
                ) * 1000.0
                self.last_wait_ms = (time.perf_counter() - started) * 1000.0
                return packet
            if time.monotonic() >= deadline:
                self.last_wait_ms = (time.perf_counter() - started) * 1000.0
                return None
            time.sleep(self.poll_interval_seconds)

    def metrics(self) -> dict[str, int | float | bool]:
        return {
            "shared_buffer_frames_read_total": self.frames_read_total,
            "shared_buffer_frames_skipped_total": self.frames_skipped_total,
            "shared_buffer_corrupt_frames_total": self.corrupt_frames_total,
            "shared_buffer_generation_changes_total": self.generation_changes_total,
            "shared_buffer_read_latency_ms": round(self.last_read_latency_ms, 3),
            "shared_buffer_wait_ms": round(self.last_wait_ms, 3),
            "shared_buffer_frame_age_ms": round(self.last_frame_age_ms, 3),
            "shared_buffer_generation": self._generation,
            "shared_buffer_last_frame_id": self._last_frame_id,
        }

    def close(self) -> None:
        if self._mapping is not None:
            self._mapping.close()
            self._mapping = None
        if self._file is not None:
            self._file.close()
            self._file = None
        self._identity = None
