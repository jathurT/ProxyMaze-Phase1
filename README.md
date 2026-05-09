# ProxyMaze

ProxyMaze is a FastAPI backend for real-time proxy pool monitoring. It keeps an
in-memory list of proxy check URLs, probes them continuously in the background,
tracks per-proxy status/history, fires alerts when the pool failure rate crosses
the configured threshold, and delivers alert events to raw webhooks plus Slack
or Discord integrations.

The service is designed as a single-process async application. Its background
monitor and delivery workers start from the FastAPI lifespan hook, so the API and
workers share one in-memory state container.

## Tech Stack

- Python 3.12
- FastAPI and Pydantic v2
- `pydantic-settings` for environment-based settings
- `httpx` for async proxy probes and webhook delivery
- `asyncio` background tasks, queues, locks, and semaphores
- `uv` for dependency management and command execution
- Docker multi-stage image based on `python:3.12-slim`

## Project Structure

```text
ProxyMaze/
+-- app/
|   +-- main.py                  # FastAPI app factory and lifespan workers
|   +-- config.py                # PROXYMAZE_* settings
|   +-- schemas.py               # Pydantic request/response models
|   +-- api/
|   |   +-- routes.py            # HTTP endpoints
|   |   +-- deps.py              # FastAPI dependencies
|   +-- core/
|   |   +-- state.py             # In-memory state, locks, records, constants
|   |   +-- ids.py               # Proxy, alert, webhook, integration IDs
|   |   +-- time.py              # UTC timestamp helpers
|   +-- services/
|   |   +-- proxies.py           # Add, replace, and clear proxy pool
|   |   +-- alerts.py            # Alert lifecycle state machine
|   |   +-- webhooks.py          # Receiver registration and event enqueue
|   |   +-- integrations.py      # Slack/Discord integration wrapper
|   |   +-- metrics.py           # Runtime metrics snapshots
|   +-- workers/
|   |   +-- monitor.py           # Continuous proxy probe loop
|   |   +-- delivery.py          # Webhook/integration delivery with retries
|   +-- formatters/
|       +-- slack.py             # Slack alert payloads
|       +-- discord.py           # Discord alert payloads
+-- tests/
|   +-- test_main.py             # Basic API registration and response tests
+-- Dockerfile                   # Runtime container image
+-- pyproject.toml               # Project metadata, dependencies, tool config
+-- uv.lock                      # Locked dependency graph
```

## Features

- **Proxy pool management**: add proxies, replace the whole pool, read a pool
  summary, inspect one proxy, read per-proxy history, and clear the pool.
- **Background health checks**: the monitor probes all registered proxy URLs on
  a configurable interval without requiring endpoint calls to trigger checks.
- **Runtime config**: update check interval and request timeout through the API.
- **Alert lifecycle**: one active alert at a time, alert archive retained after
  resolution, and a new `alert_id` for each fresh breach after recovery.
- **Webhook delivery**: alert events are queued and delivered as JSON payloads.
- **Slack and Discord integrations**: formatted alert payloads for chat
  receivers using the same alert lifecycle events.
- **Operational metrics**: total checks, current pool size, active/total alerts,
  and successful webhook deliveries.
- **Docker support**: build and run the API as a local container.

## Runtime Defaults

- Default check interval: `15` seconds.
- Default request timeout: `3000` ms.
- Alert threshold: `20%` failure rate.
- Failure rate formula: `down / total`.
- Empty proxy pool failure rate: `0.0`.
- Proxy status is `up` only when the probe returns HTTP `2xx` before timeout.
  Timeouts, transport errors, and non-`2xx` responses are treated as `down`.
- State is in-memory and resets when the process or container restarts.
- Request bodies tolerate unknown JSON fields where request models are used.

## Requirements

Install these before running the project locally:

- Python `3.12`
- `uv`
- Docker, only if you want to build or run the container image

## Run Locally

From the repository root:

```bash
UV_CACHE_DIR=.uv-cache uv sync
```

Run the development server with reload:

```bash
UV_CACHE_DIR=.uv-cache uv run fastapi dev app/main.py
```

Run a production-style local server:

```bash
UV_CACHE_DIR=.uv-cache uv run fastapi run app/main.py
```

FastAPI serves the app on `http://127.0.0.1:8000` by default. Useful URLs:

- API health: `http://127.0.0.1:8000/health`
- API docs: `http://127.0.0.1:8000/docs`
- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`

Check the service:

```bash
curl -sS http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

## Configuration

Application metadata can be overridden with `PROXYMAZE_*` environment variables:

```bash
PROXYMAZE_APP_NAME="ProxyMaze API"
PROXYMAZE_APP_VERSION="0.1.0"
PROXYMAZE_ENVIRONMENT="development"
```

Runtime monitoring settings are managed through `GET /config` and
`POST /config`.

## API Guide

The examples below assume:

```bash
export BASE_URL=http://127.0.0.1:8000
```

### Root and Health

Read API metadata:

```bash
curl -sS "$BASE_URL/"
```

Check service health:

```bash
curl -sS "$BASE_URL/health"
```

### Runtime Config

Read current config:

```bash
curl -sS "$BASE_URL/config"
```

Update monitor cadence and request timeout:

```bash
curl -sS -X POST "$BASE_URL/config" \
  -H "Content-Type: application/json" \
  -d '{"check_interval_seconds":3,"request_timeout_ms":1500}'
```

### Proxy Pool

Add or replace proxies:

```bash
curl -sS -X POST "$BASE_URL/proxies" \
  -H "Content-Type: application/json" \
  -d '{
    "replace": true,
    "proxies": [
      "https://httpbin.org/status/200/px-good",
      "https://httpbin.org/status/500/px-bad"
    ]
  }'
```

