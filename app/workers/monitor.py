"""Continuous probe loop.

Wakes up every ``check_interval_seconds``, probes every proxy in the pool
concurrently, classifies each result, writes the round atomically, and
finally evaluates the alert state machine. A POST /config sets
``state.config_changed`` so the loop can re-snapshot the new interval and
timeout immediately rather than waiting out the previous interval.

Lock ordering (taken in this order, never reversed):
    pool_lock -> alerts_lock -> webhooks_lock / integrations_lock
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.core.state import AppState, CheckEvent, ProxyRecord
from app.core.time import utcnow_iso
from app.services import alerts as alert_service

logger = logging.getLogger(__name__)

PROBE_CONCURRENCY = 50


@dataclass
class _ProbeResult:
    proxy_id: str
    status: str  # "up" | "down"


async def run_monitor(state: AppState) -> None:
    """Long-running loop. Cancellation propagates cleanly."""
    logger.info("monitor: starting")
    while not state.shutting_down:
        try:
            await _one_round(state)
        except asyncio.CancelledError:
            logger.info("monitor: cancelled")
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("monitor round failed: %s", exc)

        async with state.config_lock:
            interval = state.config.check_interval_seconds
        try:
            await asyncio.wait_for(state.config_changed.wait(), timeout=interval)
            state.config_changed.clear()
        except TimeoutError:
            pass


async def _one_round(state: AppState) -> None:
    async with state.pool_lock:
        snapshot = [(p.id, p.url) for p in state.pool.values()]
    async with state.config_lock:
        timeout_s = state.config.request_timeout_ms / 1000.0

    if not snapshot:
        return

    client = state.http_client
    if client is None:
        async with httpx.AsyncClient() as fallback:
            results = await _probe_all(fallback, snapshot, timeout_s)
    else:
        results = await _probe_all(client, snapshot, timeout_s)

    # Apply results and evaluate atomically under pool_lock.
    async with state.pool_lock:
        _apply_results_locked(state, results)
        # alerts.evaluate reads pool without locking — caller (us) holds pool_lock.
        await alert_service.evaluate(state)
        # metrics_lock taken briefly inside the same critical section.
        async with state.metrics_lock:
            state.metrics.total_checks += len(results)


async def _probe_all(
    client: httpx.AsyncClient,
    snapshot: list[tuple[str, str]],
    timeout_s: float,
) -> list[_ProbeResult]:
    sem = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def _probe(pid: str, url: str) -> _ProbeResult:
        async with sem:
            return _ProbeResult(pid, await _classify(client, url, timeout_s))

    return await asyncio.gather(*[_probe(pid, url) for pid, url in snapshot])


async def _classify(client: httpx.AsyncClient, url: str, timeout_s: float) -> str:
    try:
        response = await client.get(url, timeout=timeout_s)
    except httpx.TimeoutException:
        return "down"
    except httpx.TransportError:
        return "down"
    except Exception:  # pragma: no cover - defensive
        return "down"

    if 200 <= response.status_code < 300:
        return "up"
    return "down"


def _apply_results_locked(state: AppState, results: list[_ProbeResult]) -> None:
    """Caller must hold state.pool_lock."""
    now = utcnow_iso()
    for r in results:
        record: ProxyRecord | None = state.pool.get(r.proxy_id)
        if record is None:
            continue  # proxy was removed between snapshot and apply
        record.status = r.status  # type: ignore[assignment]
        record.last_checked_at = now
        record.total_checks += 1
        if r.status == "up":
            record.up_checks += 1
            record.consecutive_failures = 0
        else:
            record.consecutive_failures += 1
        record.history.append(CheckEvent(checked_at=now, status=r.status))  # type: ignore[arg-type]
