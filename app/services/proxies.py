"""Proxy pool service.

Domain operations on state.pool. Each pool-mutating operation also
triggers ``alerts.evaluate`` under the same pool_lock so cross-endpoint
consistency holds (failed_proxy_ids on alerts always equals the current
down set as soon as a mutation lands).
"""

from __future__ import annotations

from app.core.ids import extract_proxy_id
from app.core.state import AppState, ProxyRecord
from app.services import alerts as alert_service


async def add_or_replace(
    state: AppState,
    *,
    proxies: list[str],
    replace: bool,
) -> list[ProxyRecord]:
    """Adds (or replaces) the pool. Returns the records that were accepted."""
    accepted: list[ProxyRecord] = []
    async with state.pool_lock:
        if replace:
            state.pool.clear()
        for url in proxies:
            pid = extract_proxy_id(url)
            existing = state.pool.get(pid)
            if existing is not None and not replace:
                # Keep the existing record (preserve history). Only refresh URL.
                existing.url = url
                accepted.append(existing)
                continue
            record = ProxyRecord(id=pid, url=url, status="pending")
            state.pool[pid] = record
            accepted.append(record)
        await alert_service.evaluate(state)
    return accepted


async def clear_pool(state: AppState) -> None:
    async with state.pool_lock:
        state.pool.clear()
        await alert_service.evaluate(state)
