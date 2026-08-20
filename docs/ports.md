# Ports and Resource Guidance

Reference for what this stack publishes on the host and how much RAM it
actually uses, measured live on this machine (win32, Docker Desktop) rather
than assumed from upstream docs.

## Port map (host → container)

| Host port | Service | Notes |
|---|---|---|
| **80** | `unstract-proxy` (Traefik) | Primary entrypoint. `http://frontend.unstract.localhost` routes here — `/api/v1`, `/deployment`, `/public` all go through Traefik to the backend. **Use this, not the frontend's own port.** |
| 8080 | `unstract-proxy` | Traefik dashboard/API (not needed for the demo). |
| 3100 | `unstract-frontend` | Direct frontend container port — remapped by `docker-compose.override.yaml` because another local project uses 3000. **Debug/asset-only escape hatch**: bypasses Traefik, so login and `/deployment/api/...` calls 404 here. Do not use for the client or the API deployments. |
| 8100 | `unstract-backend` | Direct backend container port — remapped because another local project uses 8000. Not used by `client.py` (goes through Traefik on 80 instead). |
| 5433 | `unstract-db` (Postgres) | Remapped from 5432 — a local Postgres install already owns 5432 on this machine. |
| 3001 | `unstract-platform-service` | Internal platform service. |
| 5002 | `unstract-runner` | Tool runner. |
| 3004 | `unstract-x2text-service` | Text extraction service (used alongside LLMWhisperer X2Text adapter). |
| 6333 | `unstract-vector-db` (Qdrant) | Bundled vector DB — backs the `qdrant-poc` adapter; no external key needed. |
| 5672 / 15672 | `unstract-rabbitmq` | AMQP + management UI. |
| 6379 | `unstract-redis` | Cache/broker. |
| 9000-9001 | `unstract-minio` | Object storage (S3-compatible) + console. |
| 8082 / 9005 | `unstract-flipt` | Feature flags UI + API. |
| 8086-8092 (various) | `unstract-worker-*-v2` | Celery worker health/metrics ports (callback, executor, file-processing, general, log-consumer, notification, scheduler). `worker-api-deployment-v2` is remapped 8090→8185 for the same reason as frontend/backend. Not used directly by the client. |

Ports not listed (celery-beat, worker-metrics, worker-ide-callback,
worker-log-history-scheduler) are internal-only — no host port published.

Full authoritative list at any time: `docker compose -f unstract/docker/docker-compose.yaml ps`.

## RAM guidance

Upstream Unstract's README states **"8GB RAM minimum."** That number is for
a leaner default compose profile. This POC's stack — with the async
worker split, Prompt Studio, and all adapters wired — runs **~24-25
containers** simultaneously, materially more than the minimal footprint the
upstream number describes.

Measured live on this machine with `docker stats --no-stream`, platform
idle/lightly used (one extraction run completed just before the sample):

| Metric | Observed |
|---|---|
| Containers | 24 (unstract-\*, excludes unrelated host containers) |
| Total RSS across containers | **~6.6 GB** |
| Single largest consumer | `unstract-backend` (~1.1 GB) |

That is steady-state, not peak: LLM/OCR calls transiently push worker and
backend memory higher during active extraction, and Docker Desktop itself
(the Linux VM, WSL2 or Hyper-V backend) adds overhead on top of container
RSS. Budget accordingly:

- **Recommended: 12GB+ RAM allocated to Docker Desktop** (matches the
  BOOT-3 constraint used to build this POC) — not the upstream "8GB
  minimum." At 8GB you are running with near-zero headroom once Docker
  Desktop's own overhead and OS-level memory pressure are accounted for.
- Port 80 must be free before `bootstrap.ps1` runs (preflight fails fast
  otherwise, before any `docker compose up`).
