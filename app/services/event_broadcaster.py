"""Barramento de transmissão em tempo real (SSE) para eventos e alarmes do VMS."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

from app.core.logging import get_logger

logger = get_logger("app.services.event_broadcaster")


class EventBroadcaster:
    """Gerencia assinantes SSE e distribui eventos em tempo real."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()
        self._main_loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Registra o event loop principal para permitir chamadas thread-safe."""
        self._main_loop = loop

    async def register(self) -> asyncio.Queue[str]:
        """Registra um novo cliente SSE e retorna sua fila de mensagens."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.add(queue)
            logger.info("Novo cliente SSE conectado. Total clientes: %s", len(self._subscribers))
        return queue

    async def unregister(self, queue: asyncio.Queue[str]) -> None:
        """Remove um cliente SSE desconectado."""
        async with self._lock:
            self._subscribers.discard(queue)
            logger.info("Cliente SSE desconectado. Total clientes: %s", len(self._subscribers))

    def broadcast_event_sync(self, event_data: dict[str, Any]) -> None:
        """Thread-safe hook para publicar eventos a partir de threads síncronas de persistência."""
        try:
            message = json.dumps(event_data, ensure_ascii=False, default=str)
            formatted = f"event: alarm\ndata: {message}\n\n"
        except Exception:
            logger.exception("Falha ao serializar evento para transmissão SSE")
            return

        if self._main_loop and self._main_loop.is_running():
            asyncio.run_coroutine_threadsafe(self._broadcast_text(formatted), self._main_loop)
        else:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._broadcast_text(formatted))
            except RuntimeError:
                pass

    async def broadcast_event_async(self, event_data: dict[str, Any]) -> None:
        """Publica um evento para todos os clientes SSE ativos."""
        try:
            message = json.dumps(event_data, ensure_ascii=False, default=str)
            formatted = f"event: alarm\ndata: {message}\n\n"
            await self._broadcast_text(formatted)
        except Exception:
            logger.exception("Falha na transmissão assíncrona do evento SSE")

    async def _broadcast_text(self, text: str) -> None:
        async with self._lock:
            dead_queues: set[asyncio.Queue[str]] = set()
            for queue in self._subscribers:
                try:
                    queue.put_nowait(text)
                except asyncio.QueueFull:
                    dead_queues.add(queue)

            for dead in dead_queues:
                self._subscribers.discard(dead)
                logger.warning("Fila de cliente SSE lotada; cliente removido.")

    async def stream_events(self) -> AsyncGenerator[str, None]:
        """Gerador SSE com heartbeat contínuo."""
        queue = await self.register()
        try:
            yield "event: connected\ndata: {\"status\": \"connected\"}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield message
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            await self.unregister(queue)


event_broadcaster = EventBroadcaster()
