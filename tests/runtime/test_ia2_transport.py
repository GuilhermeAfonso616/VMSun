"""Etapa 3B — protocolo binário da IA2: layout, identidade e round-trip."""

from __future__ import annotations

import json
import socket
import zlib

import numpy as np
import pytest

from app.runtime.ia2_transport import (
    IA2SocketServer,
    IA2TransportError,
    PROTOCOL_VERSION,
    REQUEST_HEADER,
    REQUEST_HEADER_SIZE,
    REQUEST_MAGIC,
    RESPONSE_HEADER,
    RESPONSE_HEADER_SIZE,
    RESPONSE_MAGIC,
    STATUS_OK,
    STATUS_QUEUE_FULL,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
    _job_id_bytes,
    ia2_transport_mode,
)
from app.core.config import settings


def test_tamanho_e_endianness_do_cabecalho():
    assert REQUEST_HEADER_SIZE == 84
    assert RESPONSE_HEADER_SIZE == 40
    packed = REQUEST_HEADER.pack(
        REQUEST_MAGIC,
        PROTOCOL_VERSION,
        REQUEST_HEADER_SIZE,
        37,
        b"0" * 16,
        3,
        2,
        11,
        0,
        10,
        6,
        4,
        3,
        2,
        72,
        123,
    )
    assert len(packed) == REQUEST_HEADER_SIZE
    # camera_id em little-endian no offset 12
    assert packed[12:16] == (37).to_bytes(4, "little")
    assert packed[0:8] == REQUEST_MAGIC


def test_resposta_carrega_identidade_e_checksum():
    body = json.dumps({"camera_id": 37}).encode()
    header = RESPONSE_HEADER.pack(
        RESPONSE_MAGIC,
        PROTOCOL_VERSION,
        RESPONSE_HEADER_SIZE,
        STATUS_OK,
        b"a" * 16,
        len(body),
        zlib.crc32(body) & 0xFFFFFFFF,
    )
    magic, version, size, status, job, body_size, crc = RESPONSE_HEADER.unpack(header)
    assert magic == RESPONSE_MAGIC
    assert version == PROTOCOL_VERSION
    assert size == RESPONSE_HEADER_SIZE
    assert status == STATUS_OK
    assert body_size == len(body)
    assert crc == zlib.crc32(body) & 0xFFFFFFFF


def test_job_id_uuid_cabe_em_16_bytes():
    from app.analytics_v2.revalidation.aux_inference_types import new_job_id

    assert len(_job_id_bytes(new_job_id())) == 16
    assert len(_job_id_bytes("nao-e-uuid")) == 16


def test_modo_invalido_gera_erro(monkeypatch):
    monkeypatch.setattr(settings, "ia2_transport_mode", "turbo")
    with pytest.raises(ValueError, match="binary_prefer"):
        ia2_transport_mode()


def test_modos_validos(monkeypatch):
    for modo in ("http", "binary_prefer", "binary_strict"):
        monkeypatch.setattr(settings, "ia2_transport_mode", modo)
        assert ia2_transport_mode() == modo


class _Stats:
    last_queue_wait_ms = 4.2


class _PoolFake:
    generation_id = 7
    stats = _Stats()


class _Resultado:
    enabled = True
    applied = True
    person_score = 0.815
    not_person_score = 0.185
    passed = True
    threshold = 0.5
    mode = "block"
    inference_ms = 12.5
    model_path = "models/ia2.pt"
    reason = "ok"
    block_eligible = False
    block_reason = None
    quality = {"blur": 0.2}
    device = "cpu"


def test_serializacao_repete_identidade_recebida():
    payload = IA2SocketServer._serialize_result(
        _Resultado(),
        camera_id=37,
        frame_id=9,
        generation_id=4,
        track_id=11,
        pool=_PoolFake(),
    )
    assert payload["camera_id"] == 37
    assert payload["frame_id"] == 9
    assert payload["generation_id"] == 4
    assert payload["track_id"] == 11
    assert payload["pool_generation_id"] == 7
    assert payload["queue_wait_ms"] == 4.2
    assert payload["person_score"] == 0.815


def test_serializacao_converte_ausentes_em_none():
    payload = IA2SocketServer._serialize_result(
        _Resultado(), camera_id=37, frame_id=-1, generation_id=-1, track_id=-1, pool=_PoolFake()
    )
    assert payload["frame_id"] is None
    assert payload["generation_id"] is None
    assert payload["track_id"] is None


def test_serializacao_nao_inclui_recorte():
    payload = IA2SocketServer._serialize_result(
        _Resultado(), camera_id=37, frame_id=1, generation_id=1, track_id=1, pool=_PoolFake()
    )
    serializado = json.dumps(payload, default=str)
    assert "crop" not in serializado
    assert "payload" not in serializado


def test_status_mapeia_excecoes_da_pool():
    from app.runtime.ia2_pool import (
        IA2PoolQueueFull,
        IA2PoolTimeout,
        IA2PoolUnavailable,
    )

    assert IA2SocketServer._status_for(IA2PoolQueueFull("x")) == STATUS_QUEUE_FULL
    assert IA2SocketServer._status_for(IA2PoolTimeout("x")) == STATUS_TIMEOUT
    assert IA2SocketServer._status_for(IA2PoolUnavailable("x")) == STATUS_UNAVAILABLE


def test_payload_bgr_faz_round_trip_sem_perda():
    """BGR cru precisa voltar identico: e o que garante a equivalencia."""
    crop = np.random.randint(0, 255, size=(12, 9, 3), dtype=np.uint8)
    bytes_ = np.ascontiguousarray(crop).tobytes()
    recuperado = np.frombuffer(bytes_, dtype=np.uint8).reshape(crop.shape)
    assert np.array_equal(crop, recuperado)
    assert len(bytes_) == 12 * 9 * 3


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX indisponivel")
def test_socket_recusa_cabecalho_invalido(tmp_path, monkeypatch):
    caminho = tmp_path / "ia2.sock"
    monkeypatch.setattr(settings, "ia2_pool_enabled", True)
    monkeypatch.setattr(settings, "ia2_transport_socket_path", str(caminho))
    servidor = IA2SocketServer(str(caminho), pool=_PoolFake())
    servidor.start()
    try:
        cliente = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        cliente.settimeout(3.0)
        cliente.connect(str(caminho))
        lixo = REQUEST_HEADER.pack(
            b"XXXXXXXX", PROTOCOL_VERSION, REQUEST_HEADER_SIZE, 37,
            b"0" * 16, -1, -1, -1, 0, 10, 6, 4, 3, 0, 72, 0,
        )
        cliente.sendall(lixo)
        resposta = cliente.recv(RESPONSE_HEADER_SIZE)
        magic, _, _, status, _, _, _ = RESPONSE_HEADER.unpack(resposta)
        assert magic == RESPONSE_MAGIC
        assert status != STATUS_OK
        cliente.close()
    finally:
        servidor.stop()
