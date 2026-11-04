# Auction-Systems

A real-time auction system where many bidders race to raise one auction's high bid. **The guarantee: no confirmed bid is lost, and bids are never ordered wrongly, even during write spikes.**

The same engine fits any "many people, one hot item, live updates" problem: flash sales, ticket drops, live bidding, live dashboards.

## How it works

- **Redis decides.** An atomic Lua compare-and-set script in Redis accepts or rejects each bid. It checks that the auction is open, the deadline hasn't passed, the bid beats the current high, and the bid isn't a duplicate. It then gives the bid the next sequence number.
- **Postgres confirms.** A bid counts as confirmed only after PostgreSQL has committed it. Postgres is the source of truth, and Redis can always be rebuilt from it.
- **Everyone sees it live.** Updates reach every viewer over WebSockets, fanned out through Redis Pub/Sub. Each event carries its sequence number, so a client that misses one refetches a snapshot.

## Stack

| Part | Technology |
| --- | --- |
| API | Python 3.13, FastAPI, SQLAlchemy (async), Alembic, managed with uv |
| Front end | React + TypeScript, built with Vite |
| Hot path | Redis 7 (Lua scripts, Pub/Sub) |
| Source of truth | PostgreSQL 16 |
| Packaging and CI | Docker Compose, GitHub Actions |

## Status

| Area | State |
| --- | --- |
| Project scaffold, health check, guest sign-in, admin check | Done |
| Bid engine, real-time updates, demo bidders, front end | Done. CI runs an end-to-end test on the full stack: two bidders and the simulated bidders, a Redis restart mid-auction, then the close |
| Deployment to `auction.summittech.in` | Production setup ready ([docs/deploy.md](docs/deploy.md)); not live yet |
| Load tests, public metrics, case study | Planned |

## Run it locally

You need Docker with Docker Compose.

```bash
docker compose up --build
```

- App: http://localhost:5173
- API docs: http://localhost:8000/api/docs
- Health: http://localhost:8000/api/health
- The admin password is `admin` (local only).

## Tests

```bash
docker compose up -d --wait postgres redis
cd backend && uv sync && uv run pytest
```

```bash
cd frontend && npm ci && npm test
```

Lint, migrations, and how to add code are covered in [docs/development.md](docs/development.md).

## Repository layout

```
backend/    FastAPI app (app/), Alembic migrations (migrations/), tests (tests/)
frontend/   React + TypeScript app
deploy/     Production setup (in progress)
loadtest/   Load tests (planned)
docs/       Developer and project docs
```

## Docs

- [CLAUDE.md](CLAUDE.md): the project spec, with decisions, the interface contract and the build plan.
- [docs/development.md](docs/development.md): running, testing and extending the code.
