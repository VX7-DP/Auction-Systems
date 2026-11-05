# Auction-Systems

A live auction where many people bid on the same item at the same moment, and everyone watching sees the price change instantly.

**What it promises:** once a bidder is told "your bid is in", that bid is saved for good. Bids always land in the order they were made, including during a rush of bids.

The same engine fits any "many people, one hot item, live updates" problem: flash sales, ticket drops, live bidding, live dashboards.

## The evidence

Each promise is backed by a test that runs on every change, or by a measured number. Numbers come from a recorded load-test run; anything not yet measured on the live site says _TBD_.

| Promise | How it's checked | Result |
| --- | --- | --- |
| Every confirmed bid is saved | A full-stack test restarts the live-price store mid-auction and checks that no confirmed bid or high bid is lost (`backend/tests/e2e`, run in CI on every push). The load test then compares every bid confirmed to its bidders with what the database holds | Passes in CI. Load test on the live site: _TBD_ |
| Bids land in the right order | 100 bids sent at the same instant are accepted in strictly rising order (`test_concurrent_ordering.py`). After every load-test run, the saved bids are checked for order | Passes in CI. Load test on the live site: _TBD_ |
| Everyone watching sees the same price | Viewers that miss an update catch up from a fresh snapshot (`backend/tests/realtime`). In the load test, every viewer must end on the final price | Passes in CI. Load test on the live site: _TBD_ |
| It stays fast under load | The load test ([loadtest/](loadtest/README.md)) measures bid response time and the delay before viewers see a new bid | Bid response time p50 / p95 / p99 on the live site: _TBD_. Live-update delay: _TBD_ |

The full write-up for clients is in [docs/case-study.md](docs/case-study.md).

## Status

| Area | State |
| --- | --- |
| Live bidding, outbid notices, countdown, bid history, simulated bidders | Done. CI runs the whole stack end to end on every push |
| Guest sign-in, admin page to create auctions | Done |
| Deployment to `auction.summittech.in` | Production setup ready ([docs/deploy.md](docs/deploy.md)); not live yet |
| Load test, metrics endpoint (`GET /api/metrics`) | Done ([loadtest/](loadtest/README.md)) |
| Public metrics page, load-test results page, case study with live numbers | Next |

## How it works

- **A fast in-memory store decides.** Redis runs one atomic script per bid. The script checks that the auction is open, the deadline hasn't passed, the bid beats the current high and it isn't a duplicate, then gives the bid the next sequence number.
- **The database confirms.** A bid counts only after PostgreSQL has saved it. Postgres is the source of truth, and Redis can always be rebuilt from it.
- **Everyone sees it live.** Updates reach every viewer over WebSockets, fanned out through Redis Pub/Sub. Each update carries its sequence number, so a page that misses one fetches a fresh snapshot.

## Run it locally

You need Docker with Docker Compose.

```bash
docker compose up --build
```

- App: http://localhost:5173
- API docs: http://localhost:8000/api/docs
- Health: http://localhost:8000/api/health
- Metrics: http://localhost:8000/api/metrics
- The admin password is `admin` (local only).
- On Windows, if Docker says a port is "not available" or "forbidden", see the port note in [docs/development.md](docs/development.md#run-the-whole-stack).

## Load test

```bash
python3 loadtest/run.py
```

This starts the stack, runs 200 viewers and 50 bidders for a minute, checks the database, and writes a results file. Options, and what each number means, are in [loadtest/README.md](loadtest/README.md).

## Tests

```bash
docker compose up -d --wait postgres redis
cd backend && uv sync && uv run pytest
```

```bash
cd frontend && npm ci && npm test
```

The end-to-end test drives the whole stack: two bidders and the simulated bidders, through a Redis restart, to the close. CI runs it on every push; to run it locally, see [docs/development.md](docs/development.md#end-to-end-test).

Lint, migrations, and how to add code are covered in [docs/development.md](docs/development.md).

## Stack

| Part | Technology |
| --- | --- |
| API | Python 3.13, FastAPI, SQLAlchemy (async), Alembic, managed with uv |
| Front end | React + TypeScript, built with Vite |
| Hot path | Redis 7 (Lua scripts, Pub/Sub) |
| Source of truth | PostgreSQL 16 |
| Load test | k6 2.2 |
| Packaging and CI | Docker Compose, GitHub Actions |

## Repository layout

```
backend/    FastAPI app (app/), Alembic migrations (migrations/), tests (tests/)
frontend/   React + TypeScript app
deploy/     Production setup for the one-VM deploy
loadtest/   k6 load test and its runner
docs/       Developer and project docs, and the case study
```

## Docs

- [CLAUDE.md](CLAUDE.md): the project spec, with decisions, the interface contract and the build plan.
- [docs/case-study.md](docs/case-study.md): the case study for clients.
- [docs/development.md](docs/development.md): running, testing and extending the code.
- [docs/deploy.md](docs/deploy.md): deploying to one VM, with backups and restore.
- [loadtest/README.md](loadtest/README.md): running the load test and reading its results.
