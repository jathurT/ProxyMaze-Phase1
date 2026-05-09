"""Alert lifecycle state machine.

This is the only place that creates, mutates, or resolves AlertRecord
objects. ``evaluate`` is called after every probe round and after every
pool mutation. It guarantees:

- One active alert at a time.
- alert_id stable across a continuous breach.
- failed_proxy_ids always equal to the current down set.
- A fresh breach after resolution mints a brand-new alert_id.
- Webhook events enqueued in the canonical order
  fired(prev) -> resolved(prev) -> fired(new).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.core.ids import new_alert_id
from app.core.state import (
    ALERT_THRESHOLD,
    AlertRecord,
    AppState,
)
from app.core.time import utcnow_iso
from app.formatters import discord as discord_fmt
from app.formatters import slack as slack_fmt
from app.services.webhooks import enqueue_event_for_all_receivers

ALERT_MESSAGE = "Proxy pool failure rate exceeded threshold"


def _alert_to_dict(record: AlertRecord) -> dict[str, Any]:
    return asdict(record)


async def evaluate(state: AppState) -> None:
    """Run the state machine. Caller must hold pool_lock for the duration of
    the round; this function takes alerts_lock internally."""

    async with state.alerts_lock:
        down_ids = sorted(p.id for p in state.pool.values() if p.status == "down")
        total = len(state.pool)
        rate = (len(down_ids) / total) if total else 0.0
        active_id = state.active_alert_id

        # --- Case 1: no active alert + breach -> mint new alert -------------
        if active_id is None and rate >= ALERT_THRESHOLD and total > 0:
            new_alert = AlertRecord(
                alert_id=new_alert_id(),
                status="active",
                failure_rate=rate,
                total_proxies=total,
                failed_proxies=len(down_ids),
                failed_proxy_ids=down_ids,
                threshold=ALERT_THRESHOLD,
                fired_at=utcnow_iso(),
                resolved_at=None,
                message=ALERT_MESSAGE,
            )
            state.alerts.append(new_alert)
            state.active_alert_id = new_alert.alert_id
            await _emit_fired(state, new_alert)
            return

        # --- Case 2: active alert + below threshold -> resolve --------------
        if active_id is not None and rate < ALERT_THRESHOLD:
            existing = _find_alert(state, active_id)
            if existing is None:  # defensive; should never happen
                state.active_alert_id = None
                return
            existing.status = "resolved"
            existing.resolved_at = utcnow_iso()
            existing.failure_rate = rate
            existing.failed_proxies = len(down_ids)
            existing.failed_proxy_ids = down_ids
            existing.total_proxies = total
            state.active_alert_id = None
            await _emit_resolved(state, existing)
            return

        # --- Case 3: active alert + still breaching -> in-place update ------
        if active_id is not None and rate >= ALERT_THRESHOLD:
            existing = _find_alert(state, active_id)
            if existing is None:
                state.active_alert_id = None
                return
            existing.failure_rate = rate
            existing.total_proxies = total
            existing.failed_proxies = len(down_ids)
            existing.failed_proxy_ids = down_ids
            return

        # --- Case 4: idle, no breach -> nothing -----------------------------
        return


def _find_alert(state: AppState, alert_id: str) -> AlertRecord | None:
    for a in reversed(state.alerts):
        if a.alert_id == alert_id:
            return a
    return None


async def _emit_fired(state: AppState, alert: AlertRecord) -> None:
    snapshot = _alert_to_dict(alert)
    raw_payload = {
        "event": "alert.fired",
        "alert_id": alert.alert_id,
        "fired_at": alert.fired_at,
        "failure_rate": alert.failure_rate,
        "total_proxies": alert.total_proxies,
        "failed_proxies": alert.failed_proxies,
        "failed_proxy_ids": list(alert.failed_proxy_ids),
        "threshold": alert.threshold,
        "message": alert.message,
    }
    await enqueue_event_for_all_receivers(
        state,
        event_name="alert.fired",
        alert_id=alert.alert_id,
        raw_payload=raw_payload,
        slack_builder=lambda u: slack_fmt.build_fired_payload(u, snapshot),
        discord_builder=lambda u: discord_fmt.build_fired_payload(u, snapshot),
    )


async def _emit_resolved(state: AppState, alert: AlertRecord) -> None:
    snapshot = _alert_to_dict(alert)
    raw_payload = {
        "event": "alert.resolved",
        "alert_id": alert.alert_id,
        "resolved_at": alert.resolved_at,
    }
    await enqueue_event_for_all_receivers(
        state,
        event_name="alert.resolved",
        alert_id=alert.alert_id,
        raw_payload=raw_payload,
        slack_builder=lambda u: slack_fmt.build_resolved_payload(u, snapshot),
        discord_builder=lambda u: discord_fmt.build_resolved_payload(u, snapshot),
    )
