"""Timestamp helpers — ISO 8601 UTC strings and Unix epoch seconds."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def utcnow_iso() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_seconds(dt: datetime | None = None) -> int:
    if dt is None:
        dt = utcnow()
    return int(dt.timestamp())


def iso_to_epoch_seconds(iso: str) -> int:
    parsed = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return epoch_seconds(parsed)
