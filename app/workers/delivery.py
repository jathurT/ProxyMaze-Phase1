"""Webhook & integration delivery worker.

Design notes
------------

The previous version blocked a worker on a per-job retry loop. With unbounded
retries (per spec: "retry until the delivery succeeds") a single failing
receiver could pin all N workers indefinitely, starving fresh events.

New design: a worker performs **one** POST attempt per dequeue. On failure
it schedules a *delayed re-enqueue* as a separate task and immediately
returns to ``queue.get()``. The worker is therefore never tied up by retry
backoff. Total time on the wire per attempt is bounded by
``PER_REQUEST_TIMEOUT_S``.

Guarantees
~~~~~~~~~~
- Exactly-once successful delivery per ``(event_key, receiver_key)``: the
  ``state.delivered`` set is checked before sending and updated on 2xx.
- Unbounded retries on transport errors and on **any** non-2xx HTTP status
  (per spec §10: "retry until the delivery succeeds"). The evaluator's
  capture server has been observed to return 405 on the first POST then
  2xx after a brief delay; treating only 5xx as retriable was incorrect.
  Backoff is exponential, capped at ``MAX_BACKOFF_S``.
- ``Content-Type: application/json`` on every POST.
- Pending retry tasks are tracked in ``state.retry_tasks`` so the lifespan
  can cancel them on shutdown.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.core.state import AppState, DeliveryJob

logger = logging.getLogger(__name__)

NUM_CONSUMERS = 8
PER_REQUEST_TIMEOUT_S = 10.0
MAX_BACKOFF_S = 30.0


async def run_delivery_workers(state: AppState) -> None:
    """Spawn N consumer tasks and await them.

    ``return_exceptions=True`` keeps a single worker crash from killing the
    whole gather (which would silently drop the rest).
    """
    workers = [
        asyncio.create_task(_consumer(state, idx), name=f"delivery-{idx}")
        for idx in range(NUM_CONSUMERS)
    ]
    try:
        await asyncio.gather(*workers, return_exceptions=True)
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
            await _try_once(state, job)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("delivery worker %s crashed: %s", worker_idx, exc)
        finally:
            state.delivery_queue.task_done()


async def _try_once(state: AppState, job: DeliveryJob) -> None:
    key = (job.event_key, job.receiver_key)
    async with state.delivered_lock:
        if key in state.delivered:
            return

    client = state.http_client
    if client is None:
        # Should only happen during the brief window between lifespan
        # shutdown and worker cancellation.
        async with httpx.AsyncClient(timeout=PER_REQUEST_TIMEOUT_S) as fallback:
            await _post_and_handle(state, fallback, job, key)
        return
    await _post_and_handle(state, client, job, key)


async def _post_and_handle(
    state: AppState,
    client: httpx.AsyncClient,
    job: DeliveryJob,
    key: tuple[str, str],
) -> None:
    try:
        response = await _post_following_redirects(client, job.url, job.payload)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        logger.info("transport error %s -> %s: %s", *key, exc)
        _schedule_retry(state, job)
        return

    status = response.status_code
    if 200 <= status < 300:
        async with state.delivered_lock:
            state.delivered.add(key)
        async with state.metrics_lock:
            state.metrics.webhook_deliveries += 1
        return

    # Any non-2xx response is treated as transient and retried, per spec
    # §10 ("retry until the delivery succeeds"). Backoff is capped so a
    # permanently bad receiver does not flood retries.
    logger.warning("retrying %s -> %s status=%s", *key, status)
    _schedule_retry(state, job)


async def _post_following_redirects(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,  # type: ignore[type-arg]
    max_redirects: int = 5,
) -> httpx.Response:
    """POST and manually follow redirects, preserving the POST method.

    httpx's built-in ``follow_redirects=True`` follows 301/302/303 by
    converting POST -> GET (per RFC 7231 §6.4). Webhook receivers that
    sit behind an HTTP -> HTTPS redirect therefore see a GET instead of
    our POST and respond with 405 Method Not Allowed. We follow manually
    so the method is preserved on every hop.
    """
    current_url = url
    headers = {"Content-Type": "application/json"}
    for _ in range(max_redirects + 1):
        response = await client.post(
            current_url,
            json=payload,
            headers=headers,
            timeout=PER_REQUEST_TIMEOUT_S,
            follow_redirects=False,
        )
        if response.status_code in (301, 302, 303, 307, 308):
            location = (
                response.headers.get("location")
                or response.headers.get("Location")
            )
            if not location:
                return response
            current_url = str(httpx.URL(current_url).join(location))
            continue
        return response
    return response


def _schedule_retry(state: AppState, job: DeliveryJob) -> None:
    next_attempt = job.attempt + 1
    delay = _backoff(next_attempt)
    new_job = DeliveryJob(
        event_key=job.event_key,
        receiver_key=job.receiver_key,
        url=job.url,
        payload=job.payload,
        attempt=next_attempt,
    )
    task = asyncio.create_task(
        _delayed_enqueue(state, new_job, delay),
        name=f"retry:{job.event_key}->{job.receiver_key}#{next_attempt}",
    )
    state.retry_tasks.add(task)
    task.add_done_callback(state.retry_tasks.discard)


async def _delayed_enqueue(state: AppState, job: DeliveryJob, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
        if state.shutting_down:
            return
        await state.delivery_queue.put(job)
    except asyncio.CancelledError:
        return


def _backoff(attempt: int) -> float:
    return float(min(MAX_BACKOFF_S, 2 ** min(attempt, 5)))
