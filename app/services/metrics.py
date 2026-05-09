"""Metrics snapshot."""

from __future__ import annotations

from app.core.state import AppState


async def snapshot(state: AppState) -> dict[str, int]:
    async with state.pool_lock:
        pool_size = len(state.pool)
    async with state.alerts_lock:
        active = 1 if state.active_alert_id is not None else 0
        total = len(state.alerts)
    async with state.metrics_lock:
        total_checks = state.metrics.total_checks
        webhook_deliveries = state.metrics.webhook_deliveries
    return {
        "total_checks": total_checks,
        "current_pool_size": pool_size,
        "active_alerts": active,
        "total_alerts": total,
        "webhook_deliveries": webhook_deliveries,
    }
