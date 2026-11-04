# Development

CLAUDE.md is the source of truth for decisions and the interface contract (section 10). This page covers running, testing, and where code goes.

## Run the whole stack

```bash
docker compose up --build
```

- App: http://localhost:5173. Vite proxies `/api` and `/ws` to the backend, so the browser sees one origin.
- API docs: http://localhost:8000/api/docs
- The admin password is `admin` (local only).
- Redis runs without persistence, so `docker compose restart redis` is a real Redis restart. Postgres keeps its data in the `pgdata` volume until `docker compose down --volumes`.
- If a host port is taken, change it with `POSTGRES_PORT`, `REDIS_PORT`, `BACKEND_PORT` or `FRONTEND_PORT`.
- **Windows: "ports are not available … forbidden by its access permissions".** Windows won't let Docker bind that port, because a local PostgreSQL already uses it or the port falls in a range Windows reserves for Hyper-V/WSL. Move the host ports; the containers still talk to each other on the usual ports inside Docker. In PowerShell:

  ```powershell
  $env:POSTGRES_PORT=15432; $env:REDIS_PORT=16379; docker compose up --build
  ```

  To keep them, put `POSTGRES_PORT=15432` and `REDIS_PORT=16379` in a `.env` file in the repo root (git ignores it). To see which cause it was: `netstat -ano | findstr :5432` shows a process using the port, and `netsh int ipv4 show excludedportrange protocol=tcp` lists the reserved ranges. With moved ports, point the backend tests at them too: `TEST_DATABASE_URL=postgresql://auction:auction@localhost:15432/auction_test` and `TEST_REDIS_URL=redis://localhost:16379/15` (and `E2E_DATABASE_URL` for the end-to-end test).

## Backend (`backend/`)

```bash
cd backend
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

The tests need Postgres and Redis on localhost. One way is `docker compose up -d --wait postgres redis` (`--wait` holds until both are ready; without it the first test run can fail to connect). They use the database `auction_test` (created automatically) and Redis database 15. To point them elsewhere, set `TEST_DATABASE_URL` and `TEST_REDIS_URL`. The tests drop and rebuild the test database's schema, so its name must end in `_test`; any other name is refused.

The bid engine reserves seqs in Postgres in blocks before Redis hands them out (CLAUDE.md section 4). `BIDDING_SEQ_BLOCK` sets the block size, default 1000; it is also the jump in `seq` after Redis loses an auction. The other `BIDDING_*` settings are in `app/bidding/settings.py`.

To run the API on the host, set `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET`, `ADMIN_PASSWORD` and `COOKIE_SECURE=false`, either in the environment or in `backend/.env`. Then run:

```bash
uv run uvicorn app.main:create_app --factory --reload
```

To add a migration, change `app/models.py`, then run the following with `DATABASE_URL` set. Review the generated file before committing. The tests fail if models and migrations drift apart.

```bash
uv run alembic revision --autogenerate -m "what changed"
```

### Where code goes

- `app/contract.py` holds the section 10 shapes and Redis key names. Change section 10 first, then this file.
- Feature modules are `bidding`, `admin`, `auctions`, `realtime`, `demo` and `metrics`.
  - Each exports a `router`, which declares full paths.
  - A module can also export `lifespan(app)`, an async context manager that runs for the app's lifetime.
  - `main.py` picks both up, so don't edit `main.py`.
- Shared pools: use `get_db_session` and `get_redis` as FastAPI dependencies, and `app.resources.get_resources()` in plain functions such as `place_bid`.
- Background jobs start in a module's `lifespan`, only when `settings.run_background_jobs` is true. They hold the Redis lock `job_lock_key(name)` through the shared `app/job_lock.py`: `run_while_holding` runs a long-lived job only while the lock is held (the simulated bidders), and `hold()` takes or renews it before each pass (the close sweeper).
- Settings that only one module needs go in that module's own `pydantic_settings.BaseSettings` subclass, not in `app/config.py`.
- Tests for a module go in `tests/<module>/`.
  - The shared fixtures are in `tests/conftest.py`: `app`, `client`, `client_factory`, `bidder_client`, `admin_headers`, `db_session`, `redis` and `make_settings`.
  - Background jobs are off in tests. To turn them on for one test module, override the `settings` fixture there.

### End-to-end test

`tests/e2e/` checks the Stage 1 done-signal against a running stack. Two guests, each with the auction page open over a WebSocket, bid alongside the simulated bidders. The test restarts Redis mid-auction, then waits for the close. CI runs it in the `compose` job. Locally:

```bash
docker compose up -d --build --wait
cd backend && E2E_BASE_URL=http://localhost:5173 uv run pytest tests/e2e -v
```

- It takes about a minute, because the auction has to reach its end time.
- Without `E2E_BASE_URL` it is skipped, so plain `uv run pytest` doesn't need the stack.
- It reads the stack's Postgres to check what was saved (`E2E_DATABASE_URL`, default the Compose one) and restarts Redis with `docker compose restart redis` (override with `E2E_RESTART_REDIS`). Point it only at a local or CI stack.

## Front end (`frontend/`)

```bash
cd frontend
npm ci
npm run dev     # proxies /api and /ws to http://localhost:8000 (override with API_PROXY_TARGET)
npm run lint    # Oxlint
npm test        # Vitest
npm run build   # type-check, then build into dist/
```

## Cloud sessions

PostgreSQL 16 and Redis 7 are installed but stopped. Running the backend tests against them avoids pulling images:

```bash
service postgresql start && service redis-server start
runuser -u postgres -- psql -c "CREATE ROLE auction LOGIN SUPERUSER PASSWORD 'auction'"
```

The Docker daemon isn't running at first. Start it with `dockerd > /tmp/dockerd.log 2>&1 &`. Two limits of the sandbox network affect `docker compose up --build`:

- **Docker Hub can rate-limit anonymous pulls (HTTP 429).** Pull the base images from Google's mirror and tag them under the normal names. For example: `docker pull mirror.gcr.io/library/python:3.13-slim`, then `docker tag mirror.gcr.io/library/python:3.13-slim python:3.13-slim`.
- **Build steps don't trust the sandbox's TLS proxy certificate.** pip and npm fail with "self-signed certificate in certificate chain". The fix is to rebuild the base images locally with `/root/.ccr/ca-bundle.crt` installed, keeping the same tags.
  - Python: set `SSL_CERT_FILE` and `PIP_CERT`.
  - Node: set `NODE_EXTRA_CA_CERTS`.
  - Never commit this. It only changes local images.
