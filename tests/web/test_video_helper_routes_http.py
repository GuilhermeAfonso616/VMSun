from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.models import User
from app.services import video_helper_distribution
from app.web.infrastructure import get_web_user
from app.web.routes import video_helper_routes


@pytest.fixture
def helper_http(monkeypatch, tmp_path):
    dist = tmp_path / "video-helper"
    dist.mkdir()
    monkeypatch.setattr(
        video_helper_distribution.settings,
        "video_helper_dist_dir",
        str(dist),
        raising=False,
    )

    application = FastAPI()
    application.include_router(video_helper_routes.router)
    application.dependency_overrides[get_web_user] = lambda: User(
        id=7,
        username="operador",
        role="operator",
        is_active=True,
    )
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(client=client, dist=dist)
    finally:
        application.dependency_overrides.clear()


def _publicar(dist, nome, conteudo=b"MZ conteudo de teste"):
    caminho = dist / nome
    caminho.write_bytes(conteudo)
    return caminho


def test_status_reporta_indisponivel_quando_nada_publicado(helper_http):
    resposta = helper_http.client.get("/downloads/video-helper/status")

    assert resposta.status_code == 200
    assert resposta.json() == {"disponivel": False}


def test_status_expoe_versao_mais_recente_e_tamanho(helper_http):
    _publicar(helper_http.dist, "SunOrus-Video-Helper-Setup-0.4.1-win-x64.exe", b"x" * 2048)
    _publicar(helper_http.dist, "SunOrus-Video-Helper-Setup-0.10.0-win-x64.exe", b"y" * 4096)

    corpo = helper_http.client.get("/downloads/video-helper/status").json()

    # 0.10.0 e mais novo que 0.4.1: ordenar por texto escolheria errado.
    assert corpo["disponivel"] is True
    assert corpo["versao"] == "0.10.0"
    assert corpo["arquivo"] == "SunOrus-Video-Helper-Setup-0.10.0-win-x64.exe"
    assert corpo["url"] == "/downloads/video-helper/setup"


def test_setup_entrega_o_arquivo_publicado(helper_http):
    _publicar(helper_http.dist, "SunOrus-Video-Helper-Setup-0.4.1-win-x64.exe", b"instalador")

    resposta = helper_http.client.get("/downloads/video-helper/setup")

    assert resposta.status_code == 200
    assert resposta.content == b"instalador"
    assert "SunOrus-Video-Helper-Setup-0.4.1-win-x64.exe" in resposta.headers["content-disposition"]


def test_setup_sem_publicacao_responde_404(helper_http):
    assert helper_http.client.get("/downloads/video-helper/setup").status_code == 404


def test_payload_dispensa_sessao_e_publica_checksum(helper_http):
    zip_path = _publicar(
        helper_http.dist,
        "SunOrusVideoHelperPayload-0.4.1-win-x64.zip",
        b"PK pacote",
    )
    checksum = "a" * 64
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{checksum}  {zip_path.name}\n",
        encoding="ascii",
    )

    # Sem override de usuario: o instalador que baixa daqui nao tem cookie.
    sem_sessao = FastAPI()
    sem_sessao.include_router(video_helper_routes.router)
    with TestClient(sem_sessao) as client:
        resposta = client.get("/downloads/video-helper/payload")

    assert resposta.status_code == 200
    assert resposta.content == b"PK pacote"
    assert resposta.headers["x-payload-sha256"] == checksum


def test_payload_ignora_checksum_malformado(helper_http):
    zip_path = _publicar(helper_http.dist, "SunOrusVideoHelperPayload-0.4.1-win-x64.zip")
    zip_path.with_name(zip_path.name + ".sha256").write_text("nao-e-hash\n", encoding="ascii")

    resposta = helper_http.client.get("/downloads/video-helper/payload")

    assert resposta.status_code == 200
    assert "x-payload-sha256" not in resposta.headers
