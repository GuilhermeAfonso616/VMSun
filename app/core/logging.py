from __future__ import annotations

"""Configura logging padrao da aplicacao e um logger dedicado para as regras de evento.

O logger normal segue o formato contextual do projeto.
O logger de debug de regras grava um arquivo separado para depuracao de ROI,
linha e decisao de eventos sem poluir o log principal.
"""

import logging
import os
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.timezone import BRAZIL_TZ
from app.core.url_safety import mask_url_credentials


_LOGGING_CONFIGURED = False
_EVENT_RULES_DEBUG_LOGGER: logging.Logger | None = None
_INFERENCE_POOL_LOGGER: logging.Logger | None = None

# Env usada pelos processos filhos para escrever em arquivos proprios (ver setup_logging).
LOG_FILE_SUFFIX_ENV = "SERVER_ANALITICO_LOG_SUFFIX"

# Campos de contexto conhecidos, na ordem em que aparecem na linha de log.
# Só os preenchidos entram na saida: antes, os 14 campos eram sempre emitidos e
# ocupavam ~146 chars por linha mesmo quando todos valiam "-".
_CONTEXT_FIELDS: tuple[tuple[str, str], ...] = (
    ("request_id", "req"),
    ("camera_id", "cam"),
    ("worker_pid", "pid"),
    ("worker_mode", "mode"),
    ("worker_generation", "gen"),
    ("action", "action"),
    ("status", "status"),
    ("reason", "reason"),
    ("event_id", "event"),
    ("lifecycle_action", "lifecycle"),
    ("correlation_key", "corr"),
    ("related_event_id", "related"),
    ("alarm_eligible", "eligible"),
    ("is_alarm_active", "active"),
    ("dedupe_key", "dedupe"),
)

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(context)s%(message)s"

# O id da requisicao vive no contexto async: antes ele era gerado no middleware e
# morria ali, entao a coluna req= ficava vazia em tudo que os services logavam.
_REQUEST_ID: ContextVar[str] = ContextVar("analitico_request_id", default="")


def current_request_id() -> str:
    """Id da requisicao em curso neste contexto, ou string vazia fora de uma."""
    return _REQUEST_ID.get()


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


@contextmanager
def request_id_context(request_id: str | None = None) -> Iterator[str]:
    """Associa um id de requisicao a tudo que for logado dentro do bloco.

    Starlette copia o contexto ao despachar rotas sync para o threadpool, entao o id
    alcanca tambem os handlers definidos com `def` comum.
    """
    value = str(request_id or "").strip() or new_request_id()
    token = _REQUEST_ID.set(value)
    try:
        yield value
    finally:
        _REQUEST_ID.reset(token)


class RequestContextFilter(logging.Filter):
    """Preenche request_id a partir do contexto quando o chamador nao informou."""

    def filter(self, record: logging.LogRecord) -> bool:
        current = getattr(record, "request_id", None)
        if current in (None, "", "-"):
            request_id = _REQUEST_ID.get()
            if request_id:
                record.request_id = request_id
        return True


