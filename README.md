# HTTP Metadata Inventory Service

A small FastAPI + MongoDB service that collects and stores HTTP metadata
(headers, cookies, page source) for any URL, with a synchronous POST
endpoint and a cache-aware GET that schedules background work on a miss.

## Tech Stack

- Python 3.11+
- FastAPI
- MongoDB (via Motor, the async driver)
- Docker Compose
- Pytest

## Running

```bash
docker compose up --build
```

The API will be available on `http://localhost:8000`.  Swagger
UI at `http://localhost:8000/docs`.

To stop:

```bash
docker compose down
```

To wipe the Mongo volume too:

```bash
docker compose down -v
```

## Endpoints

### `POST /metadata`

Synchronously fetches the URL, stores the result, and returns the full
record.

```bash
curl -X POST http://localhost:8000/metadata \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/"}'
```

Returns `201 Created` on success. On an invalid URL, returns `422`. On an
upstream failure (timeout, DNS error, etc.) returns `502` with a `kind`
and `message` in the detail.

### `GET /metadata?url=<url>`

Looks up a previously collected record.

- If a complete record exists, returns it with `200 OK`.
- If not, atomically reserves a placeholder, schedules a background fetch
  using `asyncio.create_task`, and returns `202 Accepted` with status
  `pending`. The same response is also returned for a follow-up GET that
  arrives while the fetch is still in flight.
- If a previous fetch failed, the failed record is returned with `200`
  (status `failed`, with the error detail attached).

```bash
curl "http://localhost:8000/metadata?url=https://example.com/"
```

## Stored record

```json
{
  "url": "https://example.com/",
  "normalized_url": "https://example.com/",
  "status": "complete",
  "status_code": 200,
  "final_url": "https://example.com/",
  "headers": { "content-type": "text/html; charset=UTF-8" },
  "cookies": [],
  "page_source": "<!doctype html>...",
  "content_type": "text/html; charset=UTF-8",
  "content_length": 1256,
  "truncated": false,
  "error": null,
  "created_at": "2026-05-10T11:00:00+00:00",
  "updated_at": "2026-05-10T11:00:00+00:00",
  "fetched_at": "2026-05-10T11:00:00+00:00"
}
```

While `status` is `pending`, the GET response is the smaller
`MetadataAcceptedResponse` body containing only `url`, `normalized_url`,
`status`, and a `detail` message.

## Tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

The test suite is hermetic: `mongomock-motor` substitutes for MongoDB and
`respx` substitutes for httpx, so no network or database is needed.

## Coverage Report
<img width="1726" height="777" alt="image" src="https://github.com/user-attachments/assets/53500cf3-b697-432d-9e00-311f84cd192d" />

## Configuration

The service reads four environment variables. Defaults are shown.

| Variable           | Default                       | Purpose                       |
|--------------------|-------------------------------|-------------------------------|
| `MONGO_URI`        | `mongodb://localhost:27017`   | Mongo connection string       |
| `MONGO_DB`         | `metadata_inventory`          | Database name                 |
| `MONGO_COLLECTION` | `url_metadata`                | Collection name               |
| `LOG_LEVEL`        | `INFO`                        | Root logger level             |

`docker-compose.yml` sets `MONGO_URI=mongodb://mongo:27017` so the API
container can reach the database service.

## Layout

```
app/
  api/
    routes/metadata.py      POST and GET handlers
    dependencies.py         FastAPI DI
  core/
    config.py               Settings
    logging.py              logging setup
  db/
    mongo.py                Motor client + retry + indexes
    repository.py           MetadataRepository
  models/
    schemas.py              Pydantic models
  services/
    fetcher.py              httpx-based fetcher
    metadata_service.py     Business logic
    worker.py               asyncio background scheduler
  utils/
    url.py                  URL parsing & normalisation
  main.py                   FastAPI app factory + lifespan
tests/                      pytest suite
Dockerfile
docker-compose.yml
```

## How the rubric is addressed

### 1. Functionality & correctness

- **`docker compose up`** starts MongoDB and the API. `depends_on:
  condition: service_healthy` plus a startup retry loop in the API mean
  the API container waits until Mongo is ready before serving traffic.
- **POST** is synchronous: it fetches, stores, and returns the record.
- **GET** returns the stored record on a hit, or `202` plus a scheduled
  background fetch on a miss.
- **Background work does not block the API response.** The handler
  reserves a placeholder via an atomic Mongo upsert, calls
  `asyncio.create_task` through a small scheduler, and returns
  immediately. There is no self-HTTP loop and no external broker.

### 2. Code quality & standards

- **Readability:** layers are split into `api/`, `services/`, `db/`,
  `models/`, `utils/`. Each module is short and focused.
- **Type hints + Pydantic:** every function is annotated; request,
  response.
- **Error handling:**
  - Invalid URL -> `400` (or `422` if Pydantic rejects the body shape).
  - Upstream timeout / connection error / too-many-redirects -> `502`
    on POST, persisted as a `failed` record on GET so future calls
    can see the error.
  - Mongo unavailable at startup -> the API retries with backoff before
    giving up, so a slow Mongo container does not crash the API.

### 3. Database & performance

- **Schema:** one document per normalized URL keyed on
  `normalized_url`. Status is one of `pending`, `complete`, `failed`,
  with the body, headers, cookies, and timing stored alongside.
- **Indexes:** a unique index on `normalized_url` makes lookups O(log n)
  and prevents duplicates under concurrency. A secondary index on
  `(status, updated_at)` supports housekeeping queries.
- **Concurrency:** `reserve_pending` uses `$setOnInsert` so only one
  caller observes `just_created=True`; only that caller schedules a
  worker, so simultaneous GETs for the same missing URL fan out to
  exactly one fetch.

### 4. System design & scalability

- **Separation of concerns:** the API layer only translates HTTP to
  service calls; the service layer owns orchestration; the repository
  hides Mongo details. Swapping the persistence layer or the fetcher is
  a one-file change.
- **Containerisation:** a small Dockerfile installs requirements and
  runs `uvicorn`. `docker-compose.yml` wires the API to MongoDB with a
  health check so startup is reliable.
- **Extensibility:** the background scheduler is a thin abstraction
  around `asyncio.create_task`. Replacing it with arq, Celery, or a
  cloud queue is a drop-in: only the `schedule(coro_factory, name)`
  contract has to be honoured.

### 5. Documentation

- This README explains how to run, test, and call the API, with example
  curls and a sample document.
- FastAPI generates Swagger UI (`/docs`) and OpenAPI JSON
  (`/openapi.json`) automatically.

## Implementation guidelines covered

- **Resilience:** Mongo startup retry loop; per-fetch timeouts; failed
  fetches are recorded so the next GET sees the error rather than
  re-triggering immediately.
- **Configuration:** all environment-specific values come from env vars,
  with sane defaults for local runs.
- **Resource management:** all I/O is async (Motor + httpx). Background
  work runs on the API event loop without spawning threads. Response
  bodies are streamed and capped at 5 MiB to prevent runaway memory
  usage.
- **Code architecture:** modular layout (`api/`, `services/`, `db/`,
  `models/`, `utils/`) with one responsibility per module.
- **Scope:** static HTTP metadata only; no JavaScript execution or
  headless browser.


