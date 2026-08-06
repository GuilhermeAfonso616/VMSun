from __future__ import annotations

import json
import zlib

import pytest

from app.core.config import settings
from app.runtime.inference_transport import (
    BinaryLocalInferenceTransport,
    InferenceTransportError,
    REQUEST_HEADER,
    REQUEST_HEADER_SIZE,
    REQUEST_MAGIC,
    RESPONSE_HEADER,
    RESPONSE_HEADER_SIZE,
    RESPONSE_MAGIC,
    STATUS_OK,
    inference_transport_selected,
)


class FakeSocket:
    def __init__(self, response: bytes):
        self.sent = bytearray()
        self.response = bytearray(response)
        self.closed = False

    def sendall(self, data: bytes):
        self.sent.extend(data)

    def recv(self, size: int) -> bytes:
        if not self.response:
            return b""
        chunk = bytes(self.response[:size])
        del self.response[:size]
        return chunk

    def close(self):
        self.closed = True


def _response(
    job_id: int = 1,
    *,
    camera_id: int = 37,
    generation_id: int,
    response_job_id: int | None = None,
) -> bytes:
    body = json.dumps(
        {
            "ok": True,
            "camera_id": camera_id,
            "job_id": job_id,
            "generation_id": generation_id,
            "tracks": [{"track_id": 7}],
            "infer_ms": 12.5,
            "runtime": {"queue_size": 0},
        },
        separators=(",", ":"),
    ).encode()
    return RESPONSE_HEADER.pack(
        RESPONSE_MAGIC,
        1,
        RESPONSE_HEADER_SIZE,
        STATUS_OK,
        job_id if response_job_id is None else response_job_id,
        len(body),
        zlib.crc32(body) & 0xFFFFFFFF,
    ) + body


def test_inference_transport_canary_selection(monkeypatch):
    monkeypatch.setattr(settings, "inference_transport_mode", "binary_prefer")
    monkeypatch.setattr(settings, "inference_transport_camera_ids", "36, 37")
    assert inference_transport_selected(36)
    assert inference_transport_selected(37)
    assert not inference_transport_selected(38)

    monkeypatch.setattr(settings, "inference_transport_camera_ids", "*")
    assert inference_transport_selected(999)

    monkeypatch.setattr(settings, "inference_transport_mode", "http")
    assert not inference_transport_selected(37)


def test_binary_header_is_explicit_little_endian():
    assert REQUEST_HEADER_SIZE == 80
    assert RESPONSE_HEADER_SIZE == 32
    packed = REQUEST_HEADER.pack(
        REQUEST_MAGIC,
        1,
        REQUEST_HEADER_SIZE,
        0,
        37,
        11,
        12,
        13,
        1,
        2,
        1.0,
        1.0,
        704,
        576,
        1,
        0,
        3,
        4,
    )
    assert packed[16:20] == (37).to_bytes(4, "little")


def test_binary_transport_preserves_job_and_returns_result(monkeypatch):
    transport = BinaryLocalInferenceTransport(37)
    fake = FakeSocket(_response(generation_id=transport.generation_id))
    transport._socket = fake
    monkeypatch.setattr(transport, "_connect", lambda: fake)

    tracks, infer_ms, runtime = transport.submit(
        b"jpeg",
        width=704,
        height=576,
        offset_x=1,
        offset_y=2,
        scale_x=1.0,
        scale_y=1.0,
    )

    assert tracks == [{"track_id": 7}]
    assert infer_ms == pytest.approx(12.5)
    assert runtime["inference_transport_mode"] == "binary_local"
    assert runtime["inference_jobs_submitted_total"] == 1
    assert runtime["inference_payload_bytes_total"] == 4
    request = REQUEST_HEADER.unpack(bytes(fake.sent[:REQUEST_HEADER_SIZE]))
    assert request[4] == 37
    assert request[6] == 1
    assert bytes(fake.sent[REQUEST_HEADER_SIZE:]) == b"jpeg"


def test_binary_transport_rejects_stale_job_result(monkeypatch):
    transport = BinaryLocalInferenceTransport(37)
    fake = FakeSocket(
        _response(
            generation_id=transport.generation_id,
            response_job_id=999,
        )
    )
    transport._socket = fake
    monkeypatch.setattr(transport, "_connect", lambda: fake)

    with pytest.raises(InferenceTransportError, match="incompativel"):
        transport.submit(
            b"jpeg",
            width=704,
            height=576,
            offset_x=0,
            offset_y=0,
            scale_x=1.0,
            scale_y=1.0,
        )

    assert fake.closed
    assert transport.errors_total == 1


def test_binary_transport_rejects_oversized_payload():
    transport = BinaryLocalInferenceTransport(37)
    with pytest.raises(InferenceTransportError, match="payload"):
        transport.submit(
            b"",
            width=704,
            height=576,
            offset_x=0,
            offset_y=0,
            scale_x=1.0,
            scale_y=1.0,
        )
