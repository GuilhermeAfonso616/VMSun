from __future__ import annotations

import mmap
import os
import struct
import time
import zlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.camera import frame_transport
from app.camera.frame_transport import (
    FrameTransportStrictError,
    SharedMemoryGatewayCapture,
    frame_transport_selected,
)
from app.camera.shared_frame_reader import (
    FILE_HEADER_SIZE,
    FLAG_READY,
    MAGIC,
    PAYLOAD_FORMAT_JPEG,
    SLOT_HEADER_SIZE,
    SharedFrameCorrupt,
    SharedFrameProtocolError,
    SharedFrameReader,
    SharedFrameUnavailable,
)


def _jpeg(width=64, height=48, value=80):
    frame = np.full((height, width, 3), value, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", frame)
    assert ok
    return encoded.tobytes()


def _write_buffer(
    root: Path,
    *,
    camera_id=36,
    generation=11,
    frame_id=1,
    slot_count=2,
    capacity=64 * 1024,
    payload=None,
    version=1,
    active=1,
    partial=False,
    checksum=None,
):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"camera_{camera_id}_v1.mmap"
    total = FILE_HEADER_SIZE + slot_count * (SLOT_HEADER_SIZE + capacity)
    data = bytearray(total)
    data[0:8] = MAGIC
    struct.pack_into("<HHIII", data, 8, version, FILE_HEADER_SIZE, camera_id, slot_count, capacity)
    struct.pack_into("<QQII", data, 24, generation, frame_id, 0, active)
    now_mono = time.monotonic_ns()
    struct.pack_into("<Q", data, 56, now_mono)
    if payload is None:
        payload = _jpeg()
    slot_base = FILE_HEADER_SIZE
    sequence = frame_id * 2
    struct.pack_into("<QQ", data, slot_base, sequence - 1 if partial else sequence, sequence)
    struct.pack_into(
        "<QQQQQ",
        data,
        slot_base + 16,
        generation,
        frame_id,
        now_mono,
        now_mono,
        time.time_ns(),
    )
    decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    height, width = decoded.shape[:2] if decoded is not None else (48, 64)
    struct.pack_into("<II", data, slot_base + 56, width, height)
    struct.pack_into("<HHHH", data, slot_base + 64, 3, 1, PAYLOAD_FORMAT_JPEG, FLAG_READY)
    struct.pack_into(
        "<IIIII",
        data,
        slot_base + 72,
        len(payload),
        capacity,
        zlib.crc32(payload) & 0xFFFFFFFF if checksum is None else checksum,
        camera_id,
        0,
    )
    data[slot_base + SLOT_HEADER_SIZE : slot_base + SLOT_HEADER_SIZE + len(payload)] = payload
    path.write_bytes(data)
    return path


def test_transport_selection(monkeypatch):
    monkeypatch.setattr(frame_transport.settings, "frame_transport_mode", "shared_memory_prefer")
    monkeypatch.setattr(frame_transport.settings, "frame_transport_camera_ids", "36,37")
    assert frame_transport_selected(36)
    assert not frame_transport_selected(35)
    monkeypatch.setattr(frame_transport.settings, "frame_transport_camera_ids", "*")
    assert frame_transport_selected(999)
    monkeypatch.setattr(frame_transport.settings, "frame_transport_mode", "http")
    assert not frame_transport_selected(36)


def test_reader_reads_latest_valid_jpeg(tmp_path):
    payload = _jpeg(value=120)
    _write_buffer(tmp_path, payload=payload, frame_id=7)
    reader = SharedFrameReader(36, root=str(tmp_path), poll_interval_ms=1)
    packet = reader.read_latest()
    assert packet is not None
    assert packet.camera_id == 36
    assert packet.frame_id == 7
    assert packet.generation_id == 11
    assert packet.payload == payload
    assert packet.width == 64
    assert packet.height == 48
    assert packet.frame_age_ms >= 0
    assert reader.read_latest() is None
    assert reader.metrics()["shared_buffer_frames_read_total"] == 1
    reader.close()


def test_reader_rejects_missing_partial_and_corrupt_buffer(tmp_path):
    with pytest.raises(SharedFrameUnavailable):
        SharedFrameReader(36, root=str(tmp_path)).read_latest()

    _write_buffer(tmp_path, partial=True)
    reader = SharedFrameReader(36, root=str(tmp_path))
    with pytest.raises(SharedFrameCorrupt):
        reader.read_latest()
    assert reader.corrupt_frames_total == 1
    reader.close()

    _write_buffer(tmp_path, checksum=123)
    reader = SharedFrameReader(36, root=str(tmp_path))
    with pytest.raises(SharedFrameCorrupt):
        reader.read_latest()
    reader.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", 2),
        ("camera_id", 37),
    ],
)
def test_reader_rejects_protocol_or_camera_mismatch(tmp_path, field, value):
    kwargs = {field: value}
    path = _write_buffer(tmp_path, **kwargs)
    if field == "camera_id":
        target = tmp_path / "camera_36_v1.mmap"
        path.replace(target)
    reader = SharedFrameReader(36, root=str(tmp_path))
    with pytest.raises(SharedFrameProtocolError):
        reader.read_latest()


