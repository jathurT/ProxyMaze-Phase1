# CLAUDE.md — ProxyMaze'26

This file is the canonical context for Claude Code working on this repository.
Read it before making changes.

## Mission

ProxyMaze is the real-time proxy intelligence service Torch Labs (Colombo) is
building after their Tuesday-night incident: 96 of 220 residential proxies died
silently while their monitoring script was offline, a Korean client cancelled the
next day, and Sachin (CTO) put one phrase on the whiteboard:

> "It must wake somebody up before a client emails us."

We are building the watchtower. The challenge spec lives at
[ProxyMaze26_Challenge.md](../ProxyMaze26_Challenge.md). Scoring: 250 core +
20 bonus, passing 186, evaluated as a black box against the HTTP API.

## Tech stack

- Python 3.12, FastAPI, Pydantic v2
- httpx (async HTTP client) for both probes and webhook delivery
- asyncio for the background monitor and delivery workers
- pydantic-settings for runtime configuration
- `uv` for dependency management and execution
- Docker (multi-stage, python:3.12-slim) for packaging
- GCP Cloud Run for hosting (min-instances=1, CPU always-allocated)
- All state is in-memory; there is no database

## Repository layout

```
ProxyMaze/
├── CLAUDE.md                     <- you are here
├── README.md                     <- grade-5 friendly explainer
├── DEPLOYMENT.md                 <- Docker Hub + GCP Cloud Run guide
├── Dockerfile, .dockerignore
├── pyproject.toml, uv.lock
├── app/
│   ├── main.py                   FastAPI factory + lifespan
│   ├── config.py                 pydantic-settings
│   ├── schemas.py                request/response Pydantic models
│   ├── api/
│   │   ├── routes.py             all 13 endpoints (thin layer)
│   │   └── deps.py               FastAPI Depends() providers
│   ├── core/
│   │   ├── state.py              AppState singleton + dataclasses
│   │   ├── ids.py                proxy/alert/webhook id helpers
│   │   └── time.py               ISO 8601 / epoch seconds helpers
│   ├── services/
│   │   ├── proxies.py            pool add/replace/clear
│   │   ├── alerts.py             alert lifecycle state machine
│   │   ├── webhooks.py           register + enqueue (the only path that
│   │   │                         pushes onto delivery_queue)
│   │   ├── integrations.py       slack/discord registration
│   │   └── metrics.py            metrics snapshot
│   ├── workers/
│   │   ├── monitor.py            continuous probe loop
│   │   └── delivery.py           webhook/integration delivery + retry
│   └── formatters/
│       ├── slack.py              Slack-shaped payload builder
│       └── discord.py            Discord-shaped payload builder
└── tests/
    └── test_main.py
```

## HTTP contract (13 endpoints)

| # | Method & Path                  | Purpose                                                           |
|---|--------------------------------|-------------------------------------------------------------------|
| 1 | `GET /health`                  | `{"status": "ok"}` (proof of life)                                |
| 2 | `POST /config`                 | Update `check_interval_seconds`, `request_timeout_ms`             |
| 3 | `GET /config`                  | Read current config                                               |
| 4 | `POST /proxies`                | Add or replace pool. `replace=true` clears first.                 |
| 5 | `GET /proxies`                 | Pool summary: `total`, `up`, `down`, `failure_rate`, per-proxy    |
| 6 | `GET /proxies/{id}`            | Per-proxy dossier (404 if unknown)                                |
| 7 | `GET /proxies/{id}/history`    | JSON array of `{checked_at, status}` (404 if unknown)             |
| 8 | `DELETE /proxies`              | Empty pool (preserves alert archive). Returns 204                 |
| 9 | `GET /alerts`                  | All alerts active + resolved                                      |
|10 | `POST /webhooks`               | Register a JSON receiver. Returns `{webhook_id, url}`             |
|11 | `POST /integrations`           | Register Slack or Discord integration                             |
|12 | `GET /metrics`                 | Operational counters                                              |

All JSON request bodies tolerate unknown fields (`extra="ignore"` on every
Pydantic model). Bodies are rejected only on genuinely malformed input.

## Behavioural rules

- **Threshold**: 0.20. Alert fires when `failure_rate >= 0.20`. Resolves when
  `failure_rate < 0.20`.
- **Failure rate**: `down / total`. Empty pool ⇒ 0.0; never fires.
- **Probe classification**: 2xx within `request_timeout_ms` ⇒ `up`. Timeout,
  connection error, refused, 5xx ⇒ `down`. 3xx (after redirect resolution) and
  4xx ⇒ `down` (only 2xx is explicitly "up" per spec).
