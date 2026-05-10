# HTTP Metadata Inventory

A small FastAPI service that fetches a URL's response metadata (headers, cookies, page source) and remembers it in MongoDB so repeat lookups are free.

Submitted for the SDE Backend Engineering hiring challenge.

## Endpoints

| Method | Path | What it does |
| --- | --- | --- |
| `POST` | `/api/v1/metadata` | Fetches the URL synchronously, stores everything, returns the record. |
| `GET`  | `/api/v1/metadata?url=...` | Returns the stored record on a cache hit. On a miss it returns `202 Accepted` and starts a background fetch; the next call gets the full record. |
| `GET`  | `/health-check` | Liveness/readiness; also pings Mongo. |
| `GET`  | `/docs`, `/redoc`, `/openapi.json` | Auto-generated API documentation from FastAPI. |

## Running it

### With Docker (the way the brief asks for)

You need Docker and Compose v2.

```bash
cp .env.example .env       # optional
docker compose up --build
```

That brings up MongoDB and the API. Visit `http://localhost:8000/docs` once the API container reports healthy.

To wipe and restart:

```bash
docker compose down --volumes
```

### Without Docker

If Docker isn't handy, install MongoDB locally (Homebrew works) and use the helper scripts:

```bash
./scripts/run-local.sh    # starts mongod + uvicorn
./scripts/stop-local.sh
```

### Tests

The suite is hermetic. `mongomock-motor` stubs Mongo, `respx` stubs HTTP, so no daemons or network are needed.

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest --cov
```

Last clean run: 64 passed, 94% coverage.

## Quick try

```bash
# POST: synchronous fetch + store
curl -X POST http://localhost:8000/api/v1/metadata \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com"}'

# First GET on a fresh URL → 202 Accepted
curl -i 'http://localhost:8000/api/v1/metadata?url=https://github.com'

