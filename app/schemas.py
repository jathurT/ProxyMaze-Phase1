"""Pydantic models for request/response bodies.

Every model accepting JSON has model_config = ConfigDict(extra="ignore") so
unknown fields are silently dropped (per challenge rule §8 "must accept
unknown fields without error").
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_TOLERANT = ConfigDict(extra="ignore")


# --- Health & root -----------------------------------------------------------


class AppInfo(BaseModel):
    name: str
    version: str
    environment: str


class HealthResponse(BaseModel):
    status: str


# --- Config ------------------------------------------------------------------


class ConfigBody(BaseModel):
    model_config = _TOLERANT

    check_interval_seconds: int = Field(ge=1)
    request_timeout_ms: int = Field(ge=1)


# --- Proxies -----------------------------------------------------------------


class ProxiesIn(BaseModel):
    model_config = _TOLERANT

    proxies: list[str]
    replace: bool = False


class ProxyBrief(BaseModel):
    id: str
    url: str
    status: Literal["pending", "up", "down"]


class ProxiesAccepted(BaseModel):
    accepted: int
    proxies: list[ProxyBrief]


class ProxySummary(BaseModel):
    id: str
    url: str
    status: Literal["pending", "up", "down"]
    last_checked_at: str | None
    consecutive_failures: int


class PoolSummary(BaseModel):
    total: int
    up: int
    down: int
    failure_rate: float
    proxies: list[ProxySummary]


class HistoryEntry(BaseModel):
    checked_at: str
    status: Literal["pending", "up", "down"]


class ProxyDossier(BaseModel):
    id: str
    url: str
    status: Literal["pending", "up", "down"]
    last_checked_at: str | None
    consecutive_failures: int
    total_checks: int
    uptime_percentage: float
    history: list[HistoryEntry]


# --- Alerts ------------------------------------------------------------------


class AlertOut(BaseModel):
    alert_id: str
    status: Literal["active", "resolved"]
    failure_rate: float
    total_proxies: int
    failed_proxies: int
    failed_proxy_ids: list[str]
    threshold: float
    fired_at: str
    resolved_at: str | None
    message: str


# --- Webhooks ----------------------------------------------------------------


class WebhookIn(BaseModel):
    model_config = _TOLERANT

    url: str


class WebhookOut(BaseModel):
    webhook_id: str
    url: str


# --- Integrations ------------------------------------------------------------


class IntegrationIn(BaseModel):
    model_config = _TOLERANT

    type: Literal["slack", "discord"]
    webhook_url: str
    username: str = "ProxyMaze"
    events: list[str] = Field(default_factory=lambda: ["alert.fired", "alert.resolved"])


class IntegrationOut(BaseModel):
    integration_id: str
    type: Literal["slack", "discord"]
    webhook_url: str
    username: str
    events: list[str]


# --- Metrics -----------------------------------------------------------------


class MetricsOut(BaseModel):
    total_checks: int
    current_pool_size: int
    active_alerts: int
    total_alerts: int
    webhook_deliveries: int


# --- Generic -----------------------------------------------------------------


class ErrorOut(BaseModel):
    detail: str


# Helper for JSON-payload typing without circular imports.
JsonObj = dict[str, Any]
