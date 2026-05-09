"""Identifier helpers — proxy IDs from URLs, alert/webhook ID minting."""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit


def extract_proxy_id(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    if not path:
        return url
    return path.rsplit("/", 1)[-1]


def new_alert_id() -> str:
    return f"alert-{uuid.uuid4().hex[:8]}"


def new_webhook_id() -> str:
    return f"wh-{uuid.uuid4().hex[:8]}"


def new_integration_id() -> str:
    return f"int-{uuid.uuid4().hex[:8]}"
