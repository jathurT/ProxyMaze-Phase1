"""Slack-formatted alert payload builder.

The payload must contain (per ProxyMaze'26 spec):
- username: non-empty string
- text: non-empty string
- attachments[0].color: hex string "#RRGGBB"
- attachments[0].fields: array of {title, value} entries; titles must include
  Alert ID, Failure Rate, Failed Proxies, Threshold, Failed IDs, Fired At
  (case-insensitive substring)
- attachments[0].footer: non-empty string
- attachments[0].ts: integer Unix epoch seconds
"""

from __future__ import annotations

from typing import Any

from app.core.time import iso_to_epoch_seconds

COLOR_FIRED = "#E01E5A"
COLOR_RESOLVED = "#2EB67D"
FOOTER = "ProxyMaze'26"


def build_fired_payload(username: str, alert: dict[str, Any]) -> dict[str, Any]:
    failed_ids = alert["failed_proxy_ids"]
    return {
        "username": username,
        "text": (
            f"Proxy pool failure rate {alert['failure_rate'] * 100:.1f}% "
            f"breached threshold {alert['threshold'] * 100:.0f}%."
        ),
        "attachments": [
            {
                "color": COLOR_FIRED,
                "fields": [
                    {"title": "Alert ID", "value": alert["alert_id"], "short": True},
                    {
                        "title": "Failure Rate",
                        "value": f"{alert['failure_rate'] * 100:.1f}%",
                        "short": True,
                    },
                    {
                        "title": "Failed Proxies",
                        "value": f"{alert['failed_proxies']}/{alert['total_proxies']}",
                        "short": True,
                    },
                    {
                        "title": "Threshold",
                        "value": f"{alert['threshold'] * 100:.0f}%",
                        "short": True,
                    },
                    {
                        "title": "Failed IDs",
                        "value": ", ".join(failed_ids) if failed_ids else "-",
                        "short": False,
                    },
                    {"title": "Fired At", "value": alert["fired_at"], "short": True},
                ],
                "footer": FOOTER,
                "ts": iso_to_epoch_seconds(alert["fired_at"]),
            }
        ],
    }


def build_resolved_payload(username: str, alert: dict[str, Any]) -> dict[str, Any]:
    fired_at = alert["fired_at"]
    resolved_at = alert.get("resolved_at") or fired_at
    return {
        "username": username,
        "text": f"Proxy pool recovered. Alert {alert['alert_id']} resolved.",
        "attachments": [
            {
                "color": COLOR_RESOLVED,
                "fields": [
                    {"title": "Alert ID", "value": alert["alert_id"], "short": True},
                    {
                        "title": "Failure Rate",
                        "value": f"{alert['failure_rate'] * 100:.1f}%",
                        "short": True,
                    },
                    {
                        "title": "Failed Proxies",
                        "value": f"{alert['failed_proxies']}/{alert['total_proxies']}",
                        "short": True,
                    },
                    {
                        "title": "Threshold",
                        "value": f"{alert['threshold'] * 100:.0f}%",
                        "short": True,
                    },
                    {
                        "title": "Failed IDs",
                        "value": ", ".join(alert["failed_proxy_ids"])
                        if alert["failed_proxy_ids"]
                        else "-",
                        "short": False,
                    },
                    {"title": "Fired At", "value": fired_at, "short": True},
                    {"title": "Resolved At", "value": resolved_at, "short": True},
                ],
                "footer": FOOTER,
                "ts": iso_to_epoch_seconds(resolved_at),
            }
        ],
    }
