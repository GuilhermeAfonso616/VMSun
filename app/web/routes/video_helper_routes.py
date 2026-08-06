"""Distribuicao do SunOrus Video Helper para as estacoes de operacao."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from app.db.models import User
from app.services.video_helper_distribution import (
    latest_payload,
    latest_setup,
    payload_checksum,
)
from app.web.infrastructure import require_web_auth

router = APIRouter(prefix="/downloads/video-helper", tags=["video-helper"])

DOWNLOAD_ROLES = ["admin", "supervisor", "operator", "viewer", "dev"]


@router.get("/status")
def video_helper_status(
    current_user: User = Depends(require_web_auth(DOWNLOAD_ROLES)),
) -> JSONResponse:
    """Diz ao mosaico se ha instalador publicado para oferecer ao operador."""
    setup = latest_setup()
    if setup is None:
        return JSONResponse({"disponivel": False})

    return JSONResponse(
        {
            "disponivel": True,
            "versao": setup.version,
            "tamanho_mb": setup.size_mb,
            "arquivo": setup.path.name,
            "url": "/downloads/video-helper/setup",
        }
    )


@router.get("/setup")
def video_helper_setup(
    current_user: User = Depends(require_web_auth(DOWNLOAD_ROLES)),
) -> FileResponse:
    setup = latest_setup()
    if setup is None:
        raise HTTPException(status_code=404, detail="Nenhum instalador publicado no servidor.")

    return FileResponse(
        path=setup.path,
        filename=setup.path.name,
        media_type="application/vnd.microsoft.portable-executable",
    )


@router.get("/payload")
def video_helper_payload() -> FileResponse:
    """Pacote que o instalador enxuto baixa durante a instalacao.

    Fica fora da sessao web de proposito: quem busca aqui e o setup rodando na
    maquina do operador, que nao tem cookie. O conteudo e o mesmo binario que ja
    seria distribuido pelo instalador completo, e a integridade e garantida pelo
    SHA-256 gravado no proprio setup em tempo de build.
    """
    payload = latest_payload()
    if payload is None:
        raise HTTPException(status_code=404, detail="Nenhum pacote publicado no servidor.")

    headers = {}
    checksum = payload_checksum(payload)
    if checksum:
        # Ajuda a diagnosticar proxy que serve conteudo trocado ou truncado.
        headers["X-Payload-SHA256"] = checksum

    return FileResponse(
        path=payload.path,
        filename=payload.path.name,
        media_type="application/zip",
        headers=headers,
    )