class _BrazilTimeFormatter(logging.Formatter):
    """Formata o horario dos logs no fuso de Brasilia (America/Sao_Paulo),
    independente do timezone do container/host (que costuma ser UTC)."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, BRAZIL_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return "%s,%03d" % (dt.strftime("%Y-%m-%d %H:%M:%S"), record.msecs)


class ContextFormatter(_BrazilTimeFormatter):
    """Emite apenas os campos de contexto que o registro realmente carrega.

    Campos ausentes, vazios ou com o placeholder "-" sao omitidos, entao uma linha
    sem contexto fica so com "data | nivel | logger | mensagem".
    """

    def format(self, record: logging.LogRecord) -> str:
        parts = []
        for attribute, label in _CONTEXT_FIELDS:
            value = getattr(record, attribute, None)
            if value is None:
                continue
            text = str(value)
            if not text or text == "-":
                continue
            parts.append(f"{label}={text}")

        record.context = " | ".join(parts) + " | " if parts else ""
        return super().format(record)


def ensure_logs_dir() -> Path:
    logs_dir = Path(settings.logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def resolve_log_file_suffix(explicit: str | None = None) -> str:
    """Sufixo aplicado aos arquivos de log deste processo.

    Cada processo precisa do seu proprio par app/error, porque RotatingFileHandler
    nao coordena o rollover entre processos: com varios workers no mesmo arquivo os
    backups eram sobrescritos e historico se perdia.
    """
    suffix = explicit if explicit is not None else os.environ.get(LOG_FILE_SUFFIX_ENV, "")
    suffix = str(suffix or "").strip().strip(".")
    if not suffix:
        return ""
    return "".join(char if (char.isalnum() or char in "-_") else "-" for char in suffix)


def _log_file_name(stem: str, suffix: str) -> str:
    return f"{stem}.{suffix}.log" if suffix else f"{stem}.log"


def setup_logging(file_suffix: str | None = None) -> None:
    global _LOGGING_CONFIGURED

    if _LOGGING_CONFIGURED:
        return

    logs_dir = ensure_logs_dir()
    suffix = resolve_log_file_suffix(file_suffix)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    # Um formatter unico facilita correlacionar camera, evento e processo em todos os logs.
    formatter = ContextFormatter(fmt=_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    # O filtro fica nos handlers, e nao no logger raiz, para alcancar tambem os
    # registros que sobem por propagacao dos loggers de cada modulo.
    context_filter = RequestContextFilter()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)
    root_logger.addHandler(console_handler)

    app_file_handler = RotatingFileHandler(
        logs_dir / _log_file_name("app", suffix),
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    app_file_handler.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    app_file_handler.setFormatter(formatter)
    app_file_handler.addFilter(context_filter)
    root_logger.addHandler(app_file_handler)

    error_file_handler = RotatingFileHandler(
        logs_dir / _log_file_name("error", suffix),
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    error_file_handler.setLevel(logging.ERROR)
    error_file_handler.setFormatter(formatter)
    error_file_handler.addFilter(context_filter)
    root_logger.addHandler(error_file_handler)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    # httpx/httpcore emitem um INFO por chamada HTTP de saida e dominavam o app.log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _LOGGING_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


def log_ignored_exception(action: str, *, level: int = logging.DEBUG, **context: Any) -> None:
    """Registra uma excecao que o chamador decidiu ignorar.

    Substitui `except ...: pass` sem alterar o fluxo: a excecao continua engolida,
    mas deixa rastro. Descobre o logger pelo modulo do chamador, entao nao exige um
    logger declarado no escopo.

    DEBUG serve para falhas realmente inofensivas (limpeza de recurso, parsing com
    default). Use WARNING quando ignorar tem custo operacional — perder um registro
    de auditoria, por exemplo.

    Deve ser chamada de dentro do bloco `except`, para o traceback ficar disponivel.
    """
    try:
        caller = sys._getframe(1)
        module_name = caller.f_globals.get("__name__", "app")
    except Exception:  # pragma: no cover - introspeccao nunca deve derrubar o chamador
        module_name = "app"

    logger = get_logger(module_name)
    if not logger.isEnabledFor(level):
        return

    extra = {"action": action, "status": "ignored"}
    extra.update({key: value for key, value in context.items() if value is not None})
    try:
        logger.log(level, "Excecao ignorada em %s", action, exc_info=True, extra=extra)
    except Exception:  # pragma: no cover - logging nunca pode quebrar o fluxo
        pass


def get_event_rules_debug_logger() -> logging.Logger:
    global _EVENT_RULES_DEBUG_LOGGER

    setup_logging()

    if _EVENT_RULES_DEBUG_LOGGER is not None:
        return _EVENT_RULES_DEBUG_LOGGER

    logs_dir = ensure_logs_dir()
    logger = logging.getLogger("app.analytics.event_rules_debug")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        # Log separado para inspecionar o estado interno das regras sem ruído do app.log.
        handler = RotatingFileHandler(
            logs_dir / _log_file_name("event_rules_debug", resolve_log_file_suffix()),
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(_BrazilTimeFormatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)

    _EVENT_RULES_DEBUG_LOGGER = logger
    return logger


def get_inference_pool_logger() -> logging.Logger:
    global _INFERENCE_POOL_LOGGER

    setup_logging()

    if _INFERENCE_POOL_LOGGER is not None:
        return _INFERENCE_POOL_LOGGER

    logs_dir = ensure_logs_dir()
    logger = logging.getLogger("app.runtime.inference_pool")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        handler = RotatingFileHandler(
            logs_dir / _log_file_name("inference_pool", resolve_log_file_suffix()),
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(_BrazilTimeFormatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)

    _INFERENCE_POOL_LOGGER = logger
    return logger


class CameraLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]):
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("camera_id", self.extra.get("camera_id", "-"))
        extra.setdefault("worker_mode", self.extra.get("worker_mode", "-"))
        extra.setdefault("request_id", self.extra.get("request_id", "-"))
        extra.setdefault("worker_pid", self.extra.get("worker_pid", "-"))
        extra.setdefault("event_id", self.extra.get("event_id", "-"))
        extra.setdefault("action", self.extra.get("action", "-"))
        extra.setdefault("reason", self.extra.get("reason", "-"))
        extra.setdefault("status", self.extra.get("status", "-"))
        extra.setdefault("dedupe_key", self.extra.get("dedupe_key", "-"))
        extra.setdefault("lifecycle_action", self.extra.get("lifecycle_action", "-"))
        extra.setdefault("correlation_key", self.extra.get("correlation_key", "-"))
        extra.setdefault("related_event_id", self.extra.get("related_event_id", "-"))
        extra.setdefault("alarm_eligible", self.extra.get("alarm_eligible", "-"))
        extra.setdefault("is_alarm_active", self.extra.get("is_alarm_active", "-"))
        return msg, kwargs


def get_camera_logger(name: str, camera_id: int, worker_mode: str) -> CameraLoggerAdapter:
    logger = get_logger(name)
    return CameraLoggerAdapter(
        logger,
        {
            "camera_id": camera_id,
            "worker_mode": worker_mode,
            "request_id": "-",
            "worker_pid": "-",
            "event_id": "-",
            "action": "-",
            "reason": "-",
            "status": "-",
            "dedupe_key": "-",
            "lifecycle_action": "-",
            "correlation_key": "-",
            "related_event_id": "-",
            "alarm_eligible": "-",
            "is_alarm_active": "-",
        },
    )


REQUEST_ID_HEADER = "X-Request-ID"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        logger = get_logger("app.http")
        # Reaproveita o id do proxy/cliente quando houver, para a correlacao
        # atravessar o limite do servico.
        incoming = request.headers.get(REQUEST_ID_HEADER, "")[:64]

        started = time.perf_counter()
        client_ip = "-"
        try:
            if request.client and request.client.host:
                client_ip = request.client.host
        except Exception:
            pass

        with request_id_context(incoming) as request_id:
            try:
                response = await call_next(request)
                duration_ms = (time.perf_counter() - started) * 1000
                logger.info(
                    "%s %s -> %s in %.2f ms from %s",
                    request.method,
                    request.url.path,
                    response.status_code,
                    duration_ms,
                    client_ip,
                    extra={
                        "request_id": request_id,
                        "action": "http_request",
                        "status": str(response.status_code),
                        "reason": request.url.path,
                    },
                )
                response.headers.setdefault(REQUEST_ID_HEADER, request_id)
                return response
            except Exception:
                duration_ms = (time.perf_counter() - started) * 1000
                logger.exception(
                    "%s %s -> EXCEPTION in %.2f ms from %s",
                    request.method,
                    request.url.path,
                    duration_ms,
                    client_ip,
                    extra={
                        "request_id": request_id,
                        "action": "http_request",
                        "status": "exception",
                        "reason": request.url.path,
                    },
                )
                raise


def log_environment_summary() -> None:
    logger = get_logger("app.startup")
    logger.info("Logs directory: %s", os.fspath(ensure_logs_dir()))
    logger.info("Log level: %s", settings.log_level)
    # Nunca registrar a URL crua: ela carrega a senha do Postgres.
    logger.info("Database URL: %s", mask_url_credentials(settings.database_url))
    logger.info("Detector model path: %s", settings.detector_model_path)
    logger.info("Detector engine path: %s", settings.detector_engine_path or "-")
    logger.info("Detector engine auto-build: %s", settings.detector_engine_auto_build_enabled)
