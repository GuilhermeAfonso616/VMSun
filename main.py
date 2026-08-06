"""Ponto de entrada executavel do Server Analitico."""

import traceback
from multiprocessing import freeze_support

# Importa o torch antes de qualquer modulo que carregue cv2 (via app.application).
# No Windows, se o OpenCV carregar suas DLLs primeiro, as extensoes nativas do
# torchvision quebram de forma intermitente (RuntimeError: operator
# torchvision::nms does not exist / AttributeError: partially initialized
# module 'torchvision' has no attribute '_extension'), derrubando o
# revalidador IA2/IA3 que roda no processo principal via ThreadPoolExecutor.
import torch  # noqa: E402,F401

import uvicorn

from app.application import create_app
from app.core.config import settings
from app.core.logging import get_logger, setup_logging


app = create_app()


if __name__ == "__main__":
    freeze_support()
    setup_logging()
    logger = get_logger("app.startup")

    try:
        logger.info("Subindo Uvicorn em %s:%s", settings.app_host, settings.app_port)
        uvicorn.run(
            app,
            host=settings.app_host,
            port=settings.app_port,
            reload=False,
            log_level="info",
        )
    except Exception:
        logger.exception("Falha fatal durante execucao do servidor")
        with open("startup_error.log", "w", encoding="utf-8") as error_file:
            error_file.write(traceback.format_exc())
        raise