def test_reader_detects_generation_change_and_skipped_frames(tmp_path):
    path = _write_buffer(tmp_path, generation=10, frame_id=1)
    reader = SharedFrameReader(36, root=str(tmp_path), poll_interval_ms=1)
    assert reader.read_latest().frame_id == 1
    replacement = _write_buffer(tmp_path / "new", generation=20, frame_id=5)
    if os.name == "nt":
        reader.close()
    replacement.replace(path)
    packet = reader.read_latest(timeout=0.05)
    assert packet is not None and packet.generation_id == 20
    assert reader.generation_changes_total == 1
    # A generation new clears lag; frames from the previous generation are not
    # counted as skipped.
    assert reader.frames_skipped_total == 0
    reader.close()


def test_reader_isolates_camera_files(tmp_path):
    _write_buffer(tmp_path, camera_id=36, frame_id=2, payload=_jpeg(value=20))
    _write_buffer(tmp_path, camera_id=37, frame_id=9, payload=_jpeg(value=220))
    first = SharedFrameReader(36, root=str(tmp_path)).read_latest()
    second = SharedFrameReader(37, root=str(tmp_path)).read_latest()
    assert first.camera_id == 36 and first.frame_id == 2
    assert second.camera_id == 37 and second.frame_id == 9
    assert first.payload != second.payload


def test_reader_rejects_symlink(tmp_path):
    real_root = tmp_path / "real"
    real = _write_buffer(real_root)
    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    link = shared_root / "camera_36_v1.mmap"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink indisponivel neste ambiente")
    with pytest.raises(SharedFrameProtocolError):
        SharedFrameReader(36, root=str(shared_root)).read_latest()


def test_shared_capture_strict_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(frame_transport.settings, "frame_transport_root", str(tmp_path))
    monkeypatch.setattr(frame_transport.settings, "frame_transport_mode", "shared_memory_strict")
    monkeypatch.setattr(frame_transport.settings, "camera_gateway_first_frame_timeout_seconds", 0.05)
    monkeypatch.setattr(frame_transport, "register_camera_source", lambda *args, **kwargs: {"ok": True})
    capture = SharedMemoryGatewayCapture(36, "rtsp://origin")
    with pytest.raises(FrameTransportStrictError):
        capture.open()
    assert capture.http_fallback_total == 0


def test_shared_capture_prefer_has_explicit_http_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(frame_transport.settings, "frame_transport_root", str(tmp_path))
    monkeypatch.setattr(frame_transport.settings, "frame_transport_mode", "shared_memory_prefer")
    monkeypatch.setattr(frame_transport.settings, "frame_transport_fallback_enabled", True)
    monkeypatch.setattr(frame_transport.settings, "camera_gateway_first_frame_timeout_seconds", 0.05)
    monkeypatch.setattr(frame_transport, "register_camera_source", lambda *args, **kwargs: {"ok": True})

    def fake_http_open(self):
        self.cap = object()
        self._opened = True

    monkeypatch.setattr(frame_transport.GatewayFramesCapture, "open", fake_http_open)
    capture = SharedMemoryGatewayCapture(36, "rtsp://origin")
    capture.open()
    assert capture.transport_mode_active == "http_fallback"
    assert capture.http_fallback_total == 1
    assert capture.transport_metrics()["frame_transport_http_fallback_total"] == 1


def test_shared_capture_decodes_without_http(monkeypatch, tmp_path):
    _write_buffer(tmp_path, frame_id=3)
    monkeypatch.setattr(frame_transport.settings, "frame_transport_root", str(tmp_path))
    monkeypatch.setattr(frame_transport.settings, "frame_transport_mode", "shared_memory_strict")
    monkeypatch.setattr(frame_transport, "register_camera_source", lambda *args, **kwargs: {"ok": True})
    capture = SharedMemoryGatewayCapture(36, "rtsp://origin")
    capture.open()
    ok, frame = capture.read_latest()
    # open consumed frame 3; publish frame 4 by replacing the file.
    if not ok:
        replacement = _write_buffer(tmp_path / "new", generation=12, frame_id=4)
        if os.name == "nt":
            capture.reader.close()
        replacement.replace(tmp_path / "camera_36_v1.mmap")
        ok, frame = capture.read_latest()
    assert ok is True
    assert frame.shape[:2] == (48, 64)
    assert capture.http_fallback_total == 0
