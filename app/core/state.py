"""In-memory application state.

A single AppState instance holds all runtime data. Every domain has its own
asyncio.Lock so endpoint and worker code can mutate concurrently without
torn reads (e.g. GET /proxies seeing a half-updated probe round).
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

ProxyStatus = Literal["pending", "up", "down"]
AlertStatus = Literal["active", "resolved"]

DEFAULT_CHECK_INTERVAL_SECONDS = 15
DEFAULT_REQUEST_TIMEOUT_MS = 3000
ALERT_THRESHOLD = 0.20
HISTORY_MAX = 500


@dataclass
class RuntimeConfig:
    check_interval_seconds: int = DEFAULT_CHECK_INTERVAL_SECONDS
    request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS


@dataclass
class CheckEvent:
    checked_at: str
    status: ProxyStatus


@dataclass
class ProxyRecord:
    id: str
    url: str
    status: ProxyStatus = "pending"
    last_checked_at: str | None = None
    consecutive_failures: int = 0
    total_checks: int = 0
    up_checks: int = 0
    history: deque[CheckEvent] = field(
        default_factory=lambda: deque(maxlen=HISTORY_MAX)
    )


@dataclass
class AlertRecord:
    alert_id: str
    status: AlertStatus
    failure_rate: float
    total_proxies: int
    failed_proxies: int
    failed_proxy_ids: list[str]
    threshold: float
    fired_at: str
    resolved_at: str | None
    message: str


@dataclass
class WebhookReceiver:
    webhook_id: str
    url: str


@dataclass
class Integration:
    integration_id: str
    type: Literal["slack", "discord"]
    webhook_url: str
    username: str
    events: list[str]


@dataclass
class DeliveryJob:
    event_key: str  # e.g. "alert-abc:fired"
    receiver_key: str  # e.g. "webhook:wh-1" / "slack:int-1" / "discord:int-1"
    url: str
    payload: dict[str, Any]
    attempt: int = 0


@dataclass
class MetricsCounters:
    total_checks: int = 0
    webhook_deliveries: int = 0


class AppState:
    """Singleton container for all runtime state."""

    def __init__(self) -> None:
        self.config: RuntimeConfig = RuntimeConfig()
        self.config_lock: asyncio.Lock = asyncio.Lock()
        self.config_changed: asyncio.Event = asyncio.Event()

        self.pool: dict[str, ProxyRecord] = {}
        self.pool_lock: asyncio.Lock = asyncio.Lock()

        self.alerts: list[AlertRecord] = []
        self.active_alert_id: str | None = None
        self.alerts_lock: asyncio.Lock = asyncio.Lock()

        self.webhooks: list[WebhookReceiver] = []
        self.webhooks_lock: asyncio.Lock = asyncio.Lock()

        self.integrations: list[Integration] = []
        self.integrations_lock: asyncio.Lock = asyncio.Lock()

        self.delivery_queue: asyncio.Queue[DeliveryJob] = asyncio.Queue()
        self.delivered: set[tuple[str, str]] = set()
        self.delivered_lock: asyncio.Lock = asyncio.Lock()

        self.metrics: MetricsCounters = MetricsCounters()
        self.metrics_lock: asyncio.Lock = asyncio.Lock()

        self.http_client: httpx.AsyncClient | None = None
        self.background_tasks: list[asyncio.Task[None]] = []
        self.retry_tasks: set[asyncio.Task[None]] = set()
        self.shutting_down: bool = False


_state_instance: AppState | None = None


def get_state() -> AppState:
    """Lazily-initialised singleton. AppState is created on first access so
    asyncio primitives bind to the running event loop."""
    global _state_instance
    if _state_instance is None:
        _state_instance = AppState()
    return _state_instance


def reset_state_for_tests() -> None:
    """Test-only: drop the singleton so each test starts clean."""
    global _state_instance
    _state_instance = None