# A few seconds later → 200 with the full record
curl -i 'http://localhost:8000/api/v1/metadata?url=https://github.com'
```

A complete `MetadataRecord` looks like this:

```jsonc
{
  "url": "https://example.com",
  "normalized_url": "https://example.com/",
  "status": "complete",
  "status_code": 200,
  "final_url": "https://example.com/",
  "headers": { "content-type": "text/html; charset=UTF-8" },
  "cookies": [
    { "name": "session", "value": "...", "secure": true, "http_only": true }
  ],
  "page_source": "<!doctype html>...",
  "content_type": "text/html; charset=UTF-8",
  "content_length": 1256,
  "truncated": false,
  "error": null,
  "created_at": "...",
  "updated_at": "...",
  "fetched_at": "..."
}
```

`status` is `pending`, `complete`, or `failed`. The `error` field is populated when an upstream fetch fails so callers can see why. Failed records still answer with `200`; the inventory request itself succeeded, the upstream just didn't.

## How the rubric was addressed

I'll go through the rubric in the same order as the brief.

### 1. Functionality & correctness

**`docker compose up` is enough.** Compose brings up Mongo with a healthcheck, the API depends on `service_healthy` before it starts, and the API has its own healthcheck pinging Mongo. `docker-compose config --quiet` exits 0; I smoke-tested the running stack with curl end to end.

**Both endpoints follow the spec.** POST is the synchronous create (`201 Created`). GET returns `200` on a hit and `202 Accepted` on a miss with a JSON body explaining what happened. Pydantic models on both sides keep the contract honest.

**The background work doesn't block.** GET on a miss does an atomic upsert to insert a `pending` placeholder, schedules an `asyncio.create_task`, and returns `202` immediately. The task runs on the same event loop, so there's no thread/process hop, no self-HTTP call, and no message broker. I verified this in the local run: eight concurrent misses for the same URL all returned in under 25 ms each, and the logs showed exactly one `worker.start`/`worker.done` pair for that URL because the upsert deduplicated the work.

### 2. Code quality

**Readability and PEP 8.** Modules are small, names carry weight, type hints are everywhere, and docstrings are on public callables. I tried not to over-comment; the inline comments that remain explain *why*, not *what*.

**Modern Python.** Pydantic v2 for both API I/O and persisted-record validation. `pydantic-settings` for configuration. `StrEnum` for the lifecycle field. `dataclass(frozen=True, slots=True)` for the parsed-URL value object. PEP 604 union syntax (`X | None`) throughout.

**Error handling.** Three categories, three places:

- *Invalid URLs* are rejected by `pydantic.AnyHttpUrl` on POST (`422`) and by an explicit normaliser on GET (`400`). The normaliser also enforces the scheme allow-list.
- *Upstream timeouts, connect errors, and redirect loops* are caught in the fetcher and normalised into a `FetchFailure(kind, message)`. POST surfaces it as `502 Bad Gateway`; GET stores a `failed` document so the next lookup gets a useful answer instead of replaying the failure.
- *Mongo issues* are caught at startup by `wait_for_mongo`, which retries the initial ping with a configurable count and delay. The healthcheck reports `503` if Mongo disappears later. Repository methods don't swallow `PyMongoError`; it bubbles up and is logged.

### 3. Database & performance

**Schema.** One collection (`url_metadata`), one document per normalised URL. Lifecycle is tracked by a `status` field (`pending` / `complete` / `failed`), so we can tell "never seen" from "seen and failed". Cookies are stored structurally rather than as raw `Set-Cookie` text so callers can reason about them programmatically. `created_at`, `updated_at`, `fetched_at` give cheap auditability.

**Indexes** are created at startup and are idempotent:

- `{normalized_url: 1}` unique — the lookup key, also enforces dedup at the storage layer.
- `{status: 1, updated_at: 1}` — supports housekeeping queries like "show me failed records older than X".

A cached GET round-trips at ~6 ms in the local run, dominated by Pydantic serialisation rather than the index lookup itself.

**Concurrency.** `MetadataRepository.reserve_pending` uses `update_one(..., {$setOnInsert: ...}, upsert=True)` and reports whether *this* caller created the placeholder. Only that caller schedules the worker, so a thundering herd of GETs for a missing URL still results in exactly one upstream fetch.

### 4. System design & scalability

**Separation of concerns.** Five layers, one-way dependencies:

```
api/        transport (FastAPI routes, dependency injection)
services/   business logic (fetcher, orchestration, scheduler)
db/         persistence (motor client + repository)
models/     schemas (Pydantic - the contract between layers)
utils/      pure helpers (URL parsing, SSRF guard)
core/       cross-cutting (config, logging)
```

The service layer doesn't import FastAPI. The repository is the only module that imports `motor`. The scheduler is injected into the service as a callable, so the same `MetadataService` could be driven from a CLI or a queue worker without changes. Routes are thin wrappers — the metadata route file is around 85 lines including imports.

**Containerisation.** Multi-stage Dockerfile based on `python:3.12-slim-bookworm`. The build stage installs deps into a venv; the runtime stage copies the venv and the app code only. The runtime container runs as a non-root user (uid 1001), with a read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, and a `tmpfs` for `/tmp`. MongoDB binds to `127.0.0.1` on the host so it isn't exposed by accident. `.dockerignore` keeps the build context lean.

**Extensibility** mostly reduces to two questions:

- *Different store?* Implement the four repository methods (`get_by_normalized_url`, `reserve_pending`, `store_complete`, `mark_failed`) against the new backend and rewire `lifespan`. Nothing else changes.
- *Distributed worker?* Replace `BackgroundTaskScheduler` with an arq, RQ, or Celery producer that enqueues a job, and run the worker out of process. The service's `schedule(coro_factory, name)` signature is the contract.

Multiple uvicorn workers behind a load balancer are fine for the API surface (Mongo handles the concurrency), but the in-process scheduler is per-process, which is why the second swap above matters for real horizontal scale.

### 5. Documentation

This README covers running, testing, and exercising the API. The interactive Swagger UI lives at `/docs`, ReDoc at `/redoc`; the OpenAPI JSON at `/openapi.json` is what FastAPI generates from the typed signatures. Each module has a one-line docstring describing its role.

## Implementation guidelines from the brief

**System resilience.** Mongo startup retry loop with configurable attempts and delay. Healthcheck pings Mongo on every probe and degrades to `503` when it can't. The fetcher catches and classifies every `httpx` failure mode and persists a `failed` document so callers always get an answer. The worker has a final exception handler so a bug in fetching can't kill the asyncio task. On shutdown, the scheduler `drain()`s in-flight tasks within a 30 s budget so we don't lose work mid-deploy.

**Configuration.** A single `Settings` class via `pydantic-settings` reads env vars (with `.env` as a fallback for development). Defaults are sane, validators reject bad values at startup. `.env.example` is checked in; `.env` is gitignored. No credentials in source.

**Resource management.** Async all the way down (FastAPI + motor + httpx). One shared `httpx.AsyncClient` and one `AsyncIOMotorClient` per process so connections are pooled and reused. Streaming response reads with a hard byte cap (`FETCH_MAX_BYTES`, default 5 MiB) so an enormous upstream body can't OOM the worker. Hard timeouts on connect/read/write. Indexed lookups on the unique key.

**Code architecture.** See "Separation of concerns" above. Each layer has its own test module (`test_repository.py`, `test_fetcher.py`, `test_metadata_service.py`, `test_api.py`); that's only feasible because the layers are actually decoupled.

**Scope.** Static content only. We use `httpx`; there is no headless browser and no JavaScript evaluation. The fetcher does follow redirects (capped at `FETCH_MAX_REDIRECTS`) and decodes the body using the encoding declared by the upstream.

## Configuration reference

| Variable | Default | What it does |
| --- | --- | --- |
| `APP_ENV` | `development` | Tag included in logs. |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL. |
| `API_HOST`, `API_PORT` | `0.0.0.0`, `8000` | Bind address. |
| `API_PREFIX` | `/api/v1` | Route prefix. |
| `MONGO_URI` | `mongodb://localhost:27017` | Connection string. |
| `MONGO_DB` | `metadata_inventory` | Database name. |
| `MONGO_COLLECTION` | `url_metadata` | Collection name. |
| `MONGO_STARTUP_RETRY_ATTEMPTS` | `30` | Pings to try at boot. |
| `MONGO_STARTUP_RETRY_DELAY_S` | `2.0` | Seconds between attempts. |
| `FETCH_TIMEOUT_S` | `15.0` | Per-request timeout. |
| `FETCH_MAX_REDIRECTS` | `5` | Redirect cap. |
| `FETCH_MAX_BYTES` | `5242880` | Body byte cap. |
| `FETCH_USER_AGENT` | (default UA) | UA sent on every fetch. |
| `BLOCK_PRIVATE_NETWORKS` | `true` | Refuse private/loopback IPs (SSRF guard). Off in `docker-compose.yml` so reviewers can hit any URL; turn it on for real deployments. |
| `ALLOWED_SCHEMES` | `http,https` | Comma-separated allow-list. |

