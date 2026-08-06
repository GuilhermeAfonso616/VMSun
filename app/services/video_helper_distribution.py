"""Localiza os pacotes do SunOrus Video Helper disponiveis para download.

O mosaico oferece o instalador quando o navegador nao decodifica HEVC e o helper
nao esta rodando na maquina do operador. Os arquivos nao vao na imagem: ficam no
volume de dados, entao publicar uma versao nova nao exige rebuild do container.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("services.video_helper_distribution")

SETUP_PATTERN = "SunOrus-Video-Helper-Setup-*.exe"
PAYLOAD_PATTERN = "SunOrusVideoHelperPayload-*.zip"

# Aceita 0.4.1 e 0.4.1-win-x64 dentro do nome do arquivo.
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class HelperPackage:
    path: Path
    version: str
    size_bytes: int

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / (1024 * 1024), 2)


def distribution_dir() -> Path:
    return Path(settings.video_helper_dist_dir)


def latest_setup() -> HelperPackage | None:
    """Instalador mais recente por versao; empate resolve pela data do arquivo."""
    return _latest(SETUP_PATTERN)


def latest_payload() -> HelperPackage | None:
    """Pacote que o instalador enxuto (~52 KB) baixa durante a instalacao."""
    return _latest(PAYLOAD_PATTERN)


def payload_checksum(payload: HelperPackage) -> str | None:
    """SHA-256 gerado pelo build ao lado do zip, quando publicado junto."""
    arquivo = payload.path.with_name(payload.path.name + ".sha256")
    if not arquivo.is_file():
        return None
    try:
        conteudo = arquivo.read_text(encoding="ascii", errors="ignore").strip()
    except OSError:
        logger.warning("Nao foi possivel ler o checksum em %s", arquivo)
        return None
    primeiro = conteudo.split()[0] if conteudo else ""
    return primeiro if len(primeiro) == 64 else None


def _latest(pattern: str) -> HelperPackage | None:
    diretorio = distribution_dir()
    if not diretorio.is_dir():
        return None

    candidatos = [caminho for caminho in diretorio.glob(pattern) if caminho.is_file()]
    if not candidatos:
        return None

    escolhido = max(candidatos, key=lambda caminho: (_version_key(caminho.name), caminho.stat().st_mtime))
    return HelperPackage(
        path=escolhido,
        version=_version_text(escolhido.name),
        size_bytes=escolhido.stat().st_size,
    )


def _version_key(nome: str) -> tuple[int, int, int]:
    encontrado = _VERSION_RE.search(nome)
    if not encontrado:
        return (0, 0, 0)
    return tuple(int(parte) for parte in encontrado.groups())  # type: ignore[return-value]


def _version_text(nome: str) -> str:
    encontrado = _VERSION_RE.search(nome)
    return encontrado.group(0) if encontrado else "desconhecida"