`replace: true` clears the existing pool first. With `replace: false`, existing
proxy IDs keep their history and only refresh the stored URL.

Proxy IDs are derived from the last URL path segment. For example,
`https://httpbin.org/status/200/px-good` becomes `px-good`.

Read the pool summary:

```bash
curl -sS "$BASE_URL/proxies"
```

Read one proxy dossier:

```bash
curl -sS "$BASE_URL/proxies/px-good"
```

Read one proxy's check history:

```bash
curl -sS "$BASE_URL/proxies/px-good/history"
```

Clear the proxy pool:

```bash
curl -sS -X DELETE "$BASE_URL/proxies" -i
```

### Alerts

Read all active and resolved alerts:

```bash
curl -sS "$BASE_URL/alerts"
```

An alert fires when the current failure rate is greater than or equal to `20%`.
It resolves when the failure rate drops below `20%`. During a continuous breach,
the active alert keeps the same `alert_id` while its failure details update.

### Webhooks

Register a raw JSON webhook receiver:

```bash
curl -sS -X POST "$BASE_URL/webhooks" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/proxymaze-webhook"}'
```

Registered webhooks receive alert transition events such as `alert.fired` and
`alert.resolved`.

### Slack and Discord Integrations

Register a Slack integration:

```bash
curl -sS -X POST "$BASE_URL/integrations" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "slack",
    "webhook_url": "https://hooks.slack.com/services/XXX/YYY/ZZZ",
    "username": "ProxyMaze",
    "events": ["alert.fired", "alert.resolved"]
  }'
```

Register a Discord integration:

```bash
curl -sS -X POST "$BASE_URL/integrations" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "discord",
    "webhook_url": "https://discord.com/api/webhooks/XXX/YYY",
    "username": "ProxyMaze",
    "events": ["alert.fired", "alert.resolved"]
  }'
```

Slack payloads include `username`, `text`, attachment color, alert fields,
footer, and Unix timestamp. Discord payloads include `username`, embed title,
description, integer color, alert fields, footer text, and timestamp.

### Metrics

Read operational counters:

```bash
curl -sS "$BASE_URL/metrics"
```

The response includes:

- `total_checks`
- `current_pool_size`
- `active_alerts`
- `total_alerts`
- `webhook_deliveries`

## Public Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | API metadata |
| `GET` | `/health` | Service health |
| `GET` | `/config` | Read runtime config |
| `POST` | `/config` | Update runtime config |
| `POST` | `/proxies` | Add or replace proxies |
| `GET` | `/proxies` | Read pool summary |
| `DELETE` | `/proxies` | Clear pool |
| `GET` | `/proxies/{proxy_id}` | Read one proxy dossier |
| `GET` | `/proxies/{proxy_id}/history` | Read one proxy's check history |
| `GET` | `/alerts` | Read alert archive |
| `POST` | `/webhooks` | Register raw webhook receiver |
| `POST` | `/integrations` | Register Slack or Discord integration |
| `GET` | `/metrics` | Read operational metrics |

## Alert and Delivery Behavior

The monitor loop snapshots the pool, probes URLs concurrently, applies results
under the pool lock, and then evaluates the alert state machine. Alert events
are enqueued when alerts fire or resolve.

Delivery workers consume the queue and POST JSON to registered receivers. They:

- Send `Content-Type: application/json`.
- Retry transport errors and HTTP `500`, `502`, `503`, and `504`.
- Use exponential backoff capped at `30` seconds.
- Track successful delivery per event/receiver pair.
- Treat non-retryable HTTP errors, such as most `4xx` responses, as final.

## Testing and Checks

Run tests:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest
```

Run linting:

```bash
UV_CACHE_DIR=.uv-cache uv run ruff check .
```

Run strict type checking:

```bash
UV_CACHE_DIR=.uv-cache uv run mypy app tests
```

## Docker

Build the local image:

```bash
docker build -t proxymaze:dev .
```

Run the container:

```bash
docker run --rm -p 8080:8080 proxymaze:dev
```

Check the containerized service:

```bash
curl -sS http://127.0.0.1:8080/health
```

The image runs `uvicorn app.main:app` on `0.0.0.0:${PORT}`. `PORT` defaults to
`8080`.

## Deployment

ProxyMaze can be deployed as a single-container service. For Cloud Run, keep
these settings aligned with the app's in-memory and background-worker design:

- Minimum instances: `1`
- Maximum instances: `1`
- CPU always allocated
- Container port: `8080`
- Public HTTP access if the evaluator or external callers need to reach it

For the detailed local deployment walkthrough, see
[DEPLOYMENT.md](DEPLOYMENT.md) if that file is present in your workspace.

## Quick Smoke Test

After starting the local server, run:

```bash
export BASE_URL=http://127.0.0.1:8000

curl -sS "$BASE_URL/health"

curl -sS -X POST "$BASE_URL/config" \
  -H "Content-Type: application/json" \
  -d '{"check_interval_seconds":3,"request_timeout_ms":1500}'

curl -sS -X POST "$BASE_URL/proxies" \
  -H "Content-Type: application/json" \
  -d '{
    "replace": true,
    "proxies": [
      "https://httpbin.org/status/200/px-good",
      "https://httpbin.org/status/500/px-bad"
    ]
  }'

sleep 5
curl -sS "$BASE_URL/proxies"
curl -sS "$BASE_URL/alerts"
curl -sS "$BASE_URL/metrics"
```

With one good URL and one bad URL, the failure rate should reach `50%`, which is
above the `20%` alert threshold.
