"""Armazena o ultimo frame bruto e processado de cada camera.

O worker publica JPEGs e metadados aqui para que a UI e os outros servicos
consigam ler o estado mais recente sem manter filas nem dependencias externas.
"""

from __future__ import annotations

import os
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from multiprocessing import shared_memory

import cv2
import numpy as np

from app.core.config import settings
from app.core.logging import log_ignored_exception
from app.core.timezone import utc_now_naive


class _SharedFrameSlot:
    META_STRUCT = struct.Struct("<Q I I I Q")
    META_SIZE = 64

    def __init__(self, kind: str, camera_id: int, buffer_size: int):
        self.kind = kind
        self.camera_id = camera_id
        self.buffer_size = buffer_size
        self._data_name = f"analitico_frame_{kind}_{camera_id}_data"
        self._meta_name = f"analitico_frame_{kind}_{camera_id}_meta"
        self._data_shm = None
        self._meta_shm = None
        self._lock = Lock()

    def _open_or_create(self, name: str, size: int):
        try:
            return shared_memory.SharedMemory(name=name, create=True, size=size)
        except FileExistsError:
            return shared_memory.SharedMemory(name=name, create=False)

    def _ensure_open(self):
        if self._data_shm is not None and self._meta_shm is not None:
            return

        self._data_shm = self._open_or_create(self._data_name, self.buffer_size)
        self._meta_shm = self._open_or_create(self._meta_name, self.META_SIZE)

    def write_jpeg(self, jpeg_bytes: bytes, width: int, height: int) -> bool:
        # Escrita em duas etapas evita leitura de frame parcialmente atualizado.
        if not jpeg_bytes:
            return False

        if len(jpeg_bytes) > self.buffer_size:
            return False

        with self._lock:
            self._ensure_open()

            meta_buf = self._meta_shm.buf
            data_buf = self._data_shm.buf

            try:
                current_version = self.META_STRUCT.unpack_from(meta_buf, 0)[0]
            except Exception:
                current_version = 0

            start_version = current_version + 1
            if start_version % 2 == 0:
                start_version += 1
            final_version = start_version + 1
            updated_ns = time.time_ns()

            self.META_STRUCT.pack_into(
                meta_buf,
                0,
                start_version,
                0,
                int(width),
                int(height),
                int(updated_ns),
            )

            data_buf[: len(jpeg_bytes)] = jpeg_bytes

            self.META_STRUCT.pack_into(
                meta_buf,
                0,
                final_version,
                int(len(jpeg_bytes)),
                int(width),
                int(height),
                int(updated_ns),
            )

        return True

    def read_jpeg(self) -> bytes | None:
        # Leitura com double-check de versao para reduzir race conditions.
        try:
            self._ensure_open()
        except FileNotFoundError:
            return None

        meta_buf = self._meta_shm.buf
        data_buf = self._data_shm.buf

        for _ in range(3):
            try:
                version1, length, _width, _height, _updated_ns = self.META_STRUCT.unpack_from(meta_buf, 0)
            except Exception:
                return None

            if version1 == 0 or version1 % 2 == 1 or length <= 0 or length > self.buffer_size:
                return None

            payload = bytes(data_buf[:length])

            try:
                version2, _, _, _, _ = self.META_STRUCT.unpack_from(meta_buf, 0)
            except Exception:
                return None

            if version1 == version2 and version2 % 2 == 0:
                return payload

        return None

    def close(self):
        for shm in (self._data_shm, self._meta_shm):
            if shm is None:
                continue
            try:
                shm.close()
            except Exception:
                pass
        self._data_shm = None
        self._meta_shm = None

    def unlink(self):
        for shm in (self._data_shm, self._meta_shm):
            if shm is None:
                continue
            try:
                shm.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass


