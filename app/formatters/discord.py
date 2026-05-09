"""Discord-formatted alert payload builder.

The payload must contain (per ProxyMaze'26 spec):
- embeds[0].title: non-empty string
- embeds[0].description: non-empty string
- embeds[0].color: integer 0..16777215
- embeds[0].fields: array of {name, value} entries; names must include
  Alert ID, Failure Rate, Failed Proxies, Threshold, Failed IDs
  (case-insensitive substring)
- embeds[0].footer.text: non-empty string
"""

from __future__ import annotations

from typing import Any

COLOR_FIRED = 0xE01E5A
COLOR_RESOLVED = 0x2EB67D
FOOTER_TEXT = "ProxyMaze'26"


def build_fired_payload(username: str, alert: dict[str, Any]) -> dict[str, Any]:
    failed_ids = alert["failed_proxy_ids"]
    return {
        "username": username,
        "embeds": [
            {
                "title": "ProxyMaze alert fired",
                "description": (
                    f"Pool failure rate **{alert['failure_rate'] * 100:.1f}%** "
                    f"breached the **{alert['threshold'] * 100:.0f}%** threshold."
                ),
                "color": COLOR_FIRED,
                "fields": [
                    {"name": "Alert ID", "value": alert["alert_id"], "inline": True},
                    {
                        "name": "Failure Rate",
                        "value": f"{alert['failure_rate'] * 100:.1f}%",
                        "inline": True,
                    },
                    {
                        "name": "Failed Proxies",
                        "value": f"{alert['failed_proxies']}/{alert['total_proxies']}",
                        "inline": True,
                    },
                    {
                        "name": "Threshold",
                        "value": f"{alert['threshold'] * 100:.0f}%",
                        "inline": True,
                    },
                    {
                        "name": "Failed IDs",
                        "value": ", ".join(failed_ids) if failed_ids else "-",
                        "inline": False,
                    },
                ],
                "footer": {"text": FOOTER_TEXT},
                "timestamp": alert["fired_at"],
            }
        ],
    }


def build_resolved_payload(username: str, alert: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": username,
        "embeds": [
            {
                "title": "ProxyMaze alert resolved",
                "description": (
                    f"Alert {alert['alert_id']} resolved. Pool recovered below "
                    f"{alert['threshold'] * 100:.0f}% failure threshold."
                ),
                "color": COLOR_RESOLVED,
                "fields": [
                    {"name": "Alert ID", "value": alert["alert_id"], "inline": True},
                    {
                        "name": "Failure Rate",
                        "value": f"{alert['failure_rate'] * 100:.1f}%",
                        "inline": True,
                    },
                    {
                        "name": "Failed Proxies",
                        "value": f"{alert['failed_proxies']}/{alert['total_proxies']}",
                        "inline": True,
                    },
                    {
                        "name": "Threshold",
                        "value": f"{alert['threshold'] * 100:.0f}%",
                        "inline": True,
                    },
                    {
                        "name": "Failed IDs",
                        "value": ", ".join(alert["failed_proxy_ids"])
                        if alert["failed_proxy_ids"]
                        else "-",
                        "inline": False,
                    },
                ],
                "footer": {"text": FOOTER_TEXT},
                "timestamp": alert.get("resolved_at") or alert["fired_at"],
            }
        ],
    }
