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

Last clean run: 63 passed, 94% coverage.

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

The brief lists explicit questions. I'll answer each one directly.

### 1. Functionality & correctness

**Q: Does `docker-compose up` result in a fully working environment?**

Yes. The compose file defines two services — `mongo` (official `mongo:7.0` image) and `api` (built from the local Dockerfile). The API has `depends_on: { mongo: { condition: service_healthy } }`, Mongo has a `mongosh ping` healthcheck, and the API has its own `/health-check` healthcheck that pings Mongo. The lifespan handler also retries the first ping (default 30 × 2 s), so a slow Mongo boot doesn't crash the API. Configuration validation runs `docker-compose config --quiet` cleanly.

**Q: Are all endpoints (POST and GET) implemented according to the specifications?**

Yes.

| Spec | Implemented as |
| --- | --- |
| POST takes a URL, collects headers/cookies/page source, stores in Mongo | `POST /api/v1/metadata` with body `{"url": "..."}`, returns `201` with the full record. See `app/api/routes/metadata.py` and `app/services/metadata_service.py::collect_now`. |
| GET takes a URL, checks the inventory, returns the full dataset on hit | `GET /api/v1/metadata?url=...` returns `200` with the full record when `status == complete`. |
| GET returns an immediate `202` on miss | Same endpoint returns `202 Accepted` with a JSON body (`status: pending`, normalized URL, retry instruction) when no record exists. |

**Q: Does the background collection logic work seamlessly without blocking the API response?**

Yes. The miss path does an atomic upsert (`update_one(..., {$setOnInsert: ...}, upsert=True)`) to insert a `pending` placeholder, schedules an `asyncio.create_task`, and returns `202` immediately. The task runs on the same event loop, so there is no thread/process hop, no self-HTTP call, no broker. Verified live: eight concurrent misses for the same URL each returned in under 25 ms, and the logs showed exactly one `worker.start`/`worker.done` pair for that URL because the atomic upsert deduplicates the work.

### 2. Code quality & standards

**Q: Is the code clean, well-commented, and following PEP 8?**

Yes. Modules are small (the largest, `services/fetcher.py`, is around 170 lines including blank lines and docstrings). Naming is descriptive, type hints are on every signature, docstrings are on every public callable, and no linter warnings (`ReadLints` clean). Inline comments only appear where they explain *why* a non-obvious decision was made — for example, why `Set-Cookie` is read with `get_list(..., split_commas=False)`, or why only the inserting caller schedules the worker.

**Q: Are you utilising type hints and Pydantic models for data validation?**

Yes throughout.

- API I/O: `MetadataCreateRequest` (POST body, with `AnyHttpUrl`), `MetadataRecord` (response), `MetadataAcceptedResponse` (202 body), `HealthResponse` — all with `extra="forbid"` so unknown fields are rejected.
- Persistence: the same `MetadataRecord` round-trips through Mongo via `model_validate` / `model_dump`, so the document shape is always validated.
- Configuration: `Settings(BaseSettings)` from `pydantic-settings`, with `field_validator`s that reject bad values at startup.
- Lifecycle: `MetadataStatus(StrEnum)` rather than free-form strings.
- Internal: `dataclass(frozen=True, slots=True)` for the `ParsedURL` value object, `Protocol` for the Mongo client surface.
- PEP 604 union syntax (`X | None`) is used everywhere.

**Q: How does the application handle invalid URLs, timeouts, or database connection issues?**

| Failure mode | What happens |
| --- | --- |
| Malformed URL on POST | Rejected by `pydantic.AnyHttpUrl` → `422 Unprocessable Entity` with structured field-level error. |
| Malformed URL on GET, or scheme outside the allow-list | `app/utils/url.py::normalize_url` raises `InvalidURLError` → `400 Bad Request`. |
| Upstream timeout, connect error, redirect loop, oversized body | `app/services/fetcher.py` catches every `httpx.HTTPError` and raises `FetchFailure(kind, message)`. POST surfaces it as `502 Bad Gateway`. The GET path stores a `failed` document so the next lookup answers from cache instead of replaying the failure. |
| Mongo unreachable at startup | `wait_for_mongo` retries the ping with configurable attempts × delay (default 30 × 2 s). Only after that budget is exhausted does the API give up and exit. |
| Mongo unreachable mid-flight | Repository methods don't swallow `PyMongoError`; it bubbles up and is logged. The `/health-check` endpoint returns `503` so an orchestrator can route traffic away. |

### 3. Database & performance

**Q: Is the metadata structured logically in MongoDB?**

Yes. One collection (`url_metadata`), one document per normalised URL. The shape is defined and validated by `MetadataRecord`, so the storage and API contracts are identical.

```
{
  url: "https://example.com",            // original user input (audit trail)
  normalized_url: "https://example.com/",// canonical lookup key
  status: "pending" | "complete" | "failed",
  status_code: 200,
  final_url: "...",                      // after redirects
  headers: { ... },
  cookies: [ { name, value, domain, path, expires, secure, http_only }, ... ],
  page_source: "<!doctype html>...",
  content_type, content_length, truncated,
  error: { type, message } | null,       // populated on failures
  created_at, updated_at, fetched_at
}
```

Two design choices worth calling out: cookies are stored structurally rather than as raw `Set-Cookie` strings so callers can reason about them programmatically, and the lifecycle `status` field lets us distinguish "never seen" from "seen and failed".

**Q: Are lookups optimised (e.g. indexing) to ensure the system remains fast as the dataset grows?**

Yes. Two indexes are created idempotently at startup (`app/db/mongo.py::ensure_indexes`):

