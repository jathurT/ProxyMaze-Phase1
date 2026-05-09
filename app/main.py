"""FastAPI application factory + lifespan.

The lifespan boots two long-running asyncio tasks:
  - ``monitor`` (continuous proxy probing)
  - ``delivery`` (webhook + integration sender)

Both tasks live for the process lifetime. On shutdown, they are
cooperatively cancelled and the shared httpx.AsyncClient is closed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.core.state import get_state
from app.workers.delivery import run_delivery_workers
from app.workers.monitor import run_monitor

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    state = get_state()
    state.shutting_down = False
    state.http_client = httpx.AsyncClient(follow_redirects=True)

    monitor_task = asyncio.create_task(run_monitor(state), name="monitor")
    delivery_task = asyncio.create_task(run_delivery_workers(state), name="delivery")
    state.background_tasks = [monitor_task, delivery_task]

    logger.info("ProxyMaze: background tasks started")
    try:
        yield
    finally:
        state.shutting_down = True
        state.config_changed.set()
        for task in state.background_tasks:
            task.cancel()
        for task in list(state.retry_tasks):
            task.cancel()
        await asyncio.gather(*state.background_tasks, return_exceptions=True)
        if state.retry_tasks:
            await asyncio.gather(*state.retry_tasks, return_exceptions=True)
        if state.http_client is not None:
            await state.http_client.aclose()
            state.http_client = None
        state.background_tasks = []
        state.retry_tasks = set()
        logger.info("ProxyMaze: shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    application.include_router(router)
    return application


app = create_app()
