"""Thin wrapper around webhooks.register_integration for symmetry."""

from __future__ import annotations

from app.core.state import AppState, Integration
from app.services.webhooks import register_integration


async def register(
    state: AppState,
    *,
    integration_type: str,
    webhook_url: str,
    username: str,
    events: list[str],
) -> Integration:
    return await register_integration(
        state,
        integration_type=integration_type,
        webhook_url=webhook_url,
        username=username,
        events=events,
    )
