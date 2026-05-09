"""HTTP layer for ProxyMaze.

Endpoints stay thin: validate request body via Pydantic, delegate to a
service in app.services, return a Pydantic response model. All write
endpoints that touch the pool delegate to services.proxies which holds
pool_lock and runs the alert state machine atomically.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import state_dependency
from app.config import Settings, get_settings
from app.core.state import ALERT_THRESHOLD, AppState
from app.schemas import (
    AlertOut,
    AppInfo,
    ConfigBody,
    HealthResponse,
    HistoryEntry,
    IntegrationIn,
    IntegrationOut,
    MetricsOut,
    PoolSummary,
    ProxiesAccepted,
    ProxiesIn,
    ProxyBrief,
    ProxyDossier,
    ProxySummary,
    WebhookIn,
    WebhookOut,
)
from app.services import metrics as metrics_service
from app.services import proxies as proxies_service
from app.services import webhooks as webhook_service

router = APIRouter()

StateDep = Annotated[AppState, Depends(state_dependency)]


# --- Root + health -----------------------------------------------------------


@router.get("/", response_model=AppInfo, summary="Get API metadata")
def read_root(settings: Annotated[Settings, Depends(get_settings)]) -> AppInfo:
    return AppInfo(
        name=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )


@router.get("/health", response_model=HealthResponse, summary="Service health")
def read_health() -> HealthResponse:
    return HealthResponse(status="ok")


# --- Config ------------------------------------------------------------------


@router.post("/config", response_model=ConfigBody, summary="Update runtime config")
async def post_config(body: ConfigBody, state: StateDep) -> ConfigBody:
    async with state.config_lock:
        state.config.check_interval_seconds = body.check_interval_seconds
        state.config.request_timeout_ms = body.request_timeout_ms
    state.config_changed.set()  # wake the monitor loop immediately
    return body


@router.get("/config", response_model=ConfigBody, summary="Read current config")
async def get_config(state: StateDep) -> ConfigBody:
    async with state.config_lock:
        return ConfigBody(
            check_interval_seconds=state.config.check_interval_seconds,
            request_timeout_ms=state.config.request_timeout_ms,
        )


# --- Proxies -----------------------------------------------------------------


@router.post(
    "/proxies",
    response_model=ProxiesAccepted,
    status_code=status.HTTP_201_CREATED,
    summary="Add proxies to the pool",
)
async def post_proxies(body: ProxiesIn, state: StateDep) -> ProxiesAccepted:
    accepted = await proxies_service.add_or_replace(
        state, proxies=body.proxies, replace=body.replace
    )
    return ProxiesAccepted(
        accepted=len(accepted),
        proxies=[
            ProxyBrief(id=r.id, url=r.url, status=r.status) for r in accepted
        ],
    )


@router.get("/proxies", response_model=PoolSummary, summary="Pool summary")
async def get_proxies(state: StateDep) -> PoolSummary:
    async with state.pool_lock:
        proxies = list(state.pool.values())
        total = len(proxies)
        up = sum(1 for p in proxies if p.status == "up")
        down = sum(1 for p in proxies if p.status == "down")
        rate = (down / total) if total else 0.0
        return PoolSummary(
            total=total,
            up=up,
            down=down,
            failure_rate=rate,
            proxies=[
                ProxySummary(
                    id=p.id,
                    url=p.url,
                    status=p.status,
                    last_checked_at=p.last_checked_at,
                    consecutive_failures=p.consecutive_failures,
                )
                for p in proxies
            ],
        )


@router.delete(
    "/proxies",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear the pool",
)
async def delete_proxies(state: StateDep) -> Response:
    await proxies_service.clear_pool(state)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/proxies/{proxy_id}",
    response_model=ProxyDossier,
    summary="Single proxy dossier",
)
async def get_proxy(proxy_id: str, state: StateDep) -> ProxyDossier:
    async with state.pool_lock:
        record = state.pool.get(proxy_id)
        if record is None:
            raise HTTPException(status_code=404, detail="proxy not found")
        uptime = (
            (record.up_checks / record.total_checks * 100.0)
            if record.total_checks
            else 0.0
        )
        return ProxyDossier(
            id=record.id,
            url=record.url,
            status=record.status,
            last_checked_at=record.last_checked_at,
            consecutive_failures=record.consecutive_failures,
            total_checks=record.total_checks,
            uptime_percentage=round(uptime, 2),
            history=[
                HistoryEntry(checked_at=h.checked_at, status=h.status)
                for h in record.history
            ],
        )


@router.get(
    "/proxies/{proxy_id}/history",
    response_model=list[HistoryEntry],
    summary="Per-proxy check history",
)
async def get_proxy_history(proxy_id: str, state: StateDep) -> list[HistoryEntry]:
    async with state.pool_lock:
        record = state.pool.get(proxy_id)
        if record is None:
            raise HTTPException(status_code=404, detail="proxy not found")
        return [
            HistoryEntry(checked_at=h.checked_at, status=h.status)
            for h in record.history
        ]


# --- Alerts ------------------------------------------------------------------


@router.get("/alerts", response_model=list[AlertOut], summary="Alert archive")
async def get_alerts(state: StateDep) -> list[AlertOut]:
    async with state.alerts_lock:
        return [AlertOut(**asdict(a)) for a in state.alerts]


# --- Webhooks ----------------------------------------------------------------


@router.post(
    "/webhooks",
    response_model=WebhookOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a webhook receiver",
)
async def post_webhook(body: WebhookIn, state: StateDep) -> WebhookOut:
    receiver = await webhook_service.register_webhook(state, body.url)
    return WebhookOut(webhook_id=receiver.webhook_id, url=receiver.url)


# --- Integrations ------------------------------------------------------------


@router.post(
    "/integrations",
    response_model=IntegrationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a Slack/Discord integration",
)
async def post_integration(body: IntegrationIn, state: StateDep) -> IntegrationOut:
    integration = await webhook_service.register_integration(
        state,
        integration_type=body.type,
        webhook_url=body.webhook_url,
        username=body.username,
        events=body.events,
    )
    return IntegrationOut(
        integration_id=integration.integration_id,
        type=integration.type,
        webhook_url=integration.webhook_url,
        username=integration.username,
        events=integration.events,
    )


# --- Metrics -----------------------------------------------------------------


@router.get("/metrics", response_model=MetricsOut, summary="Operational metrics")
async def get_metrics(state: StateDep) -> MetricsOut:
    snap = await metrics_service.snapshot(state)
    return MetricsOut(**snap)


__all__ = ["router", "ALERT_THRESHOLD", "read_root", "read_health"]