- **Background monitoring**: runs on the cadence set by `check_interval_seconds`.
  Endpoints never trigger probes — they only read state.
- **Alert lifecycle**:
  - At most one alert is `active` at any time.
  - During a continuous breach, the same `alert_id` persists; only
    `failed_proxy_ids` / `failed_proxies` / `failure_rate` / `total_proxies`
    update in place.
  - After resolution, the alert stays in the archive; a fresh breach mints a
    brand-new `alert_id`.
  - Webhook event order across a re-breach: `fired(prev) → resolved(prev) →
    fired(new)`.
- **Cross-endpoint consistency**: `failed_proxy_ids` always equals the current
  down set; `GET /proxies`, `GET /alerts`, and webhook payloads agree on
  `total_proxies`, `failed_proxies`, `failed_proxy_ids`, `threshold` during a
  breach.
- **Webhook delivery**:
  - `Content-Type: application/json` on every POST
  - Within 60s of the underlying state transition
  - Retry on 500/502/503/504 and on transport errors with exponential backoff
    (capped 30s); unlimited retries until success
  - Exactly one successful delivery per `(event, receiver)` pair
- **Proxy ID**: `urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]`. The same
  ID appears across `POST /proxies`, `GET /proxies`, `GET /proxies/{id}`,
  `GET /proxies/{id}/history`, `failed_proxy_ids`, and webhook payloads.

## Slack payload (bonus +10)

`POST` to the integration's `webhook_url` with:

- `username` (non-empty)
- `text` (non-empty)
- `attachments[0].color` ("#RRGGBB" hex)
- `attachments[0].fields[]` with titles including (case-insensitive substring):
  *Alert ID*, *Failure Rate*, *Failed Proxies*, *Threshold*, *Failed IDs*,
  *Fired At*
- `attachments[0].footer` (non-empty)
- `attachments[0].ts` (integer Unix epoch seconds — not float, not string)

Built in [app/formatters/slack.py](app/formatters/slack.py).

## Discord payload (bonus +10)

`POST` to the integration's `webhook_url` with:

- `embeds[0].title`, `embeds[0].description` (non-empty)
- `embeds[0].color` (integer 0..16777215)
- `embeds[0].fields[]` with names including (case-insensitive substring):
  *Alert ID*, *Failure Rate*, *Failed Proxies*, *Threshold*, *Failed IDs*
- `embeds[0].footer.text` (non-empty)

Built in [app/formatters/discord.py](app/formatters/discord.py).

## Concurrency model

Single FastAPI process with two background asyncio tasks (started in
`lifespan` in [app/main.py](app/main.py)):

1. `monitor` — every `check_interval_seconds`: snapshot pool, probe all
   concurrently (asyncio.Semaphore=50), then under `pool_lock` apply results
   and call `alerts.evaluate()`.
2. `delivery` — N=4 consumer tasks pull from `state.delivery_queue` and POST
   to receivers with retry/backoff and exactly-once tracking.

**Lock ordering** (always taken in this order; never reversed to avoid deadlock):

```
pool_lock -> alerts_lock -> webhooks_lock | integrations_lock
```

`alerts.evaluate(state)` does not acquire `pool_lock`; the caller must hold it.
This is documented in the function docstring.

## Run / test / check / build commands

```bash
# Install/sync dependencies
UV_CACHE_DIR=.uv-cache uv sync

# Run dev server with reload
UV_CACHE_DIR=.uv-cache uv run fastapi dev app/main.py

# Run production-style local server
UV_CACHE_DIR=.uv-cache uv run fastapi run app/main.py

# Tests
UV_CACHE_DIR=.uv-cache uv run pytest

# Lint
UV_CACHE_DIR=.uv-cache uv run ruff check .

# Type-check (strict)
UV_CACHE_DIR=.uv-cache uv run mypy app tests

# Build container image
docker build -t proxymaze:dev .

# Run container locally
docker run --rm -p 8080:8080 proxymaze:dev
```

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md). Short version: build → push to Docker Hub →
deploy on GCP Cloud Run with min-instances=1 and CPU always-allocated.

## Conventions for AI changes

- Keep `extra="ignore"` on every request-body schema.
- Never run probes from endpoint handlers — only the monitor task probes.
- Preserve the lock ordering above. Any new code that needs both `pool_lock`
  and `alerts_lock` must take `pool_lock` first.
- Webhook payloads are built at *enqueue* time (snapshot capture). Never
  build payloads inside the delivery worker — retries must send identical
  bytes matching the alert state at the moment of transition.
- New alert events are enqueued only via
  `services.webhooks.enqueue_event_for_all_receivers`. This is the single
  ingress to the delivery queue.
- Strict mypy is enforced; new code must type-check cleanly.
