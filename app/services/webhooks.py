"""Webhook + integration registration and event enqueue.

This module is the *only* path that puts DeliveryJobs onto the queue.
Payloads are built at enqueue time so retries always send identical bytes
matching the alert state at the moment of transition.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.ids import new_integration_id, new_webhook_id
from app.core.state import AppState, DeliveryJob, Integration, WebhookReceiver


async def register_webhook(state: AppState, url: str) -> WebhookReceiver:
    receiver = WebhookReceiver(webhook_id=new_webhook_id(), url=url)
    async with state.webhooks_lock:
        state.webhooks.append(receiver)
    return receiver


async def register_integration(
    state: AppState,
    *,
    integration_type: str,
    webhook_url: str,
    username: str,
    events: list[str],
) -> Integration:
    integration = Integration(
        integration_id=new_integration_id(),
        type=integration_type,  # type: ignore[arg-type]
        webhook_url=webhook_url,
        username=username,
        events=events,
    )
    async with state.integrations_lock:
        state.integrations.append(integration)
    return integration


async def enqueue_event_for_all_receivers(
    state: AppState,
    *,
    event_name: str,
    alert_id: str,
    raw_payload: dict[str, Any],
    slack_builder: Callable[[str], dict[str, Any]],
    discord_builder: Callable[[str], dict[str, Any]],
) -> None:
    """Snapshot all receivers and push a DeliveryJob per receiver.

    The locks on webhooks/integrations are taken briefly to copy the lists,
    then released before queue.put_nowait so we don't block under contention.
    """

    event_key = f"{alert_id}:{event_name.split('.')[-1]}"

    async with state.webhooks_lock:
        webhooks_snapshot = list(state.webhooks)
    async with state.integrations_lock:
        integrations_snapshot = list(state.integrations)

    for wh in webhooks_snapshot:
        await state.delivery_queue.put(
            DeliveryJob(
                event_key=event_key,
                receiver_key=f"webhook:{wh.webhook_id}",
                url=wh.url,
                payload=raw_payload,
            )
        )

    for integ in integrations_snapshot:
        if event_name not in integ.events:
            continue
        payload: dict[str, Any]
        if integ.type == "slack":
            payload = slack_builder(integ.username)
            receiver_prefix = "slack"
        else:  # "discord"
            payload = discord_builder(integ.username)
            receiver_prefix = "discord"
        await state.delivery_queue.put(
            DeliveryJob(
                event_key=event_key,
                receiver_key=f"{receiver_prefix}:{integ.integration_id}",
                url=integ.webhook_url,
                payload=payload,
            )
        )
