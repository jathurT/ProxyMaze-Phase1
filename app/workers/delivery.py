"""Webhook & integration delivery worker.

Consumes ``state.delivery_queue`` from N parallel asyncio tasks. Provides:

- Exactly-once successful delivery per (event_key, receiver_key).
- Retry on transport errors and HTTP 500/502/503/504 with exponential
  backoff capped at 30s. Retries are unlimited (per spec
  "retry until the delivery succeeds").
- Non-retriable HTTP errors (e.g. 4xx) are marked delivered to avoid an
  infinite loop on a permanently bad receiver URL.
- Each request sets ``Content-Type: application/json``.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.core.state import AppState, DeliveryJob

logger = logging.getLogger(__name__)

NUM_CONSUMERS = 4
RETRYABLE_STATUS = {500, 502, 503, 504}
PER_REQUEST_TIMEOUT_S = 10.0
MAX_BACKOFF_S = 30.0


async def run_delivery_workers(state: AppState) -> None:
    """Spawn N consumer tasks and await them. Cancellation propagates."""
    workers = [
        asyncio.create_task(_consumer(state, idx), name=f"delivery-{idx}")
        for idx in range(NUM_CONSUMERS)
    ]
    try:
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise


async def _consumer(state: AppState, worker_idx: int) -> None:
    while not state.shutting_down:
        try:
            job: DeliveryJob = await state.delivery_queue.get()
        except asyncio.CancelledError:
            return
        try:
            await _deliver_one(state, job)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("delivery worker %s crashed on job: %s", worker_idx, exc)
        finally:
            state.delivery_queue.task_done()


async def _deliver_one(state: AppState, job: DeliveryJob) -> None:
    key = (job.event_key, job.receiver_key)
    async with state.delivered_lock:
        if key in state.delivered:
            return

    client = state.http_client
    if client is None:
        # If the lifespan didn't set up the client (e.g. tests), make a one-off
        async with httpx.AsyncClient(timeout=PER_REQUEST_TIMEOUT_S) as fallback:
            await _send_with_retries(state, fallback, job, key)
        return
    await _send_with_retries(state, client, job, key)


async def _send_with_retries(
    state: AppState,
    client: httpx.AsyncClient,
    job: DeliveryJob,
    key: tuple[str, str],
) -> None:
    attempt = job.attempt
    while not state.shutting_down:
        try:
            response = await client.post(
                job.url,
                json=job.payload,
                headers={"Content-Type": "application/json"},
                timeout=PER_REQUEST_TIMEOUT_S,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.info("transport error delivering %s -> %s: %s", *key, exc)
            await asyncio.sleep(_backoff(attempt))
            attempt += 1
            continue

        status = response.status_code
        if 200 <= status < 300:
            async with state.delivered_lock:
                state.delivered.add(key)
            async with state.metrics_lock:
                state.metrics.webhook_deliveries += 1
            return

        if status in RETRYABLE_STATUS:
            logger.info(
                "retryable status %s delivering %s -> %s; backing off",
                status,
                *key,
            )
            await asyncio.sleep(_backoff(attempt))
            attempt += 1
            continue

        # Non-retriable HTTP error; record as delivered to avoid infinite loop.
        logger.warning(
            "non-retriable status %s delivering %s -> %s; giving up",
            status,
            *key,
        )
        async with state.delivered_lock:
            state.delivered.add(key)
        return


def _backoff(attempt: int) -> float:
    return float(min(MAX_BACKOFF_S, 2 ** min(attempt, 5)))