## Project layout

```
app/
  main.py                       FastAPI factory + lifespan
  api/
    dependencies.py             DI providers
    routes/
      health.py
      metadata.py
  core/
    config.py                   pydantic-settings
    logging.py                  JSON logger
  db/
    mongo.py                    client + retry + indexes
    repository.py               MetadataRepository
  models/schemas.py             Pydantic models
  services/
    fetcher.py                  httpx fetcher
    metadata_service.py         orchestration
    worker.py                   asyncio scheduler
  utils/url.py                  normalisation + SSRF guard
tests/                          pytest suite (mongomock + respx)
scripts/
  run-local.sh                  start mongod + uvicorn locally
  stop-local.sh                 stop both
Dockerfile
docker-compose.yml
pyproject.toml
requirements.txt
requirements-dev.txt
.env.example
```

## Things I'd do given more time

- **Re-fetch policy for failed records.** Today a `failed` document stays failed until someone reposts. A TTL on `failed` documents (or a periodic re-queue scan) would let transient errors self-heal.
- **External queue.** Swap the in-process scheduler for arq or Celery so we can run multiple uvicorn workers behind a load balancer. The service is already shaped for it.
- **Rate limiting at the edge.** `POST /metadata` is essentially a way to ask the service to fetch arbitrary URLs on your behalf. In production it needs a rate limit (slowapi, or at the gateway).
- **Mongo auth + TLS in compose.** I left the dev compose unauthenticated for review convenience.