class FrameStore:
    DEFAULT_JPEG_QUALITY = 85

    def __init__(self):
        self._lock = Lock()
        self._slots: dict[tuple[str, int], _SharedFrameSlot] = {}

        self._prefer_shm = bool(settings.frame_store_prefer_shm)
        self.SHM_BUFFER_SIZE = max(1, int(settings.frame_store_shm_buffer_size_mb)) * 1024 * 1024

        self._base_dir = Path(settings.runtime_state_dir) / "frames"
        self._raw_dir = self._base_dir / "raw"
        self._processed_dir = self._base_dir / "processed"

        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._processed_dir.mkdir(parents=True, exist_ok=True)

    def _dir_for_kind(self, kind: str) -> Path:
        return self._raw_dir if kind == "raw" else self._processed_dir

    def _frame_final_path(self, kind: str, camera_id: int, stamp_ns: int) -> Path:
        return self._dir_for_kind(kind) / f"camera_{camera_id}_{stamp_ns}.jpg"

    def _frame_temp_path(self, kind: str, camera_id: int, stamp_ns: int) -> Path:
        return self._dir_for_kind(kind) / f"camera_{camera_id}_{stamp_ns}.tmp.jpg"

    def _legacy_jpg_path(self, kind: str, camera_id: int) -> Path:
        return self._dir_for_kind(kind) / f"camera_{camera_id}.jpg"

    def _slot(self, kind: str, camera_id: int) -> _SharedFrameSlot:
        key = (kind, int(camera_id))
        with self._lock:
            slot = self._slots.get(key)
            if slot is None:
                slot = _SharedFrameSlot(
                    kind=kind,
                    camera_id=int(camera_id),
                    buffer_size=self.SHM_BUFFER_SIZE,
                )
                self._slots[key] = slot
            return slot

    def _encode_jpeg(self, frame, quality: int | None = None):
        if frame is None:
            return None, 0.0

        jpeg_quality = int(quality or self.DEFAULT_JPEG_QUALITY)
        started = time.perf_counter()
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if not ok:
            return None, elapsed_ms
        return encoded.tobytes(), elapsed_ms

    def _write_disk_latest_frame(self, kind: str, camera_id: int, jpeg_bytes: bytes) -> bool:
        if not jpeg_bytes:
            return False

        stamp_ns = time.time_ns()
        temp_path = self._frame_temp_path(kind, camera_id, stamp_ns)
        final_path = self._frame_final_path(kind, camera_id, stamp_ns)

        try:
            temp_path.write_bytes(jpeg_bytes)
            os.replace(temp_path, final_path)
            self._cleanup_old_versions(kind, camera_id, keep=3)
            return True
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass

    def _list_candidate_frames(self, kind: str, camera_id: int) -> list[Path]:
        folder = self._dir_for_kind(kind)
        candidates = []

        try:
            for path in folder.glob(f"camera_{camera_id}_*.jpg"):
                if path.name.endswith(".tmp.jpg"):
                    continue
                candidates.append(path)
        except Exception:
            pass

        legacy_path = self._legacy_jpg_path(kind, camera_id)
        if legacy_path.exists():
            candidates.append(legacy_path)

        candidates.sort(
            key=lambda p: p.stat().st_mtime_ns if p.exists() else 0,
            reverse=True,
        )
        return candidates

    def _read_latest_disk_jpeg(self, kind: str, camera_id: int) -> bytes | None:
        candidates = self._list_candidate_frames(kind, camera_id)

        for frame_path in candidates[:5]:
            try:
                return frame_path.read_bytes()
            except Exception:
                continue

        return None

    def _cleanup_old_versions(self, kind: str, camera_id: int, keep: int = 3) -> None:
        folder = self._dir_for_kind(kind)

        try:
            files = []
            for path in folder.glob(f"camera_{camera_id}_*.jpg"):
                if path.name.endswith(".tmp.jpg"):
                    continue
                files.append(path)

            files.sort(
                key=lambda p: p.stat().st_mtime_ns if p.exists() else 0,
                reverse=True,
            )

            for old_path in files[keep:]:
                try:
                    old_path.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    def _ns_to_utc_datetime(self, updated_ns: int | None):
        if not updated_ns:
            return None
        try:
            return datetime.fromtimestamp(int(updated_ns) / 1_000_000_000.0, tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            return None

    def _read_slot_metadata(self, kind: str, camera_id: int) -> dict | None:
        if not self._prefer_shm:
            return None

        try:
            slot = self._slot(kind, camera_id)
            slot._ensure_open()
            meta_buf = slot._meta_shm.buf
            version, length, width, height, updated_ns = slot.META_STRUCT.unpack_from(meta_buf, 0)
            if version <= 0:
                return None
            return {
                "source": "shared_memory",
                "updated_ns": int(updated_ns) if updated_ns else None,
                "updated_at": self._ns_to_utc_datetime(updated_ns),
                "width": int(width),
                "height": int(height),
                "length": int(length),
            }
        except FileNotFoundError:
            return None
        except Exception:
            return None

    def _read_disk_metadata(self, kind: str, camera_id: int) -> dict | None:
        candidates = self._list_candidate_frames(kind, camera_id)
        for frame_path in candidates[:5]:
            try:
                stat = frame_path.stat()
            except Exception:
                continue
            return {
                "source": "disk",
                "updated_ns": int(stat.st_mtime_ns),
                "updated_at": self._ns_to_utc_datetime(stat.st_mtime_ns),
                "width": None,
                "height": None,
                "length": int(stat.st_size),
            }
        return None

    def _store_frame(self, kind: str, camera_id: int, frame):
        if frame is None:
            return {"ok": False, "source": None, "encode_ms": 0.0, "jpeg_size": 0}

        jpeg_bytes, encode_ms = self._encode_jpeg(frame)
        if not jpeg_bytes:
            return {"ok": False, "source": None, "encode_ms": encode_ms, "jpeg_size": 0}

        height, width = frame.shape[:2]

        if self._prefer_shm:
            try:
                slot = self._slot(kind, camera_id)
                if slot.write_jpeg(jpeg_bytes, width=width, height=height):
                    return {
                        "ok": True,
                        "source": "shared_memory",
                        "encode_ms": encode_ms,
                        "jpeg_size": len(jpeg_bytes),
                        "updated_ns": time.time_ns(),
                        "updated_at": utc_now_naive(),
                    }
            except Exception:
                # Cai para disco: o frame ainda chega, mas a degradacao precisa de rastro.
                log_ignored_exception(
                    "frame_store.write_shm_fallback_disk", camera_id=camera_id, reason=kind
                )

        disk_ok = self._write_disk_latest_frame(kind, camera_id, jpeg_bytes)
        return {
            "ok": bool(disk_ok),
            "source": "disk" if disk_ok else None,
            "encode_ms": encode_ms,
            "jpeg_size": len(jpeg_bytes),
            "updated_ns": time.time_ns() if disk_ok else None,
            "updated_at": utc_now_naive() if disk_ok else None,
        }

    def _get_jpeg(self, kind: str, camera_id: int) -> bytes | None:
        if self._prefer_shm:
            try:
                slot = self._slot(kind, camera_id)
                jpeg_bytes = slot.read_jpeg()
                if jpeg_bytes:
                    return jpeg_bytes
            except Exception:
                log_ignored_exception(
                    "frame_store.read_shm_fallback_disk", camera_id=camera_id, reason=kind
                )

        return self._read_latest_disk_jpeg(kind, camera_id)

    def _get_frame(self, kind: str, camera_id: int):
        jpeg_bytes = self._get_jpeg(kind, camera_id)
        if not jpeg_bytes:
            return None

        try:
            data = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            if data.size == 0:
                return None
            return cv2.imdecode(data, cv2.IMREAD_COLOR)
        except Exception:
            return None

    def set_processed_frame(self, camera_id: int, frame):
        return self._store_frame("processed", camera_id, frame)

    def get_processed_frame(self, camera_id: int):
        return self._get_frame("processed", camera_id)

    def get_processed_jpeg(self, camera_id: int) -> bytes | None:
        return self._get_jpeg("processed", camera_id)

    def get_processed_frame_metadata(self, camera_id: int) -> dict | None:
        return self._read_slot_metadata("processed", camera_id) or self._read_disk_metadata("processed", camera_id)

    def set_raw_frame(self, camera_id: int, frame):
        return self._store_frame("raw", camera_id, frame)

    def get_raw_frame(self, camera_id: int):
        return self._get_frame("raw", camera_id)

    def get_raw_jpeg(self, camera_id: int) -> bytes | None:
        return self._get_jpeg("raw", camera_id)

    def get_raw_frame_metadata(self, camera_id: int) -> dict | None:
        return self._read_slot_metadata("raw", camera_id) or self._read_disk_metadata("raw", camera_id)

    def remove_frame(self, camera_id: int):
        with self._lock:
            for kind in ("raw", "processed"):
                key = (kind, int(camera_id))
                slot = self._slots.pop(key, None)
                if slot is not None:
                    try:
                        slot.unlink()
                    except Exception:
                        pass
                    try:
                        slot.close()
                    except Exception:
                        pass

                folder = self._dir_for_kind(kind)

                try:
                    legacy_path = self._legacy_jpg_path(kind, camera_id)
                    if legacy_path.exists():
                        legacy_path.unlink()
                except Exception:
                    pass

                try:
                    for path in folder.glob(f"camera_{camera_id}_*.jpg"):
                        try:
                            path.unlink()
                        except Exception:
                            pass
                except Exception:
                    pass


frame_store = FrameStore()
