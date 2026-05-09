# Deploying ProxyMaze to GCP Cloud Run via Docker Hub

This guide walks through packaging ProxyMaze as a Docker image, pushing it
to Docker Hub, and deploying it on Google Cloud Run from the GCP Console
(no `gcloud` CLI required).

---

## What you will need

- A working **Docker** install on your laptop (`docker --version`).
- A **Docker Hub** account: <https://hub.docker.com/>.
- A **Google Cloud** account with a project where billing is enabled.
- The Cloud Run API enabled in that project (the Console will offer to
  enable it on first deploy).

> **Cost note.** With min-instances=1 and CPU always-allocated, expect
> roughly **US$5–15 per month** for a small instance in `asia-south1`.
> This is necessary because ProxyMaze must run its background monitor
> continuously, even when no HTTP requests are coming in.

---

## Step 1 — Build the image locally

From the project root (`ProxyMaze/`):

```bash
# Replace YOUR_DOCKERHUB_USERNAME with your real handle.
export IMAGE=YOUR_DOCKERHUB_USERNAME/proxymaze:latest

docker build -t $IMAGE .
```

You should see two stages run (`builder`, then `runtime`) and finally:

```
=> => exporting to image
=> => naming to docker.io/YOUR_DOCKERHUB_USERNAME/proxymaze:latest
```

## Step 2 — Smoke-test the image locally

```bash
docker run --rm -p 8080:8080 $IMAGE
```

In another terminal:

```bash
curl http://localhost:8080/health
# {"status":"ok"}

curl -X POST http://localhost:8080/config \
  -H 'Content-Type: application/json' \
  -d '{"check_interval_seconds":3,"request_timeout_ms":1500}'

curl -X POST http://localhost:8080/proxies \
  -H 'Content-Type: application/json' \
  -d '{"proxies":["https://httpbin.org/status/500/px-bad","https://httpbin.org/status/200/px-good"],"replace":true}'

sleep 5
curl http://localhost:8080/proxies
curl http://localhost:8080/alerts
```

Stop the container with `Ctrl+C`. If everything looks right, move on.

## Step 3 — Push to Docker Hub

```bash
docker login                   # follow prompts
docker push $IMAGE
```

Verify on the web: open <https://hub.docker.com/r/YOUR_DOCKERHUB_USERNAME/proxymaze>
— the `latest` tag should be there.

> **Tip.** If your Cloud Run deployment fails to pull the image with a
> 401/403, your Docker Hub repository is private. Either make it public
> (Settings → Visibility → Public) or configure Cloud Run with a
> Docker-Hub-credentials Secret.

## Step 4 — Deploy on Cloud Run from the GCP Console

1. Open <https://console.cloud.google.com/run>.
2. Pick your project from the top picker.
3. Click **Deploy container → Service**.
4. **Container image URL**: choose **Docker Hub** as the source and paste

   ```text
   docker.io/YOUR_DOCKERHUB_USERNAME/proxymaze:latest
   ```

5. **Service name**: `proxymaze` (or anything you like).
6. **Region**: `asia-south1` (Mumbai — closest to Sri Lanka). Other
   regions work; this one is just lowest-latency for Colombo.
7. **Authentication**: select **Allow unauthenticated invocations**
   (the challenge is evaluated as a black box over public HTTP).
8. Expand **Container, Networking, Security**:
   - **Container port**: `8080`
   - **Resource → Memory**: `512 MiB` (1 GiB is also fine)
   - **Resource → CPU**: `1`
   - **CPU allocation**: select **CPU is always allocated** — required
     so the asyncio monitor task can run between requests.
9. Expand **Revision autoscaling**:
   - **Minimum number of instances**: `1` — required to keep the
     in-memory state and the background monitor alive.
   - **Maximum number of instances**: `1` — ProxyMaze keeps state in
     memory, so we must not scale out.
10. Click **Create**. The first deploy takes ~1–2 minutes.

## Step 5 — Smoke-test the live service

The Cloud Run console shows a URL like
`https://proxymaze-abcdef-as.a.run.app`. Hit it with:

```bash
export URL=https://proxymaze-abcdef-as.a.run.app

curl $URL/health
# {"status":"ok"}

curl -X POST $URL/config \
  -H 'Content-Type: application/json' \
  -d '{"check_interval_seconds":5,"request_timeout_ms":3000}'

curl -X POST $URL/proxies \
  -H 'Content-Type: application/json' \
  -d '{"proxies":["https://httpbin.org/status/200/px-1","https://httpbin.org/status/500/px-2"],"replace":true}'

sleep 8
curl $URL/proxies
curl $URL/alerts
```

You should see one active alert (50% failure → over the 20% line).

## Step 6 — Wire up Slack / Discord (bonus)

Generate a webhook URL in Slack or Discord, then:

```bash
curl -X POST $URL/integrations \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "slack",
    "webhook_url": "https://hooks.slack.com/services/XXX/YYY/ZZZ",
    "username": "ProxyMaze",
    "events": ["alert.fired", "alert.resolved"]
  }'
```

Trigger a state change and watch the channel light up.

## Updating the deployment

To ship a new version:

```bash
docker build -t $IMAGE .
docker push $IMAGE
```

Then in the GCP Console: Cloud Run → click the service → **Edit & Deploy
New Revision** → keep all the previous settings → **Deploy**.

(If you want zero-friction redeploys, ask the Console to enable
**continuous deployment from a registry**; this guide intentionally avoids
that to keep the path simple and Console-only.)

## Troubleshooting

- **Cold start delays**. With min-instances=1 there should be none. If you
  see them, double-check that "CPU always allocated" is on and minimum
  instances really is 1.
- **Background monitor not probing**. Check Cloud Run **Logs** — the
  monitor logs `monitor: starting` on boot. If you only see request logs,
  CPU allocation is probably set to "during request processing".
- **Image pull fails**. Make the Docker Hub repo public, or configure a
  Secret with Docker Hub credentials and reference it from the Cloud Run
  service.
- **Webhook receivers never get called**. Cloud Run egress is allowed by
  default, but corporate VPC connectors can break it. If you added a VPC
  connector, set **Egress** to "All traffic to the VPC" only when you
  actually need it; otherwise leave it as the default.
- **Alerts disappear on redeploy**. Expected — state is in-memory and a
  new revision is a new container. The challenge spec does not require
  cross-deploy persistence; if you need it, swap the storage layer for
  Redis/Memorystore (out of scope here).

## Acceptance check

Run all 10 steps from the README's "Test plan" against the live URL.
Verify:

- [ ] `GET /health` returns 200 with `{"status":"ok"}`
- [ ] `POST /config` updates apply within one round
- [ ] `POST /proxies` with mix of 200/500 endpoints fires an alert within
      one check interval
- [ ] `GET /alerts` shows the alert with `failed_proxy_ids` matching the
      down set in `GET /proxies`
- [ ] Replacing the pool with all-up endpoints flips the alert to
      `resolved` with a `resolved_at` timestamp
- [ ] Replacing again with bad endpoints mints a brand-new `alert_id`
- [ ] A registered webhook receives `alert.fired` then `alert.resolved`
      then `alert.fired` (new id), each exactly once
- [ ] Slack integration payload includes `username`, `text`,
      `attachments[0].color` (hex), all six required field titles, integer
      `ts`
- [ ] Discord integration payload includes `embeds[0].title/description`,
      integer `color`, all five required field names, `footer.text`
- [ ] `GET /metrics` increments `total_checks` and `webhook_deliveries`

You're done.