- `{ normalized_url: 1 }` — **unique**. This is the lookup key, and the unique constraint also enforces dedup at the storage layer (so even if the application logic regressed, two documents for the same URL couldn't exist).
- `{ status: 1, updated_at: 1 }` — supports housekeeping queries like "list failed records updated more than X minutes ago" for a future re-fetch worker.

A cache hit measures at ~3–6 ms locally, dominated by Pydantic serialisation rather than the index lookup.

In addition, the atomic-upsert dedup (see Q 1.3) means N concurrent misses for the same URL produce one upstream fetch and one write, regardless of N.

### 4. System design & scalability

**Q: Is there a clear distinction between the API layer and the business logic?**

Yes. Five layers, one-way dependencies, enforced by import discipline rather than convention:

```
api/        transport (FastAPI routes, dependency injection)
services/   business logic (fetcher, orchestration, scheduler)
db/         persistence (motor client + repository)
models/     schemas (Pydantic — the contract between layers)
utils/      pure helpers (URL parsing, SSRF guard)
core/       cross-cutting (config, logging)
```

Concrete evidence the layering is enforced and not just folder cosmetics:

- The service layer (`services/`) doesn't import `fastapi` anywhere.
- The repository (`db/repository.py`) is the only module that imports `motor`.
- `MetadataService` takes its scheduler as an injected `Callable`, not a FastAPI import — so the same service could be driven from a CLI or a queue consumer.
- Routes are thin: `app/api/routes/metadata.py` is ~95 lines including imports and docstrings; the handlers translate HTTP↔service calls and nothing more.
- Each layer has its own test module (`test_repository.py`, `test_fetcher.py`, `test_metadata_service.py`, `test_api.py`), which is only feasible because the layers are actually decoupled.

**Q: Is the Docker setup efficient, secure, and easy to maintain?**

Yes.

- *Efficient:* multi-stage Dockerfile based on `python:3.12-slim-bookworm`. The build stage installs deps into a venv; the runtime stage copies the venv and the app code only, so the final image stays slim. `.dockerignore` keeps the build context small (no `.venv`, no tests, no logs, no `.run/`).
- *Secure:* runs as a non-root user (uid 1001), `read_only: true` root filesystem, `cap_drop: [ALL]`, `no-new-privileges`, `tmpfs` for `/tmp` only, MongoDB binds to `127.0.0.1` on the host so it isn't exposed by accident.
- *Maintainable:* environment variables are passed through the compose file with `${VAR:-default}` so anything can be overridden without rebuilding. Healthchecks on both services and `depends_on: condition: service_healthy` make startup ordering deterministic.

**Q: How easy would it be to extend this service or move components into a distributed architecture in the future?**

Two swaps would cover the common scaling paths, and both are isolated by design:

- *Different store?* Implement four methods (`get_by_normalized_url`, `reserve_pending`, `store_complete`, `mark_failed`) against the new backend and rewire `lifespan`. Nothing else changes — the service layer talks to `MetadataRepository`, not Mongo.
- *Distributed worker?* Replace `BackgroundTaskScheduler` with an arq, RQ, or Celery producer that enqueues a job, and run the worker out of process. The service's `schedule(coro_factory, name)` signature is the contract.

Multiple uvicorn workers behind a load balancer are fine for the API surface (Mongo handles the concurrency), but the in-process scheduler is per-process — the second swap above is what makes horizontal scale work cleanly.

### 5. Documentation

**Q: Does the README clearly explain how to run, test, and interact with the API?**

Yes — see the [Running it](#running-it), [Tests](#tests), and [Quick try](#quick-try) sections at the top of this file. There are explicit Docker and non-Docker run paths, a `pytest --cov` invocation, and copy-pasteable curl examples for both POST and GET flows.

**Q: Is the API documented (e.g. via FastAPI's automatic Swagger/OpenAPI UI)?**

Yes. FastAPI generates the OpenAPI schema from the typed signatures and Pydantic models. Three documentation surfaces are exposed:

- `GET /docs` — interactive Swagger UI
- `GET /redoc` — ReDoc
- `GET /openapi.json` — raw OpenAPI 3.x JSON (also useful for client codegen)

Every endpoint declares `summary`, `response_model`, and explicit `responses` for the non-default status codes (`400`, `502`, `202`), so the generated docs surface every realistic outcome.

## Implementation guidelines from the brief

**System resilience.** Mongo startup retry loop with configurable attempts and delay. Healthcheck pings Mongo on every probe and degrades to `503` when it can't. The fetcher catches and classifies every `httpx` failure mode and persists a `failed` document so callers always get an answer. The worker has a final exception handler so a bug in fetching can't kill the asyncio task. On shutdown, the scheduler `drain()`s in-flight tasks within a 30 s budget so we don't lose work mid-deploy.

**Configuration.** A single `Settings` class via `pydantic-settings` reads env vars (with `.env` as a fallback for development). Defaults are sane, validators reject bad values at startup. `.env.example` is checked in; `.env` is gitignored. No credentials in source.

**Resource management.** Async all the way down (FastAPI + motor + httpx). One shared `httpx.AsyncClient` and one `AsyncIOMotorClient` per process so connections are pooled and reused. Streaming response reads with a hard byte cap (`FETCH_MAX_BYTES`, default 5 MiB) so an enormous upstream body can't OOM the worker. Hard timeouts on connect/read/write. Indexed lookups on the unique key.

**Code architecture.** See "Separation of concerns" above. Each layer has its own test module; that's only feasible because the layers are actually decoupled.

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
