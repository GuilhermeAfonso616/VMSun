"""Ponto de entrada executavel do VMSun."""

import traceback
from multiprocessing import freeze_support

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
        logger.info("Subindo Uvicorn do VMSun em %s:%s", settings.app_host, settings.app_port)
        uvicorn.run(
            app,
            host=settings.app_host,
            port=settings.app_port,
            reload=False,
            log_level="info",
        )
    except Exception:
        logger.exception("Falha fatal durante execucao do servidor VMSun")
        with open("startup_error.log", "w", encoding="utf-8") as error_file:
            error_file.write(traceback.format_exc())
        raise
