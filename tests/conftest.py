"""Isola os efeitos colaterais dos testes do estado real da aplicacao.

Sem isto a suite escreve no mesmo logs/app.log e logs/error.log usados em producao:
requisicoes de testserver, loggers "test.*" e excecoes fabricadas (RuntimeError: boom)
acabavam misturadas com falhas reais, tornando o error.log inutil como sinal operacional.

As variaveis precisam existir antes de qualquer import de app.core.config, porque
Settings le o ambiente uma unica vez na importacao do modulo.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_TEST_LOGS_DIR = Path(tempfile.mkdtemp(prefix="analitico-test-logs-"))

os.environ["LOGS_DIR"] = str(_TEST_LOGS_DIR)
# Marca o processo de teste para nao colidir com arquivos de um servidor rodando em paralelo.
os.environ.setdefault("SERVER_ANALITICO_LOG_SUFFIX", "tests")

if "app.core.config" in sys.modules:  # pragma: no cover - protege contra import precoce
    raise RuntimeError(
        "app.core.config foi importado antes do conftest; os logs de teste iriam para o diretorio real."
    )


@pytest.fixture(scope="session")
def test_logs_dir() -> Path:
    """Diretorio temporario onde a suite grava seus logs."""
    return _TEST_LOGS_DIR


def pytest_report_header() -> str:
    return f"logs de teste: {_TEST_LOGS_DIR}"
